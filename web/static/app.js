"use strict";
// Jarvis web interface. Plain JS, no build step. All text goes in via textContent (logs contain transcripts).

const S = {
  view: null,
  status: null,
  settings: null,
  changes: {},     // key -> new value, or null to remove (default applies again)
  errors: {},
  fields: {},      // key -> {el, err, hint} of the rendered settings form
  optionLoaders: [], // dropdowns loaded from the server: {deps, load}, reloaded when a dep changes (see setChange)
  liveModels: null,  // live model -> supports thinking, from the GEMINI_LIVE_MODEL list
  restarting: null, // {at, started} while a restart we triggered is in progress
  setupTimer: null,
  wsDirty: new Set(), // workspace editors with unsaved text
  toolsDirty: false,
  pollTimer: null,
  logTimer: null,
};

// --- helpers --------------------------------------------------------------------------

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") el.style.cssText = v; // CSSOM: allowed by the page's CSP, style attributes are not
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const $ = (sel) => document.querySelector(sel);

function ago(ts, now) {
  if (!ts) return "";
  const s = Math.max(0, (now || Date.now() / 1000) - ts);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

function clock(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString([], today ? { hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

let toastTimer = null;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

class ApiError extends Error {
  constructor(msg, status, data) { super(msg); this.status = status; this.data = data; }
}

async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") {
    opt.headers["X-Jarvis"] = "1";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body || {});
  }
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    throw new ApiError("The device is not reachable.", 0, {});
  }
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (r.status === 401 && path !== "/api/login") {
    showAuth(data.password_set !== false);
    throw new ApiError("login required", 401, data);
  }
  if (!r.ok) throw new ApiError(data.error || r.statusText, r.status, data);
  return data;
}

function dirty() { return Object.keys(S.changes).length > 0; }

function unsaved() {
  return ((S.view === "settings" || S.view === "setup") && dirty()) || (S.view === "workspace" && S.wsDirty.size > 0)
    || (S.view === "tools" && S.toolsDirty);
}

// --- start, login, routing ----------------------------------------------------------------

async function start() {
  let session;
  try {
    session = await api("GET", "/api/session");
  } catch (e) {
    $("#main").replaceChildren(h("div", { class: "notice bad" }, h("p", { text: e.message })));
    return;
  }
  setName(session.name);
  if (!session.password_set) return renderSetup();
  if (!session.authenticated) return renderLogin();
  $("#nav").hidden = false;
  $("#logout").hidden = false;
  $("#main").replaceChildren(h("div", { id: "banner" }), h("div", { id: "view" }));
  if (session.needs_setup && !location.hash) history.replaceState(null, "", "#setup");
  poll();
  route();
}

function setName(name) {
  $("#brand-name").textContent = name || "Jarvis";
  document.title = name || "Jarvis";
}

function showAuth(passwordSet) {
  stopTimers();
  $("#nav").hidden = true;
  $("#logout").hidden = true;
  if (passwordSet) renderLogin(); else renderSetup();
}

function stopTimers() {
  clearTimeout(S.pollTimer);
  clearInterval(S.logTimer);
  clearTimeout(S.pairTimer);
  S.pollTimer = S.logTimer = S.pairTimer = null;
}

function renderLogin() {
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true, autofocus: true });
  const err = h("p", { class: "error small" });
  const form = h("form", { class: "card stack", onsubmit: async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    try {
      await api("POST", "/api/login", { password: pw.value });
      start();
    } catch (e) {
      err.textContent = e.message;
      pw.select();
    }
  } },
    h("label", {}, h("div", { text: "Password" }), pw),
    err,
    h("button", { class: "btn primary", type: "submit", text: "Log in" }));
  $("#main").replaceChildren(h("div", { class: "center" }, h("h1", { text: "Log in" }), form));
  pw.focus();
}

function renderSetup() {
  const pw = h("input", { type: "password", autocomplete: "new-password", minlength: 8, required: true });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true });
  const err = h("p", { class: "error small" });
  const form = h("form", { class: "card stack", onsubmit: async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    if (pw.value !== pw2.value) { err.textContent = "The passwords differ."; return; }
    try {
      await api("POST", "/api/setup", { password: pw.value });
      start();
    } catch (e) { err.textContent = e.message; }
  } },
    h("p", { class: "muted small", text: "Choose a password for this page. Anyone on your network who knows it can "
      + "change the settings, including where API tokens are sent." }),
    h("label", {}, h("div", { text: "Password (at least 8 characters)" }), pw),
    h("label", {}, h("div", { text: "Repeat" }), pw2),
    err,
    h("button", { class: "btn primary", type: "submit", text: "Set password" }));
  $("#main").replaceChildren(h("div", { class: "center" }, h("h1", { text: "Welcome" }), form));
  pw.focus();
}

function route() {
  const view = (location.hash || "#status").slice(1);
  S.view = ["status", "settings", "workspace", "tools", "log", "setup"].includes(view) ? view : "status";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  clearInterval(S.logTimer);
  clearInterval(S.setupTimer);
  S.logTimer = S.setupTimer = null;
  if (S.view === "status") renderStatus();
  else if (S.view === "settings") loadSettings();
  else if (S.view === "workspace") loadWorkspace();
  else if (S.view === "tools") loadTools();
  else if (S.view === "setup") loadSetup();
  else renderLog();
}

let lastHash = location.hash;
window.addEventListener("hashchange", () => {
  if (unsaved() && !confirm("Discard the unsaved changes?")) {
    history.replaceState(null, "", lastHash);
    return;
  }
  S.changes = {};
  S.wsDirty = new Set();
  S.toolsDirty = false;
  lastHash = location.hash;
  route();
});

window.addEventListener("beforeunload", (ev) => {
  if (unsaved()) { ev.preventDefault(); ev.returnValue = ""; }
});

$("#logout").addEventListener("click", async () => {
  await api("POST", "/api/logout").catch(() => {});
  stopTimers();
  showAuth(true);
});

// --- status polling -------------------------------------------------------------------------

async function poll() {
  clearTimeout(S.pollTimer);
  if (!document.hidden) {
    try {
      S.status = await api("GET", "/api/status");
      onStatus();
    } catch (e) {
      if (e.status === 401) return;
      setDot("bad", "The device is not reachable");
    }
  }
  S.pollTimer = setTimeout(poll, S.restarting ? 1500 : 3000);
}

document.addEventListener("visibilitychange", () => { if (!document.hidden && S.view) poll(); });

function jarvisState(st) {
  const j = st.jarvis, svc = st.service;
  if (S.restarting) return { dot: "warn", text: "Restarting…" };
  if (!j.alive) {
    if (svc.active === "activating" || svc.active === "active") return { dot: "warn", text: "Starting…" };
    if (svc.installed === false) return { dot: "bad", text: "Not running" };
    return { dot: "bad", text: svc.active === "failed" ? "Crashed" : "Not running" };
  }
  if (j.state === "unconfigured") return { dot: "warn", text: "Waiting for setup" };
  if (j.alarm) return { dot: "warn", text: "Alarm ringing" };
  if (j.state === "booting") return { dot: "warn", text: "Starting…" };
  if (j.state === "session") return { dot: "busy", text: "In a conversation" };
  return { dot: "ok", text: j.test_mode ? "Waiting (test mode)" : "Listening" };
}

function setDot(cls, title) {
  const d = $("#state-dot");
  d.className = "dot " + (cls || "");
  d.title = title || "";
}

function onStatus() {
  const st = S.status;
  setName(st.name);
  if (S.restarting) {
    const j = st.jarvis;
    if (j.alive && j.started !== S.restarting.started && j.state !== "booting") {
      S.restarting = null;
      toast("Jarvis is back.");
    } else if (Date.now() / 1000 - S.restarting.at > 240) {
      S.restarting = null;
      toast("Jarvis did not come back within 4 minutes. Check the log.", 6000);
    }
  }
  const state = jarvisState(st);
  setDot(state.dot, state.text);
  renderBanner();
  if (S.view === "status") renderStatus();
}

async function restart(when) {
  if (when === "now" && S.status?.jarvis?.busy && !confirm(`Jarvis is ${S.status.jarvis.busy}. Restart anyway?`)) return;
  try {
    const r = await api("POST", "/api/restart", { when });
    if (r.waiting) {
      toast(`Restarting as soon as Jarvis is idle (now ${r.waiting}).`, 5000);
    } else {
      S.restarting = { at: Date.now() / 1000, started: S.status?.jarvis?.started };
      toast("Restarting. This takes about a minute on a Pi Zero.", 5000);
    }
    poll();
  } catch (e) {
    toast("Restart failed: " + e.message, 8000);
  }
}

function restartButtons() {
  const busy = S.status?.jarvis?.busy;
  if (S.restarting || S.status?.restart_pending) return [];
  if (busy) {
    return [h("button", { class: "btn primary", text: "Restart when idle", onclick: () => restart("idle") }),
      h("button", { class: "btn danger", text: "Restart now", onclick: () => restart("now") })];
  }
  return [h("button", { class: "btn primary", text: "Restart Jarvis", onclick: () => restart("now") })];
}

function renderBanner() {
  const box = $("#banner");
  if (!box || !S.status) return;
  const st = S.status;
  const items = [];
  if (st.restart_pending) {
    items.push(h("div", { class: "notice info" },
      h("p", { text: `Restart waits until Jarvis is idle (${st.jarvis.busy || "almost"}), requested ${ago(st.restart_pending.since, st.now)} ago.` })));
  } else if (S.restarting) {
    items.push(h("div", { class: "notice info" }, h("p", { text: "Restarting Jarvis…" })));
  } else if (st.pending_changes && st.pending_changes.length) {
    items.push(h("div", { class: "notice warn" },
      h("p", {}, h("strong", { text: "Restart needed. " }),
        `Changed since Jarvis started: ${st.pending_changes.join(", ")}.`),
      h("div", { class: "row" }, restartButtons())));
  }
  box.replaceChildren(...items);
}

// --- status view ----------------------------------------------------------------------------------

function meter(used, total, warnAt = 0.8, badAt = 0.92) {
  const f = total ? Math.min(1, used / total) : 0;
  return h("div", { class: "meter" + (f >= badAt ? " bad" : f >= warnAt ? " warn" : "") },
    h("span", { style: `width:${(f * 100).toFixed(1)}%` }));
}

function renderStatus() {
  const view = $("#view");
  if (!view) return;
  const st = S.status;
  if (!st) { view.replaceChildren(h("p", { class: "muted", text: "Loading…" })); return; }
  const j = st.jarvis, svc = st.service, sys = st.system;
  const state = jarvisState(st);

  const facts = [];
  const fact = (k, v, cls) => facts.push(h("dt", { text: k }), h("dd", { class: cls || null }, v));
  if (j.alive && j.since) fact("Since", `${ago(j.since, st.now)}`);
  if (j.alive && j.started) fact("Running since", clock(j.started));
  if (j.learner) fact("Learner", `updating the workspace (${j.learner})`);
  if (svc.restarts) fact("Restarts by systemd", String(svc.restarts), "badge warn");
  if (svc.installed === false) fact("Service", "jarvis.service not installed");
  else if (!j.alive && svc.active) fact("Service", `${svc.active}${svc.sub ? " (" + svc.sub + ")" : ""}`);

  const setupNotice = st.needs_setup ? h("div", { class: "notice warn" },
    h("p", {}, h("strong", { text: "Jarvis needs a Gemini API key. " }), "The setup assistant takes two minutes."),
    h("a", { class: "btn primary", href: "#setup", text: "Start the setup" })) : null;
  const jarvisCard = h("section", { class: "card" },
    h("h2", { text: "Assistant" }),
    h("div", { class: "big" }, h("span", { class: "dot " + state.dot }), state.text),
    h("dl", { class: "kv" }, facts),
    j.error ? h("div", { class: "notice bad", style: "margin:12px 0 0" },
      h("p", { class: "small" }, h("strong", { text: `Last error (${clock(j.error_at)}): ` }), j.error)) : null,
    h("div", { class: "row", style: "margin-top:12px" }, restartButtons(),
      h("a", { class: "btn", href: "#log", text: "Log" })));

  const servers = Object.entries(j.mcp?.servers || {});
  const mcpCard = h("section", { class: "card" },
    h("h2", { text: "Tool servers (MCP)" }),
    !j.alive ? h("p", { class: "muted small", text: "Shown while Jarvis runs." })
      : servers.length === 0 ? h("p", { class: "muted small", text: "No MCP servers configured." })
        : h("ul", { class: "list" }, servers.map(([name, online]) =>
          h("li", {}, h("span", { class: "dot " + (online ? "ok" : "bad") }), name,
            h("span", { class: "spacer" }), h("span", { class: "muted small", text: online ? "online" : "offline" })))),
    j.alive && j.mcp?.tools != null ? h("p", { class: "muted small", text: `${j.mcp.tools} tools loaded at start` }) : null);

  const memUsed = sys.mem_total - sys.mem_available;
  const sysFacts = [];
  const sf = (k, v) => sysFacts.push(h("dt", { text: k }), h("dd", {}, v));
  if (sys.jarvis_pss) sf("Jarvis", `${sys.jarvis_pss} MB`);
  if (sys.web_rss) sf("This page's server", `${sys.web_rss} MB`);
  if (sys.load) sf("Load", sys.load);
  if (sys.temp) sf("CPU temperature", `${sys.temp.toFixed(0)} °C`);
  if (sys.uptime) sf("Up for", ago(st.now - sys.uptime, st.now));
  const sysCard = h("section", { class: "card" },
    h("h2", { text: sys.hostname || "System" }),
    sys.mem_total ? [
      h("div", { class: "row small" }, h("span", { text: "RAM" }), h("span", { class: "spacer" }),
        h("span", { class: "muted", text: `${sys.mem_available} MB free of ${sys.mem_total} MB` })),
      meter(memUsed, sys.mem_total, 0.8, 0.9)] : null,
    sys.swap_total ? [
      h("div", { class: "row small" }, h("span", { text: "Swap" }), h("span", { class: "spacer" }),
        h("span", { class: "muted", text: `${sys.swap_used} MB of ${sys.swap_total} MB used` })),
      meter(sys.swap_used, sys.swap_total, 0.7, 0.9)] : null,
    h("dl", { class: "kv small" }, sysFacts));

  view.replaceChildren(...[setupNotice, jarvisCard, timersCard(st), h("div", { class: "grid" }, mcpCard, sysCard)].filter(Boolean));
}

// --- settings view ----------------------------------------------------------------------------

async function loadSettings() {
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted", text: "Loading…" }));
  try {
    S.settings = await api("GET", "/api/settings");
  } catch (e) {
    if (e.status !== 401) view.replaceChildren(h("div", { class: "notice bad" }, h("p", { text: e.message })));
    return;
  }
  S.changes = {};
  S.errors = {};
  renderSettings();
}

function renderSettings() {
  const view = $("#view");
  S.fields = {};
  S.optionLoaders = [];
  const cards = S.settings.groups.map((g) => h("section", { class: "card", id: "group-" + g.id },
    h("h2", { text: g.title }),
    h("p", { class: "desc", text: g.description }),
    g.settings.map(field),
    g.id === "messages" ? telegramConnect() : null,
    TESTABLE[g.id] ? testBox(g.id) : null,
    g.id === "web" ? passwordForm() : null));
  if (S.settings.other.length) cards.push(otherKeys(S.settings.other));
  const intro = h("div", { class: "row small muted", style: "margin-bottom:12px" },
    h("span", {}, "Most settings apply from the next conversation. Those marked ", h("span", { class: "badge", text: "restart" }),
      " need a restart of Jarvis."), h("span", { class: "spacer" }), h("a", { href: "#setup", text: "Setup assistant" }));
  view.replaceChildren(intro, ...cards, saveBar());
  updateSaveBar();
}

function setChange(key, value) {
  if (value === undefined) delete S.changes[key];
  else S.changes[key] = value;
  delete S.errors[key];
  const f = S.fields[key];
  if (f) {
    f.el.classList.toggle("changed", key in S.changes);
    f.err.textContent = "";
  }
  // a newly typed API key or URL: reload the lists that depend on it
  for (const l of S.optionLoaders) {
    if (!l.deps.includes(key)) continue;
    clearTimeout(l.timer);
    l.timer = setTimeout(l.load, 800);
  }
  if (key === "GEMINI_LIVE_MODEL") updateThinkingHint();
  updateSaveBar();
}

// --- dropdowns --------------------------------------------------------------------------------
// Settings with offered values get a <select>: fixed choices, suggestions (voices, languages), or a list the
// server loads for the setting's source (the models the Gemini key can use, installed wake word models).
// "Other…" allows any value unless the schema has fixed choices.

const OTHER = "\u0001other";

function optionText(o) {
  return o.value + (o.thinking ? " · thinking" : "") + (o.note ? ` · ${o.note}` : "");
}

function effective(item) {
  // what the form shows now: a pending change, else the saved value ("" = not set)
  return item.key in S.changes ? (S.changes[item.key] ?? "") : (item.value ?? "");
}

function loadOptions(item, done) {
  const load = async () => {
    const values = {};
    for (const key of item.source_deps) if (typeof S.changes[key] === "string") values[key] = S.changes[key];
    try {
      const r = await api("POST", "/api/options", { source: item.source, values });
      done(r.options, r.error);
    } catch (e) {
      if (e.status !== 401) done([], e.message);
    }
  };
  S.optionLoaders.push({ deps: item.source_deps, load });
  load();
}

function picker(item) {
  const stored = item.value ?? "";
  const fixed = item.choices.length > 0;
  let options = (fixed ? item.choices : item.suggest).map((v) => ({ value: v }));
  let loading = Boolean(item.source);
  const note = h("div", { class: "help" });
  const commit = (v) => setChange(item.key, v === stored ? undefined : (v === "" ? null : v));
  const other = h("input", { type: "text", hidden: true, spellcheck: false, autocomplete: "off",
    placeholder: "Any value", style: "margin-top:6px", oninput: () => commit(other.value.trim()) });
  const sel = h("select", { onchange: () => {
    if (sel.value === OTHER) {
      other.hidden = false;
      other.value = effective(item);
      other.focus();
      return;
    }
    other.hidden = true;
    commit(sel.value);
  } });
  const draw = () => {
    const current = effective(item);
    const known = new Set(options.map((o) => o.value));
    const opts = [h("option", { value: "", text: item.default ? `Default (${item.default})` : "Not set" })];
    for (const o of options) opts.push(h("option", { value: o.value, text: optionText(o) }));
    if (current && !known.has(current)) {
      opts.push(h("option", { value: current, text: loading || !item.source ? current : `${current} (not in the list)` }));
    }
    if (!fixed) opts.push(h("option", { value: OTHER, text: "Other…" }));
    sel.replaceChildren(...opts);
    sel.value = other.hidden ? current : OTHER;
  };
  if (item.source) {
    note.textContent = "Loading the list…";
    loadOptions(item, (list, error) => {
      loading = false;
      if (list.length || !error) options = list;
      note.textContent = error ? `List not available: ${error}. Choose "Other…" to type a value.` : "";
      draw();
      if (item.key === "GEMINI_LIVE_MODEL") {
        S.liveModels = error ? null : Object.fromEntries(list.map((o) => [o.value, o.thinking]));
        updateThinkingHint();
      }
    });
  }
  draw();
  return h("div", {}, sel, other, note);
}

function listPicker(item, input) {
  // a <select> that appends an offered value to a comma-separated list (e.g. learner models, tried in order)
  const note = h("div", { class: "help", text: "Loading the list…" });
  const add = h("select", { disabled: true, style: "margin-top:6px", onchange: () => {
    const v = add.value;
    add.value = "";
    if (!v) return;
    // an empty field means the default list: extend that one
    const parts = (input.value.trim() || item.default).split(",").map((p) => p.trim()).filter(Boolean);
    if (!parts.includes(v)) parts.push(v);
    input.value = parts.join(",");
    input.dispatchEvent(new Event("input"));
  } }, h("option", { value: "", text: "Add…" }));
  loadOptions(item, (list, error) => {
    add.replaceChildren(h("option", { value: "", text: "Add…" }), ...list.map((o) => h("option", { value: o.value, text: optionText(o) })));
    add.disabled = !list.length;
    note.textContent = error ? `List not available: ${error}` : "";
  });
  return [add, note];
}

function updateThinkingHint() {
  const f = S.fields.JARVIS_THINKING;
  const live = findItem("GEMINI_LIVE_MODEL");
  if (!f || !f.hint || !live) return;
  const model = effective(live) || live.default;
  const thinks = S.liveModels ? S.liveModels[model] : undefined;
  f.hint.textContent = thinks === undefined ? ""
    : thinks ? `${model} supports thinking.` : `${model} doesn't support thinking: Jarvis leaves this setting out.`;
}

function field(item, opts = {}) {
  const err = h("div", { class: "error" });
  const meta = h("div", { class: "meta" });
  let control;
  const stored = item.value ?? "";

  if (item.kind === "bool") {
    const effective = (item.value ?? item.default) === "1";
    const text = h("span", { class: "muted small", text: effective ? "on" : "off" });
    const box = h("input", { type: "checkbox", checked: effective, onchange: () => {
      const v = box.checked ? "1" : "0";
      text.textContent = box.checked ? "on" : "off";
      setChange(item.key, v === item.value || (!item.set && v === item.default) ? undefined : v);
    } });
    control = h("label", { class: "switch" }, box, text);
    if (item.set) {
      const reset = h("button", { class: "link", type: "button", text: "Reset to default", onclick: () => {
        box.checked = item.default === "1";
        text.textContent = box.checked ? "on" : "off";
        setChange(item.key, null);
      } });
      meta.append(reset);
    }
  } else if (item.kind === "secret") {
    const state = h("div", { class: "secret-state", text: item.set ? `Set (${item.hint})` : "Not set" });
    const input = h("input", { type: "password", autocomplete: "off", spellcheck: false,
      placeholder: item.set ? "New value, empty = keep" : "Enter a value",
      oninput: () => setChange(item.key, input.value.trim() ? input.value.trim() : undefined) });
    const remove = item.set ? h("button", { class: "link", type: "button", text: "Remove", onclick: () => {
      const removing = S.changes[item.key] !== null;
      input.value = "";
      input.disabled = removing;
      state.textContent = removing ? "Will be removed" : `Set (${item.hint})`;
      remove.textContent = removing ? "Keep" : "Remove";
      setChange(item.key, removing ? null : undefined);
    } }) : null;
    control = h("div", {}, state, input);
    if (remove) meta.append(remove);
  } else if (item.kind === "text" && (item.source || item.choices.length || item.suggest.length)) {
    control = picker(item);
  } else {
    const numeric = item.kind === "int" || item.kind === "float";
    const listId = item.suggest.length ? "dl-" + item.key : null;
    const input = h("input", {
      type: numeric ? "number" : item.kind === "url" ? "url" : "text",
      value: stored, placeholder: item.default ? item.default : "not set",
      step: item.kind === "int" ? "1" : numeric ? "any" : null,
      min: item.min ?? null, max: item.max ?? null, list: listId, spellcheck: false, autocomplete: "off",
      oninput: () => {
        const v = input.value.trim();
        setChange(item.key, v === stored ? undefined : (v === "" ? null : v));
      },
    });
    control = h("div", {}, input,
      listId ? h("datalist", { id: listId }, item.suggest.map((s) => h("option", { value: s }))) : null,
      item.kind === "list" && item.source ? listPicker(item, input) : null);
    if (item.set) {
      meta.append(h("button", { class: "link", type: "button", text: item.default ? "Reset to default" : "Clear",
        onclick: () => {
          input.value = "";
          setChange(item.key, null);
        } }));
    }
  }
  const picked = item.kind === "text" && (item.source || item.choices.length || item.suggest.length);  // default = first entry
  const hint = item.key === "JARVIS_THINKING" ? h("span", {}) : null;
  if (hint) meta.append(hint);
  if (item.kind !== "secret" && item.default && !picked) {
    meta.prepend(h("span", { text: `Default: ${item.kind === "bool" ? (item.default === "1" ? "on" : "off") : item.default}` }));
  }
  if (item.alias) meta.append(h("span", { text: `Read from the older name ${item.alias}; saving writes ${item.key}.` }));

  const el = h("div", { class: "field" },
    h("div", {}, h("label", {}, item.label, item.applies === "restart" && !opts.noBadge ? [" ", h("span", { class: "badge", text: "restart" })] : null),
      item.help ? h("div", { class: "help", text: item.help }) : null),
    h("div", {}, control, meta, err));
  S.fields[item.key] = { el, err, hint };
  if (hint) setTimeout(updateThinkingHint);
  return el;
}

function otherKeys(other) {
  return h("section", { class: "card" },
    h("h2", { text: "Other entries in .env" }),
    h("p", { class: "desc", text: "Keys Jarvis does not read, or older names. Removing one comments it out in .env." }),
    h("ul", { class: "list" }, other.map((o) => {
      const btn = h("button", { class: "link", type: "button", text: "Remove", onclick: () => {
        const removing = !(o.key in S.changes);
        setChange(o.key, removing ? null : undefined);
        btn.textContent = removing ? "Keep" : "Remove";
        li.classList.toggle("muted", removing);
      } });
      const li = h("li", {}, h("code", { text: o.key }), h("span", { class: "muted small", text: o.note }),
        h("span", { class: "spacer" }), btn);
      return li;
    })));
}

function passwordForm() {
  const cur = h("input", { type: "password", autocomplete: "current-password" });
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password" });
  const err = h("div", { class: "error" });
  const go = async () => {
    err.textContent = "";
    if (pw.value.length < 8) { err.textContent = "At least 8 characters."; return; }
    if (pw.value !== pw2.value) { err.textContent = "The new passwords differ."; return; }
    try {
      await api("POST", "/api/password", { current: cur.value, new: pw.value });
      cur.value = pw.value = pw2.value = "";
      toast("Password changed. Other browsers have to log in again.");
    } catch (e) { err.textContent = e.message; }
  };
  return h("div", { class: "field" },
    h("div", {}, h("label", { text: "Password" }),
      h("div", { class: "help", text: "Changing it logs out every other browser." })),
    h("div", { class: "stack" },
      h("input", { type: "text", autocomplete: "username", value: "jarvis", hidden: true }),
      h("label", {}, h("div", { class: "small muted", text: "Current password" }), cur),
      h("label", {}, h("div", { class: "small muted", text: "New password" }), pw),
      h("label", {}, h("div", { class: "small muted", text: "Repeat" }), pw2),
      err,
      h("div", {}, h("button", { class: "btn", type: "button", text: "Change password", onclick: go }))));
}

function saveBar() {
  return h("div", { class: "savebar", id: "savebar", hidden: true },
    h("div", { class: "inner" },
      h("span", { id: "savecount" }),
      h("span", { class: "spacer" }),
      h("button", { class: "btn", type: "button", text: "Discard", onclick: () => { S.changes = {}; renderSettings(); } }),
      h("button", { class: "btn primary", type: "button", id: "savebtn", text: "Save", onclick: save })));
}

function updateSaveBar() {
  const bar = $("#savebar");
  if (!bar) return;
  const n = Object.keys(S.changes).length;
  bar.hidden = n === 0;
  $("#savecount").textContent = n === 1 ? "1 unsaved change" : `${n} unsaved changes`;
}

async function save() {
  const btn = $("#savebtn");
  btn.disabled = true;
  try {
    const r = await api("PUT", "/api/settings", { changes: S.changes });
    S.settings = { groups: r.groups, other: r.other };
    S.changes = {};
    S.errors = {};
    renderSettings();
    if (S.status) S.status.pending_changes = r.pending_changes;
    renderBanner();
    const needsRestart = r.changed.some((k) => findItem(k)?.applies === "restart");
    toast(!r.changed.length ? "Nothing changed."
      : needsRestart ? "Saved. Some changes need a restart of Jarvis."
        : "Saved. Applies from the next conversation.", 4500);
  } catch (e) {
    if (e.data?.errors) {
      S.errors = e.data.errors;
      let first = null;
      for (const [key, msg] of Object.entries(e.data.errors)) {
        const f = S.fields[key];
        if (f) { f.err.textContent = msg; first = first || f.el; }
      }
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
      toast("Some values are invalid.");
    } else if (e.status !== 401) {
      toast("Saving failed: " + e.message, 6000);
    }
  } finally {
    if ($("#savebtn")) $("#savebtn").disabled = false;
  }
}

// --- log view ------------------------------------------------------------------------------------

function renderLog() {
  const view = $("#view");
  const pre = h("pre", { class: "log", text: "Loading…" });
  const lines = h("select", { onchange: () => load() },
    [100, 300, 1000].map((n) => h("option", { value: n, text: `${n} lines`, selected: n === 300 })));
  const filter = h("input", { type: "text", placeholder: "Filter", oninput: () => draw() });
  const errorsOnly = h("input", { type: "checkbox", onchange: () => draw() });
  const follow = h("input", { type: "checkbox", checked: true, onchange: () => setFollow() });
  let data = [];

  const cls = (l) => /Traceback|Error|ERROR|❌|Exception|failed/.test(l) ? "err"
    : /[Ww]arn|Watchdog|Near miss/.test(l) ? "warn"
      : /Wake Word Detected|\[Idle\]|\[Boot\]/.test(l) ? "hl" : null;

  function draw() {
    const q = filter.value.trim().toLowerCase();
    const shown = data.filter((l) => (!q || l.toLowerCase().includes(q)) && (!errorsOnly.checked || cls(l) === "err"));
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    pre.replaceChildren(...shown.map((l) => h("span", { class: cls(l) }, l + "\n")));
    if (!shown.length) pre.textContent = data.length ? "No matching lines." : "The log is empty.";
    if (follow.checked && atBottom) pre.scrollTop = pre.scrollHeight;
  }

  async function load(first) {
    try {
      const r = await api("GET", `/api/logs?lines=${lines.value}`);
      data = r.lines;
      if (r.error) data = [r.error];
      draw();
      if (first) pre.scrollTop = pre.scrollHeight;
    } catch (e) {
      if (e.status !== 401) pre.textContent = e.message;
    }
  }

  function setFollow() {
    clearInterval(S.logTimer);
    S.logTimer = follow.checked ? setInterval(() => { if (!document.hidden) load(); }, 4000) : null;
  }

  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "row" },
      h("h2", { text: "Log", style: "margin:0" }), h("span", { class: "spacer" }),
      lines,
      h("button", { class: "btn", text: "Refresh", onclick: () => load() })),
    h("div", { class: "row", style: "margin-top:8px" },
      h("div", { style: "flex:1;min-width:160px" }, filter),
      h("label", { class: "switch" }, errorsOnly, h("span", { class: "small", text: "Errors only" })),
      h("label", { class: "switch" }, follow, h("span", { class: "small", text: "Follow" }))),
    pre));
  load(true);
  setFollow();
}

// --- connection tests (settings) ---------------------------------------------------------------

const TESTABLE = {
  gemini: ["GEMINI_KEYS", "GEMINI_LIVE_MODEL"],
  home_assistant: ["HA_URL", "HA_TOKEN"],
  openclaw: ["OPENCLAW_URL", "OPENCLAW_TOKEN"],
  messages: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "JARVIS_HA_NOTIFY", "HA_URL", "HA_TOKEN", "JARVIS_NAME"],
};
const TEST_LABEL = { messages: "Send test message" };

function typedValues(keys) {
  // values typed into the form but not saved yet; the server falls back to the saved ones
  const values = {};
  for (const key of keys) if (typeof S.changes[key] === "string") values[key] = S.changes[key];
  return values;
}

function testBox(group) {
  const out = h("ul", { class: "list small", style: "margin-top:8px" });
  const btn = h("button", { class: "btn", type: "button", text: TEST_LABEL[group] || "Test connection", onclick: async () => {
    btn.disabled = true;
    out.replaceChildren(h("li", { class: "muted", text: "Testing…" }));
    const values = typedValues(TESTABLE[group]);
    try {
      const r = await api("POST", "/api/test", { what: group, values });
      out.replaceChildren(...r.results.map((x) => h("li", {},
        h("span", { class: "dot " + (x.ok ? "ok" : "bad") }), h("strong", { text: x.label }),
        h("span", { class: "muted", text: x.detail }))));
    } catch (e) {
      if (e.status !== 401) out.replaceChildren(h("li", { class: "error", text: e.message }));
    } finally { btn.disabled = false; }
  } });
  return h("div", { class: "field" },
    h("div", {}, h("div", { class: "help", text: "Uses the values typed above, saved or not." })),
    h("div", {}, btn, out));
}

// Connecting a Telegram chat: the server shows a t.me link with a one-time code, the user presses Start in
// Telegram, and the page polls until the bot has seen that message. The chat id then goes into the form.
const PAIR_POLL_MS = 2000;
const PAIR_WAIT_MS = 180000;

function telegramConnect() {
  const out = h("div", { class: "small" });
  const fail = (msg) => out.replaceChildren(h("p", { class: "error", text: msg }));

  function useChat(chat) {
    const id = String(chat.id);
    const f = S.fields.TELEGRAM_CHAT_ID;
    const input = f && f.el.querySelector("input");
    if (input) input.value = id;
    const stored = findItem("TELEGRAM_CHAT_ID")?.value ?? "";
    setChange("TELEGRAM_CHAT_ID", id === stored ? undefined : id);
    out.replaceChildren(h("p", {}, h("span", { class: "dot ok" }), `Connected with ${chat.name}. `,
      dirty() ? h("strong", { text: "Save to keep it." }) : "Nothing else to save."));
  }

  const btn = h("button", { class: "btn", type: "button", text: "Connect Telegram", onclick: async () => {
    clearTimeout(S.pairTimer);
    btn.disabled = true;
    out.replaceChildren(h("p", { class: "muted", text: "Asking Telegram…" }));
    let r;
    try {
      r = await api("POST", "/api/telegram/pair", { values: typedValues(["TELEGRAM_BOT_TOKEN"]) });
    } catch (e) {
      if (e.status !== 401) fail(e.message);
      return;
    } finally { btn.disabled = false; }
    const waiting = h("p", { class: "muted", text: "Waiting for your message…" });
    out.replaceChildren(
      h("p", {}, "Open ", h("a", { href: r.link, target: "_blank", rel: "noopener", text: `t.me/${r.bot}` }),
        " on your phone and press Start. ",
        h("span", { class: "muted", text: `Or send @${r.bot} the message ${r.code}` })),
      waiting);
    const until = Date.now() + PAIR_WAIT_MS;
    const poll = async () => {
      if (!document.body.contains(out)) return;  // the page was left or re-rendered
      try {
        const c = await api("POST", "/api/telegram/check", {});
        if (c.chat) return useChat(c.chat);
        if (c.error) return fail(c.error);
      } catch (e) {
        if (e.status !== 401) fail(e.message);
        return;
      }
      if (Date.now() > until) {
        waiting.textContent = "No message arrived. Click 'Connect Telegram' to try again.";
        return;
      }
      S.pairTimer = setTimeout(poll, PAIR_POLL_MS);
    };
    S.pairTimer = setTimeout(poll, PAIR_POLL_MS);
  } });

  return h("div", { class: "field" },
    h("div", {}, h("label", { text: "Connect Telegram" }),
      h("div", { class: "help", text: "Links the chat with your bot: Jarvis only writes to this chat." })),
    h("div", {}, btn, out));
}

// --- timers (status) -------------------------------------------------------------------------

function timersCard(st) {
  const timers = (st.timers || []).filter((t) => t.due);
  if (!timers.length) return null;
  return h("section", { class: "card" },
    h("h2", { text: "Timers and alarms" }),
    h("ul", { class: "list" }, timers.map((t) => h("li", {},
      h("span", { class: "badge", text: t.kind }), t.label || "(no label)",
      h("span", { class: "spacer" }),
      h("span", { class: "muted small", text: `${clock(t.due)} · in ${ago(st.now - (t.due - st.now), st.now)}` })))),
    h("p", { class: "muted small", text: "Cancel them by voice: Jarvis keeps its timers in memory." }));
}

// --- workspace view ------------------------------------------------------------------------------

async function loadWorkspace() {
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted", text: "Loading…" }));
  try {
    S.ws = await api("GET", "/api/workspace");
  } catch (e) {
    if (e.status !== 401) view.replaceChildren(h("div", { class: "notice bad" }, h("p", { text: e.message })));
    return;
  }
  S.wsDirty = new Set();
  renderWorkspace();
}

function wsError(e) {
  if (e.status === 401) return;
  toast(e.message, 7000);
}

function wsApply(r, msg) {
  S.ws = r;
  S.wsDirty = new Set();
  renderWorkspace();
  if (msg) toast(msg);
}

function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(window.innerHeight * 0.7, ta.scrollHeight + 4) + "px";
}

function counter(len, budget) {
  const cls = len > budget ? "badge bad" : len > budget * 0.9 ? "badge warn" : "badge";
  return h("span", { class: cls, text: `${len} / ${budget}` });
}

function fileCard(f) {
  const ta = h("textarea", { class: "mono", spellcheck: false, rows: 6 });
  ta.value = f.text;
  const count = h("span", {}, counter(f.text.length, f.budget));
  const over = h("p", { class: "error small", hidden: f.text.length <= f.budget,
    text: `Over budget: Jarvis only sees the first ${f.budget} characters. Merge or shorten entries.` });
  const save = h("button", { class: "btn primary", type: "button", text: "Save", disabled: true });
  const revert = h("button", { class: "btn", type: "button", text: "Revert", disabled: true });
  const update = () => {
    const changed = ta.value !== f.text;
    save.disabled = revert.disabled = !changed;
    if (changed) S.wsDirty.add(f.name); else S.wsDirty.delete(f.name);
    count.replaceChildren(counter(ta.value.length, f.budget));
    over.hidden = ta.value.length <= f.budget;
    autosize(ta);
  };
  ta.addEventListener("input", update);
  revert.addEventListener("click", () => { ta.value = f.text; update(); });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const r = await api("PUT", "/api/workspace/file", { name: f.name, text: ta.value, hash: f.hash });
      wsApply(r, r.changed.length ? `${f.title} saved. Used from the next conversation on.` : "Nothing changed.");
    } catch (e) { wsError(e); save.disabled = false; }
  });
  const card = h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", { text: f.title, style: "margin:0" }), h("span", { class: "badge", text: f.file }),
      h("span", { class: "spacer" }), count),
    h("p", { class: "desc", text: f.description }),
    ta, over,
    h("div", { class: "row", style: "margin-top:8px" }, save, revert));
  requestAnimationFrame(() => autosize(ta));
  return card;
}

function skillEditor(skill, onDone) {
  const isNew = !skill;
  skill = skill || { name: "", description: "", body: "", always: false, hash: null };
  const name = h("input", { type: "text", value: skill.name, placeholder: "e.g. good-morning", disabled: !isNew,
    spellcheck: false, autocomplete: "off" });
  const desc = h("input", { type: "text", value: skill.description, placeholder: "When to use it, e.g. when the user says good morning" });
  const body = h("textarea", { class: "mono", rows: 6, spellcheck: false, placeholder: "1. …\n2. …" });
  body.value = skill.body;
  const always = h("input", { type: "checkbox", checked: skill.always });
  const count = h("span", {}, counter(skill.body.length, S.ws.skill_budget));
  body.addEventListener("input", () => {
    count.replaceChildren(counter(body.value.length, S.ws.skill_budget));
    S.wsDirty.add("skill:" + (skill.name || "new"));
    autosize(body);
  });
  for (const el of [name, desc]) el.addEventListener("input", () => S.wsDirty.add("skill:" + (skill.name || "new")));
  const save = h("button", { class: "btn primary", type: "button", text: isNew ? "Create" : "Save", onclick: async () => {
    save.disabled = true;
    try {
      const r = await api("PUT", "/api/workspace/skill", { name: name.value.trim().toLowerCase(),
        description: desc.value, body: body.value, always: always.checked, hash: skill.hash });
      wsApply(r, isNew ? "Skill created." : "Skill saved.");
    } catch (e) { wsError(e); save.disabled = false; }
  } });
  const del = isNew ? null : h("button", { class: "btn danger", type: "button", text: "Delete", onclick: async () => {
    if (!confirm(`Delete the skill ${skill.name}? It can be restored with Undo.`)) return;
    try {
      wsApply(await api("POST", "/api/workspace/skill/delete", { name: skill.name, hash: skill.hash }), "Skill deleted.");
    } catch (e) { wsError(e); }
  } });
  const cancel = h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => {
    S.wsDirty.delete("skill:" + (skill.name || "new"));
    onDone();
  } });
  requestAnimationFrame(() => autosize(body));
  return h("div", { class: "stack", style: "padding:8px 0 4px" },
    isNew ? h("label", {}, h("div", { class: "small muted", text: "Name (lowercase letters, digits, dashes)" }), name) : null,
    h("label", {}, h("div", { class: "small muted", text: "Description" }), desc),
    h("label", {}, h("div", { class: "row small muted" }, h("span", { text: "Steps" }), h("span", { class: "spacer" }), count), body),
    h("label", { class: "switch" }, always, h("span", { class: "small", text: "Always in the prompt (otherwise Jarvis loads it when needed)" })),
    h("div", { class: "row" }, save, cancel, h("span", { class: "spacer" }), del));
}

function skillsCard() {
  const list = h("ul", { class: "list" });
  const editorSlot = h("div");
  const draw = () => {
    list.replaceChildren(...S.ws.skills.map((s) => {
      const li = h("li", { style: "flex-wrap:wrap" },
        h("code", { text: s.name }),
        s.always ? h("span", { class: "badge", text: "always" }) : null,
        h("span", { class: "muted small", style: "flex:1;min-width:160px", text: s.description }),
        s.last_used ? h("span", { class: "muted small", text: `used ${s.last_used}` }) : null,
        h("button", { class: "link", type: "button", text: "Edit", onclick: () => {
          li.replaceChildren(skillEditor(s, draw));
        } }));
      return li;
    }));
    if (!S.ws.skills.length) list.append(h("li", { class: "muted small", text: "No skills yet. The learner creates them for routines that took several tries." }));
    editorSlot.replaceChildren();
  };
  draw();
  const add = h("button", { class: "btn", type: "button", text: "New skill", onclick: () => {
    editorSlot.replaceChildren(skillEditor(null, draw));
  } });
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", { text: "Skills", style: "margin:0" }),
      h("span", { class: "badge", text: `${S.ws.skills.length} / ${S.ws.max_skills}` }), h("span", { class: "spacer" }), add),
    h("p", { class: "desc", text: "Procedures Jarvis follows, e.g. a morning routine. It sees only names and descriptions and loads the steps when needed." }),
    editorSlot, list);
}

function renderWorkspace() {
  const ws = S.ws;
  const undo = ws.undo ? h("button", { class: "btn", type: "button", text: "Undo last change", onclick: async () => {
    if (!confirm(`Undo the change "${ws.undo.source}" (${ws.undo.id})?`)) return;
    try {
      const r = await api("POST", "/api/workspace/undo");
      wsApply(r, r.result);
    } catch (e) { wsError(e); }
  } }) : null;
  const intro = h("section", { class: "card" },
    h("h2", { text: "Workspace" }),
    h("p", { class: "desc", text: "What Jarvis knows and how it behaves. It reads these files at every wake word: changes apply to the next conversation, no restart needed. The learner also edits them between conversations; every change is logged below and can be undone." }),
    ws.learner ? h("div", { class: "notice info" }, h("p", { text: "The learner is updating the workspace right now. Saving waits until it is done." })) : null,
    h("div", { class: "row" }, undo,
      ws.undo ? h("span", { class: "muted small", text: `Last: ${ws.undo.source}` }) : null));
  const notes = ws.notes.length ? h("section", { class: "card" },
    h("h2", { text: "Waiting for the learner" }),
    h("p", { class: "desc", text: "Things you asked Jarvis to remember. The learner works them in after the conversation." }),
    h("ul", { class: "list small" }, ws.notes.map((n) => h("li", {}, h("span", { class: "muted", text: n.time }), n.note)))) : null;
  const hist = h("section", { class: "card" },
    h("h2", { text: "Recent changes" }),
    ws.history.length ? ws.history.map((e) => h("details", {},
      h("summary", {}, h("span", { class: "muted small", text: e.time + " " }), e.source,
        h("span", { class: "muted small", text: ` · ${e.lines.length} change${e.lines.length === 1 ? "" : "s"}` })),
      h("ul", { class: "small" }, e.lines.map((l) => h("li", { text: l })))))
      : h("p", { class: "muted small", text: "No changes yet." }));
  $("#view").replaceChildren(intro, notes, ...ws.files.map(fileCard), skillsCard(), hist);
}

// --- tools (MCP servers) view ------------------------------------------------------------------

async function loadTools() {
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted", text: "Loading…" }));
  try {
    S.mcp = await api("GET", "/api/mcp");
  } catch (e) {
    if (e.status !== 401) view.replaceChildren(h("div", { class: "notice bad" }, h("p", { text: e.message })));
    return;
  }
  S.toolsDirty = false;
  S.mcpRows = S.mcp.servers.map((s) => ({ ...s, include: s.include == null ? "" : s.include.join(", "),
    exclude: (s.exclude || []).join(", ") }));
  renderTools();
}

function renderTools() {
  const m = S.mcp;
  const rows = h("div");
  const errors = {};
  const touch = () => { S.toolsDirty = true; save.disabled = false; };

  const drawRows = () => {
    rows.replaceChildren(...S.mcpRows.map((r, i) => {
      const input = (key, attrs) => h("input", { type: "text", value: r[key], spellcheck: false, autocomplete: "off",
        ...attrs, oninput: (ev) => { r[key] = ev.target.value; touch(); } });
      const type = h("select", { onchange: (ev) => { r.type = ev.target.value; touch(); } },
        ["sse", "stateless"].map((t) => h("option", { value: t, text: t === "sse" ? "SSE" : "Streamable HTTP (stateless)", selected: r.type === t })));
      const result = h("div", { class: "small" });
      const test = h("button", { class: "btn", type: "button", text: "Test", onclick: async () => {
        test.disabled = true;
        result.replaceChildren(h("span", { class: "muted", text: "Testing…" }));
        try {
          const x = await api("POST", "/api/mcp/test", { url: r.url, type: r.type });
          result.replaceChildren(h("span", { class: "dot " + (x.ok ? "ok" : "bad") }), " ", x.detail);
        } catch (e) { if (e.status !== 401) result.textContent = e.message; } finally { test.disabled = false; }
      } });
      const live = r.online === true ? "online" : r.online === false ? "offline" : null;
      return h("div", { class: "card", style: "margin:0 0 12px;box-shadow:none" },
        h("div", { class: "row" },
          live ? h("span", { class: "dot " + (live === "online" ? "ok" : "bad"), title: live }) : null,
          h("strong", { text: r.name || "New server" }), live ? h("span", { class: "muted small", text: live }) : null,
          h("span", { class: "spacer" }),
          h("button", { class: "link", type: "button", text: "Remove", onclick: () => { S.mcpRows.splice(i, 1); touch(); drawRows(); } })),
        h("div", { class: "grid", style: "margin-top:8px;gap:8px" },
          h("label", {}, h("div", { class: "small muted", text: "Name" }), input("name", { placeholder: "e.g. gateway" })),
          h("label", {}, h("div", { class: "small muted", text: "Type" }), type)),
        h("label", { style: "display:block;margin-top:8px" }, h("div", { class: "small muted", text: "URL" }),
          input("url", { placeholder: "http://host:8000/sse" })),
        h("div", { class: "grid", style: "margin-top:8px;gap:8px" },
          h("label", {}, h("div", { class: "small muted", text: "Only these tools (empty = all)" }), input("include", { placeholder: "e.g. bm_search, bm_read" })),
          h("label", {}, h("div", { class: "small muted", text: "Hide these tools" }), input("exclude", { placeholder: "e.g. *_delete_*" }))),
        errors[i] ? h("p", { class: "error small", text: errors[i] }) : null,
        h("div", { class: "row", style: "margin-top:8px" }, test, result));
    }));
    if (!S.mcpRows.length) rows.append(h("p", { class: "muted small", text: "No servers. Jarvis then only has its built-in tools (smart home, weather, timers, memory)." }));
  };

  const save = h("button", { class: "btn primary", type: "button", text: "Save", disabled: true, onclick: async () => {
    save.disabled = true;
    const servers = S.mcpRows.map((r) => ({ name: r.name.trim(), url: r.url.trim(), type: r.type,
      include: r.include.trim() ? r.include : null, exclude: r.exclude }));
    try {
      const res = await api("PUT", "/api/mcp", { servers });
      S.mcp = res;
      S.toolsDirty = false;
      if (S.status) S.status.pending_changes = res.pending_changes;
      renderBanner();
      toast("Saved. Restart Jarvis to load the tools.");
      loadTools();
    } catch (e) {
      if (e.data?.errors) { Object.assign(errors, e.data.errors); drawRows(); }
      if (e.status !== 401) toast(e.message, 6000);
      save.disabled = false;
    }
  } });
  const add = h("button", { class: "btn", type: "button", text: "Add server", onclick: () => {
    S.mcpRows.push({ name: "", url: "", type: "sse", include: "", exclude: "", online: null });
    touch();
    drawRows();
  } });
  drawRows();
  $("#view").replaceChildren(h("section", { class: "card" },
    h("h2", { text: "Tool servers (MCP)" }),
    h("p", { class: "desc", text: "MCP servers give Jarvis extra tools: web search, calendar, mail, your notes. Every tool goes into every conversation, so hide the ones Jarvis doesn't need. Jarvis loads the tool list at start: restart after saving." }),
    m.error ? h("div", { class: "notice bad" }, h("p", { text: m.error })) : null,
    !m.exists && !m.error ? h("p", { class: "muted small", text: "No core/mcp_config.json yet: saving creates it." }) : null,
    rows,
    h("div", { class: "row" }, add, h("span", { class: "spacer" }),
      m.tools != null ? h("span", { class: "muted small", text: `${m.tools} MCP tools loaded at the last start` }) : null, save)));
}

// --- setup assistant ------------------------------------------------------------------------------

function findItem(key) {
  for (const g of S.settings.groups) for (const s of g.settings) if (s.key === key) return s;
  return null;
}

async function loadSetup() {
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted", text: "Loading…" }));
  try {
    [S.settings, S.onboarding] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/onboarding")]);
  } catch (e) {
    if (e.status !== 401) view.replaceChildren(h("div", { class: "notice bad" }, h("p", { text: e.message })));
    return;
  }
  S.changes = {};
  S.errors = {};
  renderSetupAssistant();
}

function renderSetupAssistant() {
  S.fields = {};
  S.optionLoaders = [];
  const fields = (...keys) => keys.map(findItem).filter(Boolean).map((item) => field(item, { noBadge: true }));
  const answers = {};
  const ob = S.onboarding;
  const questions = ob.questions.map((q) => {
    const input = h("textarea", { rows: 2, placeholder: "optional", oninput: () => { answers[q.key] = input.value; } });
    return h("label", { class: "stack", style: "gap:4px;margin-bottom:12px" }, h("span", { text: q.question }), input);
  });
  const err = h("p", { class: "error" });
  const go = h("button", { class: "btn primary", type: "button", text: "Save and start", onclick: () => finishSetup(answers, go, err) });
  $("#view").replaceChildren(
    h("section", { class: "card" },
      h("h2", { text: "Set up your assistant" }),
      h("p", { class: "desc", text: "A few things Jarvis needs. Everything here can be changed later under Settings and Workspace." })),
    h("section", { class: "card", id: "group-assistant" }, h("h2", { text: "1. Assistant" }), fields("JARVIS_NAME", "JARVIS_LANGUAGE", "JARVIS_VOICE")),
    h("section", { class: "card", id: "group-gemini" },
      h("h2", { text: "2. Gemini API key (required)" }),
      h("p", { class: "desc" }, "Jarvis talks through Google's Gemini Live API. A free key: ",
        h("a", { href: "https://aistudio.google.com/apikey", target: "_blank", rel: "noopener noreferrer", text: "aistudio.google.com/apikey" }),
        ". Several keys, comma-separated, give more free quota."),
      fields("GEMINI_KEYS"), testBox("gemini")),
    h("section", { class: "card", id: "group-home_assistant" },
      h("h2", { text: "3. Home Assistant (optional)" }),
      h("p", { class: "desc", text: "For lights, switches, shopping lists and calendars. Create a token in Home Assistant under your profile → Security → Long-lived access tokens." }),
      fields("HA_URL", "HA_TOKEN"), testBox("home_assistant")),
    h("section", { class: "card" },
      h("h2", { text: "4. About you (optional)" }),
      h("p", { class: "desc", text: ob.learner_on
        ? "The learner turns your answers into the first entries of the personality and of what Jarvis knows about you. This uses the Gemini key and takes about a minute after Jarvis has started."
        : "The learner is switched off (Settings → Learner), so these answers would not be processed." }),
      ob.pending ? h("div", { class: "notice info" }, h("p", { text: `Answers from ${ob.pending.time} are still waiting for the learner.` })) : null,
      questions),
    h("section", { class: "card" }, err, h("div", { class: "row" }, go, h("a", { class: "btn", href: "#status", text: "Skip" }))));
}

async function finishSetup(answers, btn, err) {
  err.textContent = "";
  const gem = findItem("GEMINI_KEYS");
  if (!gem.set && typeof S.changes.GEMINI_KEYS !== "string") {
    err.textContent = "Enter a Gemini API key first.";
    S.fields.GEMINI_KEYS?.el.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  btn.disabled = true;
  const steps = [];
  try {
    if (dirty()) {
      try {
        await api("PUT", "/api/settings", { changes: S.changes });
      } catch (e) {
        if (e.data?.errors) {
          for (const [key, msg] of Object.entries(e.data.errors)) if (S.fields[key]) S.fields[key].err.textContent = msg;
        }
        throw e;
      }
      S.changes = {};
    }
    const filled = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()));
    if (Object.keys(filled).length) {
      await api("POST", "/api/onboarding", { answers: filled });
      steps.push("learn");
    }
    const st = await api("GET", "/api/status");
    S.status = st;
    const since = Date.now() / 1000;
    if (!st.jarvis.alive || st.jarvis.state !== "unconfigured") {
      if (st.service.installed === false) throw new Error("Saved. jarvis.service is not installed here: start Jarvis yourself (python main.py).");
      await api("POST", "/api/restart", { when: "idle" });
    }
    setupProgress(since, steps.includes("learn"));
  } catch (e) {
    if (e.status !== 401) err.textContent = e.message;
    btn.disabled = false;
  }
}

function setupProgress(since, learn) {
  const wake = "the wake word";
  const items = [
    { id: "start", text: "Starting Jarvis (about a minute on a Pi Zero)" },
    ...(learn ? [{ id: "learn", text: "Learning about you" }] : []),
  ];
  const list = h("ul", { class: "list" });
  const done = h("div", { hidden: true, class: "notice info" },
    h("p", {}, h("strong", { text: "Ready. " }), `Say ${wake} ("Hey Jarvis" unless you changed the wake word model) and talk to it.`),
    h("div", { class: "row" }, h("a", { class: "btn", href: "#workspace", text: "Look at the workspace" }),
      h("a", { class: "btn", href: "#status", text: "Status" })));
  $("#view").replaceChildren(h("section", { class: "card" }, h("h2", { text: "Almost done" }), list, done));
  const state = { start: false, learn: !learn };
  const draw = () => list.replaceChildren(...items.map((i) => h("li", {},
    h("span", { class: "dot " + (state[i.id] ? "ok" : "warn") }), i.text, state[i.id] ? h("span", { class: "muted small", text: "done" }) : null)));
  draw();
  clearInterval(S.setupTimer);
  S.setupTimer = setInterval(async () => {
    if (S.view !== "setup") { clearInterval(S.setupTimer); return; }
    try {
      const st = await api("GET", "/api/status");
      const j = st.jarvis;
      state.start = state.start || (j.alive && j.state === "idle" && j.started >= since - 5);
      if (state.start && !state.learn) {
        const ob = await api("GET", "/api/onboarding");
        state.learn = !ob.pending && ob.learner !== "onboarding";
      }
      draw();
      if (state.start && state.learn) { clearInterval(S.setupTimer); done.hidden = false; }
    } catch (e) { /* keep polling */ }
  }, 2500);
}

start();

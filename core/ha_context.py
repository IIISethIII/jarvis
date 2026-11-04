"""Home Assistant context for the Live model: a compact overview in the prompt, details on request.

The overview has one line per entity with its state and only short attributes. A trailing '+' marks
entities with more attributes (forecasts, menus, lists); the tool get_ha_details returns them.
If the overview exceeds JARVIS_HA_CONTEXT_CHARS it drops attributes first, then the read-only entities,
which stay reachable through a search.
"""
import json
import os

# never shown: internal or noisy entities
SKIP_DOMAINS = {"automation", "script", "zone", "sun", "update", "button", "device_tracker", "event",
                "conversation", "tts", "stt", "ai_task", "notify"}
# admin and system services (shutdown, backups, reloads) are never offered
SKIP_SERVICE_DOMAINS = {"homeassistant", "persistent_notification", "system_log", "ffmpeg", "profiler",
                        "recorder", "logbook", "shopping_list", "tts", "hassio", "cloud", "logger", "frontend",
                        "template", "rest", "command_line", "universal", "mqtt", "group", "db_info", "update",
                        "person", "zone", "backup", "conversation", "device_tracker"}
SKIP_SERVICES = {"reload"}
# entities that can be controlled; kept when the overview has to shrink
CONTROL_DOMAINS = {"light", "switch", "cover", "climate", "media_player", "fan", "lock", "vacuum", "scene",
                   "humidifier", "water_heater", "valve", "lawn_mower", "select", "number", "todo",
                   "input_boolean", "input_select", "input_number", "input_text", "input_button", "input_datetime"}
# attributes that say nothing to the model (the unit is appended to the state instead)
NOISE_ATTRS = {"friendly_name", "icon", "entity_picture", "supported_features", "options", "restored",
               "attribution", "device_class", "state_class", "unit_of_measurement", "editable", "id",
               "assumed_state", "supported_color_modes", "color_mode", "min_mireds", "max_mireds",
               "min_color_temp_kelvin", "max_color_temp_kelvin", "last_reset", "entity_id"}
# domain -> (domain, service, extra data, label) whose response get_ha_details adds; the label marks
# the line ("+forecast"), so Jarvis sees in the overview which data Home Assistant has
RESPONSE_SERVICES = {
    "todo": ("todo", "get_items", {"status": "needs_action"}, "items"),
    "weather": ("weather", "get_forecasts", {"type": "daily"}, "forecast"),
    "calendar": ("calendar", "get_events", {"duration": {"days": 7}}, "events"),
}
SHORT_ATTR = 30     # longer attribute values go behind '+'
MAX_SHOWN_ATTRS = 4
MAX_STATE = 60
DETAILS_CHARS = 20000  # per get_ha_details call
MAX_MATCHES = 40


def _budget():
    return int(os.getenv("JARVIS_HA_CONTEXT_CHARS", "30000"))


def _client():
    from core.tools import ha_client
    return ha_client


def _name(s):
    return s["attributes"].get("friendly_name", s["entity_id"])


def _state(s):
    state = str(s["state"])
    if len(state) > MAX_STATE:
        state = state[:MAX_STATE] + "…"
    unit = s["attributes"].get("unit_of_measurement")
    return f"{state} {unit}" if unit else state


def _line(s, with_attrs=True):
    extra = {k: v for k, v in s["attributes"].items() if k not in NOISE_ATTRS}
    shown = {k: v for k, v in extra.items()
             if isinstance(v, (str, int, float, bool)) and len(str(v)) <= SHORT_ATTR}
    shown = dict(list(shown.items())[:MAX_SHOWN_ATTRS]) if with_attrs else {}
    line = f"- {_name(s)} ({s['entity_id']}): {_state(s)}"
    if shown:
        line += " [" + ", ".join(f"{k}={v}" for k, v in shown.items()) + "]"
    response = RESPONSE_SERVICES.get(s["entity_id"].split(".")[0])
    if response:
        line += f" +{response[3]}"
    elif len(extra) > len(shown):
        line += " +"
    return line


def _areas():
    """entity_id -> area name, via the template API (empty if HA has no areas or the call fails)."""
    template = ("{% set ns = namespace(m={}) %}{% for s in states %}{% set a = area_name(s.entity_id) %}"
                "{% if a %}{% set ns.m = dict(ns.m, **{s.entity_id: a}) %}{% endif %}{% endfor %}{{ ns.m | tojson }}")
    ha = _client()
    try:
        r = ha.session.post(f"{ha.ha_url}/api/template", json={"template": template}, timeout=10)
        r.raise_for_status()
        return json.loads(r.text)
    except Exception as e:
        print(f"[HA] Areas unavailable: {e}", flush=True)
        return {}


def _visible(states):
    return [s for s in states if s["entity_id"].split(".")[0] not in SKIP_DOMAINS]


def _render(states, areas, with_attrs):
    groups = {}
    for s in sorted(states, key=lambda s: s["entity_id"]):
        groups.setdefault(areas.get(s["entity_id"], ""), []).append(_line(s, with_attrs))
    if list(groups) == [""]:
        return "\n".join(groups[""])
    parts = [f"{area}:\n" + "\n".join(lines) for area, lines in sorted(groups.items()) if area]
    if "" in groups:
        parts.append("No room:\n" + "\n".join(groups[""]))
    return "\n\n".join(parts)


def devices_overview(states=None, areas=None):
    states = _visible(states if states is not None else _client().fetch_all_states())
    if not states:
        return ""
    areas = _areas() if areas is None else areas
    budget = _budget()
    text = _render(states, areas, with_attrs=True)
    if len(text) <= budget:
        return text
    text = _render(states, areas, with_attrs=False)
    if len(text) <= budget:
        return text
    control = [s for s in states if s["entity_id"].split(".")[0] in CONTROL_DOMAINS]
    hidden = len(states) - len(control)
    text = _render(control, areas, with_attrs=False)
    if len(text) > budget:
        text = text[:budget].rsplit("\n", 1)[0] + "\n- … (list cut, search with get_ha_details)"
    return text + f"\n\n({hidden} sensors and other read-only entities are not listed: find them with get_ha_details(query=...).)"


def services_overview(services=None):
    services = services if services is not None else _client().fetch_all_services()
    lines = []
    for d in services:
        names = [n for n in d.get("services", {}) if n not in SKIP_SERVICES]
        if d["domain"] not in SKIP_SERVICE_DOMAINS and names:
            lines.append(f"- {d['domain']}: {', '.join(names)}")
    return "\n".join(sorted(lines))


def prompt_section():
    devices = devices_overview()
    out = ""
    if devices:
        out += ("\n=== SMART HOME DEVICES (state at session start; '+' = more details, '+forecast' / '+items' / "
                "'+events' = full forecast, list items, upcoming events; all via get_ha_details) ===\n"
                f"{devices}\n")
    services = services_overview()
    if services:
        out += ("\n=== SMART HOME SERVICES (domain: services; parameters via get_ha_details(service_domain=...)) ===\n"
                f"{services}\n")
    return out


def _strip_ids(obj):
    """Drops item uids from service responses; they only cost tokens."""
    if isinstance(obj, dict):
        return {k: _strip_ids(v) for k, v in obj.items() if k != "uid"}
    if isinstance(obj, list):
        return [_strip_ids(v) for v in obj]
    return obj


def details(entity_ids=None, query="", service_domain=""):
    """Current state with all attributes, a search over all entities, or the parameters of a domain's services."""
    ha = _client()
    out = []
    for eid in entity_ids or []:
        s = ha.get_state(eid)
        if not s:
            out.append(f"{eid}: not found")
            continue
        attrs = {k: v for k, v in s["attributes"].items() if k not in ("icon", "entity_picture", "supported_features")}
        # some data is not in the attributes but behind a service with a response
        call = RESPONSE_SERVICES.get(eid.split(".")[0])
        if call:
            response = ha.call_service_response(call[0], call[1], {"entity_id": eid, **call[2]})
            if response:
                attrs["data"] = _strip_ids(response.get(eid, response))
        out.append(f"{eid}: {_state(s)} (changed {s.get('last_changed', '?')[:19]})\n"
                   + json.dumps(attrs, ensure_ascii=False, separators=(",", ":")))
    if query:
        q = query.lower()
        hits = [s for s in ha.fetch_all_states() if q in s["entity_id"].lower() or q in _name(s).lower()]
        lines = [_line(s) for s in hits[:MAX_MATCHES]]
        if len(hits) > MAX_MATCHES:
            lines.append(f"... {len(hits) - MAX_MATCHES} more, search more precisely")
        out.append(f"Entities matching '{query}':\n" + ("\n".join(lines) or "none"))
    if service_domain:
        found = [d for d in ha.fetch_all_services() if d["domain"] == service_domain]
        if not found:
            out.append(f"No services in domain '{service_domain}'")
        for d in found:
            lines = []
            for name, srv in d.get("services", {}).items():
                fields = []
                flat = {}
                for f, meta in (srv.get("fields") or {}).items():
                    # sections (e.g. advanced_fields) group further fields
                    flat.update(meta["fields"] if isinstance(meta, dict) and "fields" in meta else {f: meta})
                for f, meta in flat.items():
                    if not isinstance(meta, dict):
                        continue
                    desc = (meta.get("description") or "")[:80]
                    fields.append(f"{f}{'*' if meta.get('required') else ''}" + (f" ({desc})" if desc else ""))
                lines.append(f"- {name}: " + ("; ".join(fields) or "no parameters"))
            out.append(f"Services of {service_domain} (* = required):\n" + "\n".join(lines))
    if not out:
        return "Error: give entity_ids, query or service_domain."
    text = "\n\n".join(out)
    if len(text) > DETAILS_CHARS:
        text = text[:DETAILS_CHARS] + "\n… (cut)"
    return text


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    text = prompt_section()
    print(text)
    print(f"[HA] {len(text)} chars", flush=True)

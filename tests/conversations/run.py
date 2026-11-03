"""Conversation tests: text sessions against the real prompt and Live model, without the Pi speaking.

  venv/bin/python -m tests.conversations.run tests/conversations/examples/
  venv/bin/python -m tests.conversations.run my.json --workspace data/workspace --keep /tmp/ws-out

Each scenario runs in a throwaway data directory (archive and workspace), starting from a copy of --workspace
or from the templates. Tools with side effects are stubbed; remember, load_skills, search_archive, get_ha_details and
end_conversation run for real. After every session the learner runs like in production, then the checks.
Needs network, the Gemini keys from .env and (by default) Home Assistant and the MCP servers for the tool list.

Scenario file (JSON):
{
  "name": "...",
  "import": ["facts.md"],                 optional, paths relative to the scenario file, imported first
  "after_import": {CHECKS},
  "stubs": {"ask_openclaw": "Left machine is free."},  results for stubbed tools (also per session)
  "sessions": [
    {"turns": ["Merk dir ...", "..."], "expect": {EXPECT}, "after": {CHECKS}, "no_learn": false}
  ]
}
EXPECT: "tools": [names that must be called], "no_tools": [names that must not be called],
        "tool_args": [{"tool": name, "match": regex}], "answer_match": regex, "silent": true
CHECKS: {"MEMORY": {"match": [regex], "no_match": [regex]}, "SKILLS": {...}, "ANY": {...}}
        (regexes are case-insensitive; SKILLS = all skill files, ANY = all files)
"""
import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STUBS = {
    "call_ha_service": "Success",
    "music_play": "OK, playing.",
    "music_control": "OK.",
    "manage_timer": "OK (test).",
    "set_speaker_volume": "Volume set.",
    "get_pi_temperature": "Pi Temperature: temp=45.0'C",
    "ask_openclaw": "Test: no answer available in this test.",
}
REAL = {"remember", "load_skills", "search_archive", "get_ha_details", "end_conversation"}
TURN_TIMEOUT = 90
QUIET_TIMEOUT = 25  # no message at all for this long = the model stays silent


def check_files(checks, label):
    from core import workspace
    failures = []
    texts = {name: workspace.read_file(name) for name in workspace.FILES}
    texts["SKILLS"] = "\n".join(s["text"] for s in workspace.list_skills())
    texts["ANY"] = "\n".join(texts.values())
    for name, rule in (checks or {}).items():
        text = texts.get(name.upper(), "")
        for rx in rule.get("match", []):
            if not re.search(rx, text, re.I):
                failures.append(f"{label}: {name} should match /{rx}/")
        for rx in rule.get("no_match", []):
            if re.search(rx, text, re.I):
                failures.append(f"{label}: {name} must not match /{rx}/")
    return failures


def check_session(expect, calls, answer, label):
    failures = []
    names = [c["name"] for c in calls]
    for t in expect.get("tools", []):
        if t not in names:
            failures.append(f"{label}: tool {t} not called (called: {names})")
    for t in expect.get("no_tools", []):
        if t in names:
            failures.append(f"{label}: tool {t} must not be called")
    for rule in expect.get("tool_args", []):
        args = [json.dumps(c["args"], ensure_ascii=False) for c in calls if c["name"] == rule["tool"]]
        if not any(re.search(rule["match"], a, re.I) for a in args):
            failures.append(f"{label}: no {rule['tool']} call matching /{rule['match']}/ (args: {args})")
    if expect.get("answer_match") and not re.search(expect["answer_match"], answer, re.I):
        failures.append(f"{label}: answer should match /{expect['answer_match']}/")
    if expect.get("silent") and answer.strip():
        failures.append(f"{label}: should stay silent, said {answer!r}")
    return failures


async def run_session(client, model, config, turns, stubs, session_id, log):
    """Sends the turns as text, answers tool calls, returns (tool calls, spoken answer)."""
    from google.genai import types
    from core import memory, tools
    calls, spoken = [], []
    async with client.aio.live.connect(model=model, config=config) as session:
        for text in turns:
            log(f"  User: {text}")
            await session.send_client_content(turns=[types.Content(role="user", parts=[types.Part.from_text(text=text)])],
                                              turn_complete=True)
            answer, turn_calls, ended = await _receive_turn(session, stubs, session_id, log)
            calls += turn_calls
            spoken.append(answer)
            memory.log_interaction(text, answer, session_id, [memory.tool_entry(c["name"], c["args"], c["result"]) for c in turn_calls])
            if ended:
                log("  (conversation ended)")
                break
    return calls, " ".join(spoken)


async def _receive_turn(session, stubs, session_id, log):
    from google.genai import types
    from core import tools
    parts, calls, ended = [], [], False
    pending_answer = False  # a tool response was sent: the model answers in a further turn
    start = time.time()
    it = session.receive().__aiter__()
    while time.time() - start < TURN_TIMEOUT:
        try:
            response = await asyncio.wait_for(it.__anext__(), QUIET_TIMEOUT)
        except (asyncio.TimeoutError, StopAsyncIteration):
            if pending_answer:
                it = session.receive().__aiter__()
                pending_answer = False
                continue
            break
        sc = getattr(response, "server_content", None)
        if sc is not None:
            ot = getattr(sc, "output_transcription", None)
            if ot is not None and getattr(ot, "text", None):
                parts.append(ot.text)
            if getattr(sc, "turn_complete", None):
                if pending_answer:
                    pending_answer = False
                    it = session.receive().__aiter__()
                    continue
                break
        tc = getattr(response, "tool_call", None)
        if tc is not None:
            responses = []
            for fc in tc.function_calls:
                args = dict(fc.args or {})
                if fc.name in REAL:
                    if fc.name == "remember":
                        args["session_id"] = session_id
                    result = tools.execute_tool(fc.name, args)
                    args.pop("session_id", None)
                else:
                    result = stubs.get(fc.name, STUBS.get(fc.name, "No data (test stub)."))
                log(f"  tool {fc.name}({json.dumps(args, ensure_ascii=False)[:200]}) -> {str(result)[:120]}")
                calls.append({"name": fc.name, "args": args, "result": result})
                if result == "<END_CONVERSATION>":
                    ended = True
                responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
            if ended:
                break
            await session.send_tool_response(function_responses=responses)
            pending_answer = True
    answer = "".join(parts).strip()
    log(f"  Jarvis: {answer or '(silent)'}")
    return answer, calls, ended


def run_scenario(path, args, log):
    from google import genai
    from core import learner, llm, memory, prompt, tools, workspace
    with open(path, encoding="utf-8") as f:
        sc = json.load(f)
    failures = []
    base = os.path.dirname(os.path.abspath(path))
    for imp in sc.get("import", []):
        from scripts.import_facts import import_text
        log(f"  import {imp}")
        import_text(open(os.path.join(base, imp), encoding="utf-8").read(), source=f"import {imp}")
    failures += check_files(sc.get("after_import"), "after import")

    declarations = list(tools.FUNCTION_DECLARATIONS) + args.dynamic_tools
    keys = learner._api_keys()
    model = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
    stubs = sc.get("stubs", {})
    for i, s in enumerate(sc.get("sessions", []), 1):
        label = f"session {i}"
        system_prompt = prompt.build({d.name for d in declarations}, include_ha=not args.no_ha)
        config = llm.live_config(system_prompt, declarations)
        session_id = time.strftime("%Y%m%d-%H%M%S") + f"-{i}"
        log(f" {label} ({len(system_prompt)} prompt chars)")
        calls, answer = None, ""
        for attempt, key in enumerate(keys):
            try:
                calls, answer = asyncio.run(run_session(genai.Client(api_key=key), model, config, s["turns"],
                                                        {**stubs, **s.get("stubs", {})}, session_id, log))
                break
            except Exception as e:
                log(f"  Live error with key {attempt + 1}: {e}")
        if calls is None:
            failures.append(f"{label}: Live session failed")
            continue
        failures += check_session(s.get("expect", {}), calls, answer, label)
        if not s.get("no_learn"):
            sessions = [(sid, t) for sid, t in learner._sessions_from_archive() if sid == session_id]
            try:
                for line in learner.learn_from(sessions, workspace.read_notes()):
                    log(f"  learned: {line}")
            except Exception as e:
                failures.append(f"{label}: learner failed: {e}")
        failures += check_files(s.get("after"), f"{label} after learning")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="scenario files or folders")
    parser.add_argument("--workspace", help="start every scenario from a copy of this workspace (default: templates)")
    parser.add_argument("--keep", help="copy the final workspace of the last scenario here")
    parser.add_argument("--chain", action="store_true", help="scenarios share one workspace, in the given order")
    parser.add_argument("--no-ha", action="store_true", help="leave out the Home Assistant devices")
    parser.add_argument("--no-mcp", action="store_true", help="don't load the MCP tool list")
    parser.add_argument("--learner-models", help="comma-separated, overrides JARVIS_LEARNER_MODELS")
    args = parser.parse_args()

    files = []
    for p in args.paths:
        files += sorted(glob.glob(os.path.join(p, "*.json"))) if os.path.isdir(p) else [p]

    data_dir = tempfile.mkdtemp(prefix="jarvis-conv-")
    os.environ["JARVIS_DATA_DIR"] = data_dir  # before any core import: archive and workspace go here
    os.environ["JARVIS_EMBEDDINGS"] = "0"
    sys.path.insert(0, ROOT)
    from core import learner, workspace
    if args.learner_models:
        learner.MODELS[:] = [m.strip() for m in args.learner_models.split(",")]

    args.dynamic_tools = []
    if not args.no_mcp:
        from core.mcp import mcp_client
        mcp_client.start_sync()
        args.dynamic_tools = mcp_client.get_gemini_tools_sync()

    def reset():
        shutil.rmtree(data_dir, ignore_errors=True)
        os.makedirs(data_dir)
        open(os.path.join(data_dir, "archive.jsonl"), "w").close()
        if args.workspace:
            shutil.copytree(args.workspace, workspace.root(), ignore=shutil.ignore_patterns(".history", ".inbox.jsonl", ".learner.json"))
        workspace.ensure()

    total = 0
    reset()
    for i, path in enumerate(files):
        if i and not args.chain:
            reset()
        print(f"\n=== {path}", flush=True)
        failures = run_scenario(path, args, lambda m: print(m, flush=True))
        total += len(failures)
        for f in failures:
            print(f"  FAIL {f}", flush=True)
        print(f"  {'PASS' if not failures else f'{len(failures)} failure(s)'}", flush=True)
    if args.keep:
        shutil.rmtree(args.keep, ignore_errors=True)
        shutil.copytree(workspace.root(), args.keep)
        print(f"\nWorkspace kept in {args.keep}")
    print(f"\n{len(files)} scenario(s), {total} failure(s). Data dir: {data_dir}")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()

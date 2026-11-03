"""Builds the system prompt for one Live session.

Order: core rules (prompts/core.md) -> personality, house rules (workspace) -> guides for the loaded tools
(prompts/guides/) -> skill list -> user, memory (workspace) -> live context (recent conversations, timers, HA).
Everything is read per session, so edits take effect at the next wake word.
"""
import datetime
import os

from core import workspace

PROMPTS_DIR = os.path.join(workspace.BASE_DIR, "prompts")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class _Vars(dict):
    """format_map() dict that leaves unknown {placeholders} as they are."""
    def __missing__(self, key):
        return "{" + key + "}"


def assistant_name():
    return os.getenv("JARVIS_NAME", "Jarvis")


def language():
    return os.getenv("JARVIS_LANGUAGE", "English")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(text, **extra):
    from core import openclaw
    values = _Vars(assistant_name=assistant_name(), language=language(),
                   openclaw_name=openclaw.NAME, OPENCLAW_NAME=openclaw.NAME.upper(), **extra)
    return text.format_map(values)


def guides(tool_names):
    """Guides whose 'requires' tools are all loaded, rendered."""
    folder = os.path.join(PROMPTS_DIR, "guides")
    parts = []
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith(".md"):
            continue
        meta, body = workspace._parse_skill(_read(os.path.join(folder, fname)))
        required = [t.strip() for t in meta.get("requires", "").split(",") if t.strip()]
        if all(t in tool_names for t in required):
            parts.append(render(body.strip()))
    return parts


def skills_section():
    skills = workspace.list_skills()
    if not skills:
        return ""
    lines = ["=== SKILLS (load with 'load_skills' before you act) ==="]
    always = []
    for s in skills:
        if s["always"]:
            always.append(f"=== Skill {s['name']} ===\n{s['body']}")
        else:
            lines.append(f"- {s['name']}: {s['description']}")
    text = "\n".join(lines) if len(lines) > 1 else ""
    return "\n\n".join(p for p in [text] + always if p)


def static_prompt(tool_names, now=None):
    """Everything except the live context: rules, workspace files, guides, skills."""
    now = now or datetime.datetime.now()
    time_str = f"{WEEKDAYS[now.weekday()]}, {now.strftime('%Y-%m-%d %H:%M:%S')}"
    workspace.ensure()
    sections = [render(_read(os.path.join(PROMPTS_DIR, "core.md")).strip(), time=time_str)]
    for name, title in (("SOUL", "PERSONALITY"), ("AGENTS", "HOUSE RULES")):
        text = workspace.prompt_text(name)
        if _has_entries(text):
            sections.append(f"=== {title} ===\n{text}")
    sections += ["=== GUIDES ==="] + guides(tool_names)
    skills = skills_section()
    if skills:
        sections.append(skills)
    for name, title in (("USER", "USER"), ("MEMORY", "MEMORY")):
        text = workspace.prompt_text(name)
        if _has_entries(text):
            sections.append(f"=== {title} ===\n{text}")
    learned = workspace.recent_changes()
    if learned:
        sections.append("=== RECENTLY LEARNED (changes to your files, newest last) ===\n" + "\n".join(f"- {l}" for l in learned))
    return "\n\n".join(sections) + "\n"


def _has_entries(text):
    """False for a file that is only a heading (fresh template)."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines())


def live_context(include_ha=True):
    """Recent conversations, active timers and the Home Assistant devices and services."""
    from core import memory
    out = ""
    recent = memory.get_recent_conversations()
    if recent:
        out += (
            "\n=== RECENT CONVERSATIONS (just now, chronological) ===\n"
            "Use this only as context when the user picks up on it (e.g. \"and tomorrow?\", \"how many people live there?\"). "
            "Don't bring it up yourself and don't repeat it.\n"
            f"{recent}\n"
        )

    from core.timers import timer_manager
    active_timers = timer_manager.describe()
    if active_timers:
        out += f"\n=== ACTIVE TIMERS & ALARMS (as of session start) ===\n{active_timers}\n"

    if include_ha:
        try:
            from core import ha_context
            out += ha_context.prompt_section()
        except Exception as e:
            print(f"[Prompt] Failed to fetch HA context: {e}", flush=True)
    return out


def build(tool_names, include_ha=True):
    return static_prompt(tool_names) + live_context(include_ha)

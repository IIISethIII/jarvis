import os
import re
import json
import base64
import hashlib
from datetime import datetime

# Wir speichern die Daten lokal im /data Ordner (oder wo immer V3 läuft)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
CORE_MEMORY_FILE = os.path.join(DATA_DIR, "core_memory.md")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.jsonl")
SEARCH_WINDOW_BYTES = 4 * 1024 * 1024  # newest part of the archive that search_archive scans
CONTEXT_WINDOW_BYTES = 128 * 1024  # newest part of the archive scanned for recent sessions
VECTORS_FILE = os.path.join(DATA_DIR, "archive_vectors.jsonl")
VECTORS_WINDOW_BYTES = 4 * 1024 * 1024  # ~5000 newest entries at 256 dims; keeps RAM small on the Pi
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIMS = 256
SEMANTIC_MIN_SCORE = 0.70  # cosine similarity; unrelated German snippets score ~0.63-0.68 with this model

def _ensure_files():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    if not os.path.exists(CORE_MEMORY_FILE):
        with open(CORE_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write("# Jarvis Core Memory\n\nThis file contains fundamental facts about the user.\n")
            
    if not os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            pass

_ensure_files()

def get_core_memory() -> str:
    """Reads the core memory markdown file and returns it."""
    try:
        with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading core memory: {e}"

def edit_core_memory(action: str, fact: str, replace_text: str = "") -> str:
    """
    Edits the core memory file.
    action: 'append' or 'replace'
    fact: the new fact to append, or the exact text to be replaced
    replace_text: if action is 'replace', the text to replace the fact with
    """
    try:
        if action == "append":
            with open(CORE_MEMORY_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n- {fact}")
            return f"Successfully appended fact: {fact}"
            
        elif action == "replace":
            with open(CORE_MEMORY_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                
            if fact not in content:
                return f"Error: Could not find exactly '{fact}' in the core memory."
                
            new_content = content.replace(fact, replace_text)
            
            with open(CORE_MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"Successfully replaced fact."
            
        else:
            return "Error: Action must be 'append' or 'replace'"
            
    except Exception as e:
        return f"Error editing core memory: {e}"

def log_interaction(user_text: str, assistant_text: str, session_id: str = ""):
    """Appends a conversation turn to the archive.jsonl log. session_id groups the turns of one wake-word session."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = {
        "timestamp": now_str,
        "user": user_text,
        "assistant": assistant_text
    }
    if session_id:
        entry["session"] = session_id
    try:
        with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Memory] Failed to log interaction: {e}")
        return
    _store_embeddings([entry])

# --- Semantic search -------------------------------------------------------
# Embeddings come from the Gemini API (no local model, the Pi is too small) and are
# stored next to the archive in archive_vectors.jsonl, keyed by a hash of the entry.
# Entries without a vector are still found by keyword.

def _embeddings_enabled() -> bool:
    return os.getenv("JARVIS_EMBEDDINGS", "1") != "0" and bool(_api_keys())

def _api_keys() -> list:
    keys_env = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY") or ""
    return [k.strip() for k in keys_env.split(",") if k.strip()]

def _entry_hash(entry: dict) -> str:
    raw = f"{entry.get('timestamp', '')}|{entry.get('session', '')}|{entry.get('user', '')}|{entry.get('assistant', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def _entry_text(entry: dict) -> str:
    return f"User: {entry.get('user', '')}\nJarvis: {entry.get('assistant', '')}"

def _embed(texts: list, task_type: str):
    """Embeds texts with the Gemini API. Returns a normalized float32 matrix (len(texts) x EMBED_DIMS) or None on failure."""
    import numpy as np
    import requests
    model = os.getenv("JARVIS_EMBED_MODEL", EMBED_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    body = {"requests": [{
        "model": f"models/{model}",
        "content": {"parts": [{"text": t}]},
        "taskType": task_type,
        "outputDimensionality": EMBED_DIMS,
    } for t in texts]}
    for key in _api_keys():
        try:
            r = requests.post(url, params={"key": key}, json=body, timeout=10)
            if r.status_code == 200:
                m = np.array([e["values"] for e in r.json()["embeddings"]], dtype=np.float32)
                return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
            print(f"[Memory] Embedding request failed: HTTP {r.status_code}", flush=True)
        except Exception as e:
            print(f"[Memory] Embedding request failed: {e}", flush=True)
    return None

def _store_embeddings(entries: list) -> int:
    """Embeds archive entries and appends the vectors to archive_vectors.jsonl. Failures are non-fatal."""
    if not entries or not _embeddings_enabled():
        return 0
    vectors = _embed([_entry_text(e) for e in entries], "RETRIEVAL_DOCUMENT")
    if vectors is None:
        return 0
    try:
        with open(VECTORS_FILE, "a", encoding="utf-8") as f:
            for entry, vec in zip(entries, vectors):
                b64 = base64.b64encode(vec.astype("float16").tobytes()).decode("ascii")
                f.write(json.dumps({"h": _entry_hash(entry), "v": b64}) + "\n")
    except Exception as e:
        print(f"[Memory] Failed to store embeddings: {e}", flush=True)
        return 0
    return len(entries)

def _load_vectors() -> dict:
    """Loads the newest vectors as {entry hash: float32 array}."""
    import numpy as np
    vectors = {}
    try:
        with open(VECTORS_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - VECTORS_WINDOW_BYTES))
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        if size > VECTORS_WINDOW_BYTES:
            lines = lines[1:]
        for line in lines:
            try:
                row = json.loads(line)
                vectors[row["h"]] = np.frombuffer(base64.b64decode(row["v"]), dtype=np.float16).astype(np.float32)
            except (ValueError, KeyError):
                continue
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Memory] Failed to load vectors: {e}", flush=True)
    return vectors

def _read_entries(window_bytes: int) -> list:
    """Returns the newest archive entries (oldest first). Reads only the tail of the file."""
    with open(ARCHIVE_FILE, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - window_bytes))
        data = f.read().decode("utf-8", errors="ignore")
    lines = data.splitlines()
    if size > window_bytes:
        lines = lines[1:]  # first line is probably cut off
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries

def backfill_embeddings(batch_size: int = 50) -> int:
    """Embeds all archive entries that have no vector yet (e.g. entries written before semantic search). Returns the count."""
    if not _embeddings_enabled():
        print("[Memory] Embeddings are disabled or no API key is set.", flush=True)
        return 0
    entries = _read_entries(SEARCH_WINDOW_BYTES)
    have = set(_load_vectors())
    missing = [e for e in entries if _entry_hash(e) not in have and (e.get("user") or e.get("assistant"))]
    done = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        done += _store_embeddings(batch)
        print(f"[Memory] Embedded {done}/{len(missing)}", flush=True)
    return done

def get_recent_conversations(max_sessions: int = None, max_age_min: int = None) -> str:
    """
    Returns the turns of the last few sessions as prompt text (oldest first), so a new
    session can pick up what was just discussed. Sessions whose last turn is older than
    max_age_min are dropped. Entries without a session id (older archive lines) are ignored.
    """
    if max_sessions is None:
        max_sessions = int(os.getenv("JARVIS_CONTEXT_SESSIONS", "3"))
    if max_age_min is None:
        max_age_min = int(os.getenv("JARVIS_CONTEXT_MAX_AGE_MIN", "30"))
    if max_sessions <= 0:
        return ""

    try:
        with open(ARCHIVE_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - CONTEXT_WINDOW_BYTES))
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        if size > CONTEXT_WINDOW_BYTES:
            lines = lines[1:]  # first line is probably cut off
    except Exception as e:
        print(f"[Memory] Failed to read recent conversations: {e}", flush=True)
        return ""

    sessions = {}  # session id -> turns; dicts keep insertion order = chronological
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = entry.get("session")
        if sid:
            sessions.setdefault(sid, []).append(entry)

    now = datetime.now()
    blocks = []
    for turns in list(sessions.values())[-max_sessions:]:
        try:
            last = datetime.strptime(turns[-1]["timestamp"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if (now - last).total_seconds() > max_age_min * 60:
            continue
        rows = []
        for t in turns:
            if t.get("user"):
                rows.append(f"User: {t['user']}")
            if t.get("assistant"):
                rows.append(f"Jarvis: {t['assistant']}")
        if rows:
            blocks.append(f"[{turns[0]['timestamp']}]\n" + "\n".join(rows))
    return "\n\n".join(blocks)

def search_archive(query: str, max_results: int = 8) -> str:
    """
    Searches the archive by meaning (embeddings) and by keyword.
    - "*" or an empty query returns the newest entries.
    - A word like 2026-08-01 restricts the search to that day.
    - Other words are matched semantically; entries containing them literally get a bonus.
      Without an embedding API the search falls back to pure keyword matching.
    """
    day_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    words = [w for w in query.lower().split() if w.strip("*?")]
    days = [w for w in words if day_re.match(w)]
    words = [w for w in words if not day_re.match(w)]

    try:
        entries = _read_entries(SEARCH_WINDOW_BYTES)
    except Exception as e:
        return f"Error searching archive: {e}"
    entries.reverse()  # newest first
    if days:
        entries = [e for e in entries if e.get("timestamp", "")[:10] in days]

    scored = []  # (score, entry); sort is stable, so equal scores stay newest first
    if not words:
        scored = [(0.0, e) for e in entries]
        max_results = min(max_results, 10)
    else:
        qvec = None
        vectors = {}
        if _embeddings_enabled():
            q = _embed([" ".join(words)], "RETRIEVAL_QUERY")
            if q is not None:
                qvec = q[0]
                vectors = _load_vectors()
        for e in entries:
            haystack = f"{e.get('timestamp', '')} {e.get('user', '')} {e.get('assistant', '')}".lower()
            hits = sum(1 for w in words if w in haystack)
            vec = vectors.get(_entry_hash(e))
            sim = float(vec @ qvec) if vec is not None and qvec is not None else 0.0
            if hits == len(words) or sim >= SEMANTIC_MIN_SCORE:
                scored.append((sim + 0.3 * hits / len(words), e))
        scored.sort(key=lambda x: -x[0])

    results = [
        f"[{e.get('timestamp', '')}] User: {e.get('user', '')}\nJarvis: {e.get('assistant', '')}"
        for _, e in scored[:max_results]
    ]
    if not results:
        return f"No results found for '{query}' in the archive."
    return "Found the following past interactions:\n\n" + "\n\n".join(results)

import os
import json
from datetime import datetime

# Wir speichern die Daten lokal im /data Ordner (oder wo immer V3 läuft)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
CORE_MEMORY_FILE = os.path.join(DATA_DIR, "core_memory.md")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.jsonl")

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

def log_interaction(user_text: str, assistant_text: str):
    """Appends a conversation turn to the archive.jsonl log."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = {
        "timestamp": now_str,
        "user": user_text,
        "assistant": assistant_text
    }
    try:
        with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Memory] Failed to log interaction: {e}")

def search_archive(query: str, max_results: int = 20) -> str:
    """Searches the archive.jsonl file for a keyword or phrase."""
    results = []
    query_lower = query.lower()
    
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            # We read line by line. For massive files, this is memory efficient.
            # To get most recent, we might read all and reverse, but for now we just find matches.
            lines = f.readlines()
            # Reverse to get newest first
            for line in reversed(lines):
                if not line.strip(): continue
                
                try:
                    entry = json.loads(line)
                    user_text = entry.get("user", "")
                    assistant_text = entry.get("assistant", "")
                    
                    if query_lower in user_text.lower() or query_lower in assistant_text.lower():
                        results.append(f"[{entry['timestamp']}] User: {user_text}\nJarvis: {assistant_text}")
                        if len(results) >= max_results:
                            break
                except json.JSONDecodeError:
                    continue
                    
        if not results:
            return f"No results found for '{query}' in the archive."
            
        return "Found the following past interactions:\n\n" + "\n\n".join(results)
        
    except Exception as e:
        return f"Error searching archive: {e}"

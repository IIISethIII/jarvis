import os
import time
import threading
from jarvis import config
from jarvis.utils import session
from jarvis.services import routine
from mem0 import Memory

# --- INITIALIZE MEM0 ---
mem0_config = {
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "jarvis_memories",
            "path": config.MEM0_DB_DIR,
        }
    },
    "llm": {
        "provider": "litellm",
        "config": {
            "model": "gemini/gemini-2.5-flash",
            "temperature": 0.1,
            "max_tokens": 8000,
        }
    },
    "embedder": {
        "provider": "gemini",
        "config": {
            "model": "gemini-embedding-001",
        }
    }
}

try:
    memory_client = Memory.from_config(mem0_config)
except Exception as e:
    print(f" [Memory] Failed to initialize Mem0: {e}")
    memory_client = None

# --- HYBRID RETRIEVAL ---
def get_hybrid_context(query_text=None):
    """
    Rückgabe von Core Memory & Routines für den System Prompt.
    Episodic Memory (Archiv) wird NICHT mehr automatisch geladen.
    """
    if memory_client is None:
        return "Memory offline."

    core_content = ""
    try:
        # Hole alle Core-Fakten aus Mem0
        results = memory_client.get_all(user_id="paul_core")
        
        # Formatierung der Ergebnisse
        if isinstance(results, dict) and 'results' in results:
            results_list = results['results']
        elif isinstance(results, dict) and 'memories' in results:
            results_list = results['memories']
        else:
            results_list = results
            
        facts = []
        for res in results_list:
            if isinstance(res, dict):
                memory_text = res.get('memory', '')
            else:
                memory_text = getattr(res, 'memory', '')
            if memory_text:
                facts.append(f"- {memory_text}")
                
        if facts:
            core_content = "\n".join(facts)
        else:
            core_content = "Bisher keine Core-Fakten gespeichert."
    except Exception as e:
        print(f" [Memory] Core Fetch Error: {e}")
        core_content = "Fehler beim Laden der Core-Fakten."

    return f"""
    === CORE MEMORY & ROUTINES (ESTABLISHED FACTS) ===
    {core_content}
    """

# --- SAVING & DREAMING ---
def _async_add_memory(messages):
    """Background task to add memories to Mem0 without blocking the LLM."""
    if memory_client is None: return
    try:
        memory_client.add(messages, user_id="paul_archive")
    except Exception as e:
        print(f" [Memory] Async Add Error: {e}")

def save_interaction(user_text, assistant_text):
    """
    Saves the turn to Mem0 in real-time into the archive.
    Prepends exact timestamps so semantic search can find time-based queries.
    """
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    messages = [
        {"role": "user", "content": f"[{now_str}] {user_text}"},
        {"role": "assistant", "content": f"[{now_str}] {assistant_text}"}
    ]
    # Fire and forget in a background thread to prevent latency
    threading.Thread(target=_async_add_memory, args=(messages,), daemon=True).start()

def dream():
    """
    Called nightly.
    Analysiert den Tag via Routine Tracker und pusht die Erkenntnisse
    als Fakten/Gewohnheiten in das Mem0 "paul_core" Profil.
    Mem0 kümmert sich intern um das Update/Replacement älterer Gewohnheiten.
    """
    print(" [Memory] 🌙 Nightly Maintenance...")
    
    # Analyze Daily Routine
    print(" [Memory] 🕵️ Analyzing Daily Routine & Habits...")
    try:
        routine.tracker.analyze_routine()
        
        # Die generierten Erkenntnisse des Tages holen
        habits_summary = routine.tracker.get_habits_summary()
        
        if memory_client is not None and habits_summary:
            print(" [Memory] 🧠 Aktualisiere paul_core mit neuen Routinen...")
            # Mem0 nutzt LLMs im Hintergrund, um zu entscheiden, 
            # ob alte Gewohnheiten überschrieben oder neue hinzugefügt werden.
            memory_client.add(f"Aktuelle Routinen und Gewohnheiten: {habits_summary}", user_id="paul_core")
            print(" [Memory] ✅ Core Memory erfolgreich aktualisiert.")
            
    except Exception as e:
        print(f" [Memory] Routine Analysis Error: {e}")

# --- TOOL ADAPTERS (Für jarvis/core/tools.py) ---

def save_memory_tool(text):
    """
    Speichert einen expliziten Fakt (via Tool-Call) ins Archiv UND in die Core-Memories.
    Wichtig für Dinge, die Paul in der Zukunft dauerhaft wissen soll.
    """
    if memory_client is None:
        return "Fehler: Mem0 nicht initialisiert."
    
    try:
        # Synchrone Speicherung in die Core Memories
        memory_client.add(text, user_id="paul_core")
        return f"Wichtiger Fakt notiert: '{text}'."
    except Exception as e:
        return f"Fehler beim Speichern: {e}"

def search_memory_tool(search_query):
    """
    Durchsucht das Archiv (paul_archive). Nutzt semantische Textsuche, die auch 
    die angehängten Zeitstempel im Text findet.
    """
    if memory_client is None:
        return "Fehler: Mem0 nicht initialisiert."
    
    hits = []
    try:
        results = memory_client.search(query=search_query, user_id="paul_archive", limit=10)
        if isinstance(results, dict) and 'results' in results:
            results_list = results['results']
        elif isinstance(results, dict) and 'memories' in results:
            results_list = results['memories']
        else:
            results_list = results

        for res in results_list:
            if isinstance(res, dict):
                memory_text = res.get('memory', '')
            else:
                memory_text = getattr(res, 'memory', '')
            if memory_text:
                hits.append(f"- {memory_text}")
    except Exception as e:
        return f"Suchfehler: {e}"
        
    if not hits:
        return "Keine relevanten Einträge im Archiv gefunden."
        
    return "Gefundene Archiv-Einträge:\n" + "\n".join(hits)
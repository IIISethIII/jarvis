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
def get_hybrid_context(query_text):
    """
    Combines:
    1. CORE MEMORY (The 'BIOS' from core.md)
    2. RELEVANT RECALL (Mem0 Vector DB)
    """
    # 1. Load Core Memory (Always prioritized)
    core_content = ""
    if os.path.exists(config.CORE_FILE):
        with open(config.CORE_FILE, 'r', encoding='utf-8') as f:
            core_content = f.read()

    # 2. Vector Search (Mem0)
    rag_content = "No specific past details found."
    
    if query_text and memory_client is not None:
        try:
            # Mem0 search returns a list of dictionaries with 'memory', 'score', etc.
            # `memory_client.search()` can sometimes return a dictionary with a 'results' key or direct list.
            results = memory_client.search(query=query_text, user_id="paul", limit=10)
            if isinstance(results, dict) and 'results' in results:
                results_list = results['results']
            elif isinstance(results, dict) and 'memories' in results:
                results_list = results['memories']
            else:
                results_list = results
                
            hits = []
            for res in results_list:
                # Handle cases where res is a dict or an object
                if isinstance(res, dict):
                    memory_text = res.get('memory', '')
                else:
                    memory_text = getattr(res, 'memory', '')

                if memory_text:
                    hits.append(f"- {memory_text[:300]}")
            
            if hits:
                rag_content = "\n".join(hits)
        except Exception as e:
            print(f" [Memory] Search Error: {e}")

    # 3. Format for the System Prompt
    return f"""
    === CORE MEMORY (ESTABLISHED FACTS) ===
    {core_content}

    === RELEVANT CONVERSATION HISTORY ===
    {rag_content}
    """

# --- SAVING & DREAMING ---
def _async_add_memory(messages):
    """Background task to add memories to Mem0 without blocking the LLM."""
    if memory_client is None: return
    try:
        memory_client.add(messages, user_id="paul")
    except Exception as e:
        print(f" [Memory] Async Add Error: {e}")

def save_interaction(user_text, assistant_text):
    """
    Saves the turn to Mem0 in real-time. Replaces old numpy+episodic logic.
    """
    messages = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text}
    ]
    # Fire and forget in a background thread to prevent latency
    threading.Thread(target=_async_add_memory, args=(messages,), daemon=True).start()

def dream():
    """
    Called nightly. The LLM rewriting logic is removed because Mem0 handles 
    granular updates dynamically. We only keep routine analysis here.
    """
    print(" [Memory] 🌙 Nightly Maintenance...")
    
    # Analyze Daily Routine
    print(" [Memory] 🕵️ Analyzing Daily Routine...")
    try:
        routine.tracker.analyze_routine()
    except Exception as e:
        print(f" [Memory] Routine Analysis Error: {e}")

# --- TOOL ADAPTERS (Für jarvis/core/tools.py) ---

def save_memory_tool(text):
    """
    Speichert einen expliziten Fakt (via Tool-Call) ins Archiv.
    """
    if memory_client is None:
        return "Fehler: Mem0 nicht initialisiert."
    
    try:
        # Synchrone Speicherung für explizite Tools (damit es sofort genutzt werden kann)
        memory_client.add(text, user_id="paul")
        return f"Notiert: '{text}'."
    except Exception as e:
        return f"Fehler beim Speichern: {e}"

def search_memory_tool(search_query):
    """
    Erlaubt dem Agenten, manuell im Archiv zu suchen, falls
    der initiale Kontext nicht gereicht hat.
    """
    if memory_client is None:
        return "Fehler: Mem0 nicht initialisiert."
    
    hits = []
    try:
        results = memory_client.search(query=search_query, user_id="paul", limit=10)
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
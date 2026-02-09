import json
import os
import time
import numpy as np
from jarvis import config
from jarvis.utils import session

# --- EMBEDDING HELPER ---
EMBEDDING_MODEL = "models/text-embedding-004"

def get_embedding(text):
    if not text or not text.strip(): return None

    key = config.get_next_key()
    api_url = f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent?key={key}"

    payload = {"model": EMBEDDING_MODEL, "content": {"parts": [{"text": text}]}}
    try:
        response = session.post(api_url, json=payload, timeout=5)
        if response.status_code == 200:
            values = response.json()['embedding']['values']
            return np.array(values, dtype=np.float32)
    except Exception as e:
        print(f" [Memory] Embedding Error: {e}")
    return None

# --- HYBRID RETRIEVAL ---
def get_hybrid_context(query_text):
    """
    Combines:
    1. CORE MEMORY (The 'BIOS' from core.md)
    2. RELEVANT RECALL (Vector search from past conversations)
    """
    # 1. Load Core Memory (Always prioritized)
    core_content = ""
    if os.path.exists(config.CORE_FILE):
        with open(config.CORE_FILE, 'r', encoding='utf-8') as f:
            core_content = f.read()

    # 2. Vector Search (RAG)
    rag_content = "No specific past details found."
    
    if query_text:
        vec = get_embedding(query_text)
        if vec is not None and os.path.exists(config.VECTOR_NPY_FILE):
            try:
                vectors = np.load(config.VECTOR_NPY_FILE)
                with open(config.VECTOR_DB_FILE, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                
                # Cosine Similarity (Dot product for normalized vectors)
                scores = np.dot(vectors, vec)
                # Get Top 3
                top_k_indices = np.argsort(scores)[::-1][:3]
                
                hits = []
                for idx in top_k_indices:
                    # Filter for relevance (threshold 0.45 is a good baseline)
                    if scores[idx] > 0.45:
                        hits.append(f"- {db[idx]['text']} (Relevance: {scores[idx]:.2f})")
                
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
def save_interaction(user_text, assistant_text):
    """
    Saves the turn to BOTH the Vector DB (for search) AND the Episodic Log (for dreaming).
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M")
    full_entry = f"[{timestamp}] User: {user_text} | Jarvis: {assistant_text}"

    # A. Save to Vector DB (Immediate Recall)
    vec = get_embedding(full_entry)
    if vec is not None:
        db = []
        vectors = None
        
        if os.path.exists(config.VECTOR_DB_FILE):
            with open(config.VECTOR_DB_FILE, 'r', encoding='utf-8') as f: db = json.load(f)
        if os.path.exists(config.VECTOR_NPY_FILE):
            vectors = np.load(config.VECTOR_NPY_FILE)
        
        db.append({"text": full_entry, "timestamp": time.time()})
        
        if vectors is None: vectors = np.array([vec])
        else: vectors = np.vstack([vectors, vec])
        
        with open(config.VECTOR_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        np.save(config.VECTOR_NPY_FILE, vectors)

    # B. Save to Episodic Log (For the Dreamer)
    with open(config.EPISODIC_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{full_entry}")

def dream():
    """
    AGENTIC UPDATE: Reads episodic logs -> Updates Core Memory -> Wipes logs.
    """
    if not os.path.exists(config.EPISODIC_FILE): return

    with open(config.EPISODIC_FILE, "r", encoding="utf-8") as f:
        logs = f.read()
    
    # Don't dream if nothing happened (save API tokens)
    if len(logs) < 50: return 

    print(" [Memory] 🌙 Dreaming (Consolidating Memory)...")
    
    current_core = ""
    if os.path.exists(config.CORE_FILE):
        with open(config.CORE_FILE, 'r', encoding='utf-8') as f: current_core = f.read()

    # The "Maintainer" Prompt
    prompt = f"""
    You are the Memory Manager for JARVIS.
    
    TASK: Incorporate new information from the 'Recent Logs' into the 'Core Memory'.
    
    1. CURRENT CORE MEMORY:
    {current_core}
    
    2. RECENT LOGS (New Information):
    {logs}
    
    INSTRUCTIONS:
    - Extract PERMANENT facts (Projects, Hardware Specs, Personal Preferences, Health, Working Code).
    - IGNORE trivial chat (Greetings, Jokes, Weather).
    - IF information conflicts, the Recent Logs take precedence (Update the facts).
    - Organize with Markdown Headers (e.g. ## User Profile, ## Hardware, ## Projects).
    - OUTPUT ONLY the full, updated content for the Core Memory file.
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1} # Low temp for factual consistency
    }
    
    try:
        # Use the standard chat model
        url = config.get_gemini_url()
        resp = session.post(url, json=payload, timeout=30)
        
        if resp.status_code == 200:
            new_core = resp.json()['candidates'][0]['content']['parts'][0]['text']
            
            # 1. Update Core
            with open(config.CORE_FILE, "w", encoding="utf-8") as f:
                f.write(new_core)
            
            # 2. Wipe Episodic Log (It is now "consumed")
            with open(config.EPISODIC_FILE, "w", encoding="utf-8") as f:
                f.write("")
                
            print(" [Memory] ✨ Dream complete. Core updated.")
        else:
            print(f" [Memory] Dream failed: {resp.text}")
            
    except Exception as e:
        print(f" [Memory] Dream Error: {e}")

# --- TOOL ADAPTERS (Für jarvis/core/tools.py) ---

def save_memory_tool(text):
    """
    Speichert einen expliziten Fakt (via Tool-Call) in das episodische Gedächtnis.
    Der 'Dreamer' wird diesen Fakt heute Nacht in die Core-Memory übernehmen.
    """
    timestamp = time.strftime("%H:%M")
    # Wir markieren es als [EXPLICIT], damit der User sieht, dass es gespeichert wurde
    log_entry = f"\n[{timestamp}] [USER WANTS TO REMEMBER] {text}"
    
    with open(config.EPISODIC_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
        
    return f"Notiert: '{text}'. (Wird heute Nacht verarbeitet)"

def search_memory_tool(search_query):
    """
    Erlaubt dem Agenten, manuell im Vektor-Speicher zu suchen, falls
    der initiale Kontext nicht gereicht hat.
    """
    # Wir nutzen die Logik von get_hybrid_context, geben aber nur die Hits zurück
    vec = get_embedding(search_query)
    if vec is None: return "Fehler beim Berechnen des Vektors."
    
    hits = []
    if os.path.exists(config.VECTOR_NPY_FILE) and os.path.exists(config.VECTOR_DB_FILE):
        try:
            vectors = np.load(config.VECTOR_NPY_FILE)
            with open(config.VECTOR_DB_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
            
            scores = np.dot(vectors, vec)
            top_k = np.argsort(scores)[::-1][:5] # Top 5 für manuelle Suche
            
            for idx in top_k:
                if scores[idx] > 0.4:
                    hits.append(f"- {db[idx]['text']}")
        except Exception as e:
            return f"Suchfehler: {e}"
            
    if not hits:
        return "Keine relevanten Einträge im Archiv gefunden."
        
    return "Gefundene Archiv-Einträge:\n" + "\n".join(hits)
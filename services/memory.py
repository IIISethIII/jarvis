import json
import os
import time
import numpy as np
from jarvis.config import GEMINI_API_KEY, MEMORY_FILE, VECTOR_FILE
from jarvis.utils import session

EMBEDDING_MODEL = "models/text-embedding-004"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"

def get_embedding(text):
    if not text or not text.strip(): return None
    payload = {"model": EMBEDDING_MODEL, "content": {"parts": [{"text": text}]}}
    try:
        response = session.post(API_URL, json=payload, timeout=10)
        if response.status_code == 200:
            values = response.json()['embedding']['values']
            return np.array(values, dtype=np.float32)
    except Exception as e:
        print(f" [Memory] Error: {e}")
    return None

def save_memory(text):
    """Speichert Text & Vektor."""
    vec = get_embedding(text)
    if vec is None: return "Fehler beim Embedding."

    memories = []
    vectors = None

    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f: memories = json.load(f)
    if os.path.exists(VECTOR_FILE):
        vectors = np.load(VECTOR_FILE)

    memories.append({"text": text, "timestamp": time.time(), "date": time.strftime("%Y-%m-%d")})
    
    # Stack vectors
    if vectors is None: vectors = np.array([vec])
    else: vectors = np.vstack([vectors, vec])

    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)
    np.save(VECTOR_FILE, vectors)
    
    return "Erinnerung gespeichert."

def retrieve_relevant_memories(search_query, top_k=3, threshold=0.35):
    """Sucht nach Infos. Gibt einen String zurück, den das LLM lesen kann."""
    if not os.path.exists(VECTOR_FILE) or not os.path.exists(MEMORY_FILE):
        return "Kein Gedächtnis vorhanden."

    q_vec = get_embedding(search_query)
    if q_vec is None: return "Fehler beim Suchen."

    try:
        vectors = np.load(VECTOR_FILE)
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f: memories = json.load(f)
    except: return "Lesefehler."

    if len(vectors) == 0: return "Gedächtnis ist leer."

    scores = np.dot(vectors, q_vec)
    top_indices = np.argsort(scores)[::-1][:top_k]
    
    found_texts = []
    for idx in top_indices:
        score = scores[idx]
        if score >= threshold:
            found_texts.append(f"- {memories[idx]['text']} (Relevanz: {score:.2f})")
    
    if not found_texts:
        return "Keine relevanten Einträge gefunden."
        
    return "Gefundene Informationen:\n" + "\n".join(found_texts)

def delete_memory(topic):
    """Löscht den Eintrag, der am besten zum Thema passt."""
    if not os.path.exists(VECTOR_FILE) or not os.path.exists(MEMORY_FILE):
        return "Gedächtnis ist leer."

    q_vec = get_embedding(topic)
    if q_vec is None: return "Fehler beim Embedding."

    try:
        vectors = np.load(VECTOR_FILE)
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            memories = json.load(f)
    except: return "Lesefehler."

    scores = np.dot(vectors, q_vec)
    best_idx = np.argmax(scores) 
    best_score = scores[best_idx]

    if best_score < 0.6: 
        return f"Ich bin mir nicht sicher, was ich zu '{topic}' löschen soll (Kein treffender Eintrag)."

    removed_text = memories[best_idx]['text']
    
    memories.pop(best_idx)
    
    vectors = np.delete(vectors, best_idx, axis=0)

    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)
    np.save(VECTOR_FILE, vectors)

    return f"Gelöscht: '{removed_text}'"
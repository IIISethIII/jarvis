import chromadb
import os

V2_MEM0_DIR = "/home/pi/jarvis/memories/mem0_db"
V3_DATA_DIR = "/home/pi/jarvis_v3/data"

def main():
    print("========================================")
    print(" JARVIS V3 - MIGRATING MEMORIES")
    print("========================================")
    
    if not os.path.exists(V2_MEM0_DIR):
        print(f"❌ Error: Could not find V2 memories at {V2_MEM0_DIR}")
        return
        
    os.makedirs(V3_DATA_DIR, exist_ok=True)
    
    # Initialize ChromaDB client pointing to V2's database
    client = chromadb.PersistentClient(path=V2_MEM0_DIR)
    
    # The default collection used by mem0 is usually "mem0" or "jarvis_memories"
    # In jarvis_v2/services/memory.py, it was set to "jarvis_memories"
    try:
        collection = client.get_collection("jarvis_memories")
    except Exception as e:
        print(f"❌ Error getting collection: {e}")
        # list collections
        print("Available collections:", client.list_collections())
        return
        
    print("✅ Connected to ChromaDB.")
    
    # We want to extract memories where user_id == "paul_core"
    try:
        results = collection.get(
            where={"user_id": "paul_core"}
        )
    except Exception as e:
        print(f"❌ Error querying 'paul_core': {e}")
        return
        
    if not results or not results.get("documents"):
        print("No core memories found!")
        return
        
    facts = []
    docs = results.get("documents") or []
    metas = results.get("metadatas") or []
    
    # Pad docs to match metas if necessary
    while len(docs) < len(metas):
        docs.append(None)
        
    for doc, meta in zip(docs, metas):
        if meta and "data" in meta:
            fact = meta["data"]
        elif meta and "memory" in meta:
            fact = meta["memory"]
        else:
            fact = doc
            
        if fact and str(fact) != "None":
            facts.append(str(fact).strip())
            
    print(f"Found {len(facts)} facts in 'paul_core'.")
    
    core_file = os.path.join(V3_DATA_DIR, "core_memory.md")
    with open(core_file, "w", encoding="utf-8") as f:
        f.write("# Jarvis Core Memory\n\n")
        f.write("This file contains fundamental facts about the user. You must remember these.\n\n")
        for fact in facts:
            f.write(f"- {fact}\n")
            
    print(f"✅ Successfully wrote {len(facts)} facts to {core_file}")

if __name__ == "__main__":
    main()

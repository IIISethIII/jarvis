import chromadb

V2_MEM0_DIR = "/home/pi/jarvis/memories/mem0_db"
client = chromadb.PersistentClient(path=V2_MEM0_DIR)
col = client.get_collection("jarvis_memories")
res = col.get(where={"user_id": "paul_core"}, limit=1)

print("KEYS:", res.keys())
print("FACTS:")
for doc, meta in zip(res.get("documents", []), res.get("metadatas", [])):
    print(repr(doc), repr(meta))

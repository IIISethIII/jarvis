from core import memory
from core import tools

def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 5: MEMORY TEST")
    print("========================================")
    
    print("\n1. Testing Core Memory Retrieval...")
    core = memory.get_core_memory()
    print(f"Loaded Core Memory ({len(core)} characters).")
    
    print("\n2. Testing Archive Logging...")
    memory.log_interaction("Was hast du dir gerade gemerkt?", "Ich habe deine alte Erinnerung erfolgreich migriert.")
    print("Logged test interaction to archive.")
    
    print("\n3. Testing Archive Search Tool...")
    result = tools.execute_tool("search_archive", {"query": "migriert"})
    print(f"Search Result:\n{result}")

if __name__ == "__main__":
    main()

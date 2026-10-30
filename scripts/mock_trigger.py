import os
import sys
import time
import subprocess

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/mock_trigger.py 'text to speak'")
        sys.exit(1)
        
    text = sys.argv[1]
    
    print("🚨 Injecting mock text trigger...")
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trigger.tmp"), "w", encoding="utf-8") as f:
        f.write(text)
        
    print(f"✅ Wrote '{text}' to trigger.tmp! Check main.py logs for Jarvis's response.")

if __name__ == "__main__":
    main()

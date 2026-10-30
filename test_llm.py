import asyncio
import sys
from core.audio import AudioManager
from core.hardware import BonnetHardware
from core.llm import GeminiLiveProvider

async def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 3: GEMINI LIVE API")
    print("========================================")
    
    print("Initializing Hardware LEDs...")
    hw = BonnetHardware()
    hw.turn_off()
    
    print("Initializing Audio Manager...")
    am = AudioManager()
    am.start_mic()
    
    try:
        while True:
            print("Initializing LLM Provider...")
            llm = GeminiLiveProvider()
            
            # Connect to Gemini. This will block and run the bidirecitonal audio streams.
            # Press Ctrl+C to stop.
            await llm.connect(am, hw)
            print("[LLM] Connection closed. Reconnecting in 1 second...")
            await asyncio.sleep(1)
        
    except ValueError as e:
        print(f"\n[Error] {e}")
        print("Please create a .env file in jarvis_v3 with your GEMINI_API_KEY.")
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        hw.turn_off()
        am.stop()

if __name__ == "__main__":
    asyncio.run(main())

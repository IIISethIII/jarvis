import os
import sys
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 4: HA TOOLS + LLM TEST")
    print("========================================")
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Error] GEMINI_API_KEY missing in .env")
        return
        
    client = genai.Client(api_key=api_key)
    
    from core import tools
    from core.ha import HomeAssistant
    
    # We load the HA context so Gemini knows what devices exist!
    ha = HomeAssistant()
    print("Fetching Home Assistant devices for context...")
    all_states = ha.fetch_all_states()
    devices = [f"{s['attributes'].get('friendly_name', s['entity_id'])} ({s['entity_id']}) is {s['state']}" 
               for s in all_states if s['entity_id'].startswith(('light.', 'switch.', 'media_player.', 'input_text.'))]
    
    context_str = "\n".join(devices[:50]) # limit to 50 for this test
    
    system_instruction = f"""Du bist Jarvis, ein Smart Home Assistent.
Hier sind die verfügbaren Geräte in deinem Home Assistant:
{context_str}

Wenn der Benutzer dich bittet, ein Gerät zu steuern, benutze die verfügbaren Tools. Antworte extrem kurz."""

    print("\nStarting LLM chat session...")
    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=tools.FUNCTION_DECLARATIONS)],
            temperature=0.0
        )
    )
    
    print("\nJarvis is ready! Type your command (or 'exit' to quit).")
    print("Examples:")
    print("- 'Welche Geräte kennst du?'")
    print("- 'Schalte das Licht im Wohnzimmer ein'")
    print("- 'Schalte input_text.jarvis_chat ein'")
    
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ['exit', 'quit']:
                break
                
            response = chat.send_message(user_input)
            
            # Check if it called a tool
            if response.function_calls:
                for func_call in response.function_calls:
                    print(f"\n[Tool Call] {func_call.name}({func_call.args})")
                    result = tools.execute_tool(func_call.name, func_call.args)
                    
                    # Send result back to Gemini
                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=func_call.name,
                            response={"result": result}
                        )
                    )
            
            print(f"\nJarvis: {response.text}")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()

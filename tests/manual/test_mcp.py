import time
from core.mcp import mcp_client

def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 5: MCP SERVER TEST")
    print("========================================")
    
    print("\nStarting MCP background loop and connecting to servers...")
    mcp_client.start_sync()
    
    print("\nFetching dynamic tools from all active servers...")
    tools = mcp_client.get_gemini_tools_sync()
    
    if tools:
        print(f"\n✅ Success! Fetched {len(tools)} tools from MCP:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")
    else:
        print("\n❌ Failed to fetch any tools. Are the MCP servers running?")
        
if __name__ == "__main__":
    main()

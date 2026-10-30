import asyncio
import fnmatch
import threading
import logging
from mcp import ClientSession
from mcp.client.sse import sse_client
from google.genai import types
import urllib.request
import json
import os
from mcp.types import CallToolResult, ListToolsResult, Tool, TextContent

# Suppress annoying SSE logs
logging.getLogger("mcp.client.sse").setLevel(logging.CRITICAL)

class StatelessMCPSession:
    def __init__(self, url):
        self.url = url
        self.id_counter = 1

    async def _post(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": self.id_counter, "method": method}
        if params: payload["params"] = params
        self.id_counter += 1
        
        def do_request():
            req = urllib.request.Request(self.url, method="POST")
            req.add_header("Content-Type", "application/json")
            data = json.dumps(payload).encode("utf-8")
            try:
                with urllib.request.urlopen(req, data=data) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                try:
                    return json.loads(e.read().decode())
                except:
                    raise Exception(f"HTTP {e.code}: {e.read().decode()}")
                    
        data = await asyncio.to_thread(do_request)
        if "error" in data:
            raise Exception(str(data["error"]))
        return data.get("result", {})

    async def list_tools(self):
        result = await self._post("tools/list")
        tools = []
        for t in result.get("tools", []):
            tools.append(Tool(
                name=t["name"],
                description=t.get("description", ""),
                inputSchema=t.get("inputSchema", {})
            ))
        return ListToolsResult(tools=tools)

    async def call_tool(self, name, arguments=None):
        result = await self._post("tools/call", {"name": name, "arguments": arguments or {}})
        content = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                content.append(TextContent(type="text", text=c.get("text", "")))
        return CallToolResult(isError=result.get("isError", False), content=content)

class MCPManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        self.sessions = {}
        self.mcp_tools_cache = {}
        self.json_params = {}  # gemini tool name -> parameters sent as JSON strings
        self.server_tasks = {}

        self.servers = {}
        self._load_servers_from_config()

    def _load_servers_from_config(self):
        """Load MCP server definitions from external JSON config file."""
        import json
        import os
        config_path = os.path.join(os.path.dirname(__file__), "mcp_config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                servers = data.get("servers") or data.get("mcpServers") or {}
                # Ensure each server has a type; default to 'sse'
                for name, info in servers.items():
                    if "type" not in info:
                        info["type"] = "sse"
                self.servers = servers
        except Exception as e:
            print(f"[MCP] Failed to load server config: {e}")
            self.servers = {}

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _server_loop(self, name):
        """Maintains an SSE connection to a specific MCP server."""
        info = self.servers[name]
        url = info["url"]
        
        if info["type"] == "stateless":
            self.sessions[name] = StatelessMCPSession(url)
            print(f"[MCP] ✅ {name} online (stateless).")
            return

        while True:
            try:
                print(f"[MCP] Connecting to {name}...")
                async with sse_client(url, timeout=None) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.sessions[name] = session
                        print(f"[MCP] ✅ {name} online.")
                        
                        # Block until connection drops
                        await asyncio.Event().wait()
                        
            except asyncio.CancelledError:
                self.sessions.pop(name, None)
                break
            except Exception as e:
                print(f"[MCP] ❌ Error in {name}: {repr(e)}. Reconnecting in 10s...")
                
            self.sessions.pop(name, None)
            await asyncio.sleep(10)

    async def _start_async(self):
        """Starts background connections to all servers."""
        for name in self.servers:
            task = self.loop.create_task(self._server_loop(name))
            self.server_tasks[name] = task
            
        import time
        start_time = time.time()
        # Wait up to 10 seconds for servers to connect
        while time.time() - start_time < 10.0:
            if len(self.sessions) == len(self.servers):
                break
            await asyncio.sleep(0.5)

    def start_sync(self):
        """Synchronous wrapper to initialize all servers."""
        asyncio.run_coroutine_threadsafe(self._start_async(), self.loop).result()

    def _convert_to_gemini_schema(self, schema_dict, json_params=None):
        """Converts JSON Schema types to Google GenAI Schema.

        Free-form object parameters (no properties, e.g. call_tool's 'arguments') are offered
        as JSON strings; their names are added to json_params so _execute_async can decode them.
        """
        if not isinstance(schema_dict, dict):
            return schema_dict
            
        gemini_type_map = {
            "string": types.Type.STRING,
            "number": types.Type.NUMBER,
            "integer": types.Type.INTEGER,
            "boolean": types.Type.BOOLEAN,
            "array": types.Type.ARRAY,
            "object": types.Type.OBJECT
        }
        
        properties = {}
        required = schema_dict.get("required", [])
        
        for k, v in schema_dict.get("properties", {}).items():
            if isinstance(v, dict):
                v_type = v.get("type")
                if isinstance(v_type, list):  # e.g. ["string", "null"]
                    v_type = next((t for t in v_type if t != "null"), None)
                if not v_type and "anyOf" in v:  # e.g. anyOf: [{type: object}, {type: null}]
                    v_type = next((s.get("type") for s in v["anyOf"]
                                   if isinstance(s, dict) and s.get("type") not in (None, "null")), None)
                v_type = v_type or "string"
                description = v.get("description")
                if v_type == "object" and not v.get("properties") and json_params is not None:
                    json_params.add(k)
                    v_type = "string"
                    description = f"{description or k} (JSON object as a string)"
                prop_schema = {"type": gemini_type_map.get(v_type, types.Type.STRING)}
                if description:
                    prop_schema["description"] = description

                # Gemini API requires 'items' field if type is ARRAY
                if v_type == "array":
                    prop_schema["items"] = types.Schema(type=types.Type.STRING)
                    
                properties[k] = types.Schema(**prop_schema)
                
        return types.Schema(
            type=types.Type.OBJECT,
            properties=properties,
            required=required
        )

    async def _get_gemini_tools_async(self):
        """Fetches tools from all active MCP sessions."""
        gemini_tools = []
        for server_name, session in list(self.sessions.items()):
            # Optional per-server blacklist in mcp_config.json, e.g. "exclude": ["library_delete_*"]
            exclude = self.servers.get(server_name, {}).get("exclude", [])
            try:
                response = await session.list_tools()
                skipped = 0
                for tool in response.tools:
                    if any(fnmatch.fnmatchcase(tool.name, pattern) for pattern in exclude):
                        skipped += 1
                        continue
                    # Replace hyphens with underscores for Gemini
                    gemini_name = tool.name.replace("-", "_")
                    self.mcp_tools_cache[gemini_name] = (server_name, tool.name)

                    json_params = set()
                    schema = self._convert_to_gemini_schema(tool.inputSchema, json_params)
                    self.json_params[gemini_name] = json_params
                    
                    gemini_tools.append(
                        types.FunctionDeclaration(
                            name=gemini_name,
                            description=tool.description or f"MCP Tool: {tool.name}",
                            parameters=schema
                        )
                    )
                if skipped:
                    print(f"[MCP] {server_name}: {skipped} tool(s) excluded by config.", flush=True)
            except Exception as e:
                print(f"[MCP Error] Could not list tools for {server_name}: {e}")
        return gemini_tools

    def get_gemini_tools_sync(self):
        """Synchronous wrapper to fetch tools."""
        return asyncio.run_coroutine_threadsafe(self._get_gemini_tools_async(), self.loop).result()

    async def _execute_async(self, gemini_name, args, max_retries=5):
        info = self.mcp_tools_cache.get(gemini_name)
        if not info:
            return "Error: Tool not found."
        
        server_name, original_name = info

        # Free-form object parameters arrive as JSON strings (see _convert_to_gemini_schema)
        args = dict(args or {})
        for k in self.json_params.get(gemini_name, ()):
            if isinstance(args.get(k), str):
                try:
                    args[k] = json.loads(args[k]) if args[k].strip() else None
                except json.JSONDecodeError:
                    return f"Error: parameter '{k}' must be a JSON object."

        for attempt in range(max_retries):
            session = self.sessions.get(server_name)
            
            if not session:
                if attempt < max_retries - 1:
                    print(f"[MCP] {server_name} offline. Waiting... ({attempt + 1}/{max_retries})", flush=True)
                    await asyncio.sleep(3)
                    continue
                return f"Error: MCP Server '{server_name}' is offline."

            try:
                result = await asyncio.wait_for(
                    session.call_tool(original_name, arguments=args),
                    timeout=30.0 
                )
                
                if hasattr(result, 'isError') and result.isError:
                    error_msg = "\n".join([b.text for b in result.content if getattr(b, 'type', '') == 'text'])
                    return f"Tool Error: {error_msg}"

                if result.content:
                    return "\n".join([block.text for block in result.content if getattr(block, 'type', '') == 'text'])
                return "Executed successfully."

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    continue
                return f"Error: Timeout executing '{gemini_name}'."
            except Exception as e:
                # Force restart the task if connection died
                task = self.server_tasks.get(server_name)
                if task:
                    task.cancel()
                self.server_tasks[server_name] = self.loop.create_task(self._server_loop(server_name))
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return f"MCP Error: {e}"
                
        return "MCP Error: Max retries reached."

    def execute_sync(self, gemini_name, args):
        """Synchronous wrapper to execute a tool."""
        return asyncio.run_coroutine_threadsafe(self._execute_async(gemini_name, args), self.loop).result()
        
    async def execute(self, gemini_name, args):
        """Asynchronous wrapper that safely executes the tool on the background event loop."""
        future = asyncio.run_coroutine_threadsafe(self._execute_async(gemini_name, args), self.loop)
        return await asyncio.wrap_future(future)

# Global Singleton Instance
mcp_client = MCPManager()

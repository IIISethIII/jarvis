import asyncio
import threading
from mcp import ClientSession
from mcp.client.sse import sse_client

class MCPManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        self.sessions = {} # Speichert jetzt nur noch die Session (kein Stack mehr nötig)
        self.mcp_tools_cache = {}
        self.server_tasks = {} # Referenzen auf die Background-Tasks

        self.server_names = ["gcal", "maps", "search"]
        self.base_url = "http://mcp.local"

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        # Verhindert, dass unhandled Exceptions die gesamte Loop killen
        self.loop.set_exception_handler(self._handle_exception)
        self.loop.run_forever()

    def _handle_exception(self, loop, context):
        msg = context.get("exception", context["message"])
        print(f"  [MCP Loop] Background Info: {msg}")

    async def _server_loop(self, name):
        """Hält die Verbindung zu einem Server in einer eigenen Task aufrecht und managt den Scope."""
        url = f"{self.base_url}/{name}/sse"
        while True:
            try:
                print(f"  [MCP] Verbinde mit {name} über {url}...")
                async with sse_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.sessions[name] = session
                        print(f"  [MCP] ✅ {name} online.")
                        
                        # Blockiert endlos, hält den Kontext im selben Task offen, 
                        # bis die Verbindung abbricht oder der Task gecancelt wird
                        await asyncio.Event().wait()
                        
            except asyncio.CancelledError:
                print(f"  [MCP] Task für {name} beendet.")
                self.sessions.pop(name, None)
                break
            except Exception as e:
                # ExceptionGroup Logging (Python 3.11+)
                if hasattr(e, "exceptions"):
                    err_str = " | ".join([repr(sub) for sub in e.exceptions])
                else:
                    err_str = repr(e)
                print(f"  [MCP ❌] Fehler bei {name}, Reconnect in 5s: {err_str}")
            
            # Bei Abbruch bereinigen und neu versuchen
            self.sessions.pop(name, None)
            await asyncio.sleep(5)

    async def _start_async(self):
        """Initialer Start aller konfigurierten Server als Background-Tasks."""
        for name in self.server_names:
            task = self.loop.create_task(self._server_loop(name))
            self.server_tasks[name] = task
        # Kurze Wartezeit, um den Servern Zeit zum initialen Verbinden zu geben
        await asyncio.sleep(2)

    def start_sync(self):
        asyncio.run_coroutine_threadsafe(self._start_async(), self.loop).result()

    async def _get_gemini_tools_async(self):
        """Ruft die Tool-Definitionen aller aktiven Sessions ab."""
        gemini_tools = []
        for server_name, session in list(self.sessions.items()):
            try:
                response = await session.list_tools()
                for tool in response.tools:
                    gemini_name = tool.name.replace("-", "_")
                    self.mcp_tools_cache[gemini_name] = (server_name, tool.name) 
                    gemini_tools.append({
                        "name": gemini_name,
                        "description": tool.description or f"MCP Tool: {tool.name}",
                        "parameters": self._uppercase_types(tool.inputSchema)
                    })
            except Exception as e:
                print(f"  [MCP Error] Could not list tools for {server_name}: {e}")
        return gemini_tools

    def get_gemini_tools_sync(self):
        return asyncio.run_coroutine_threadsafe(self._get_gemini_tools_async(), self.loop).result()

    def _uppercase_types(self, d):
        if not isinstance(d, dict): return d
        new_d = {}
        for k, v in d.items():
            if k in ["$schema", "additionalProperties"]: continue
            if k == "type" and isinstance(v, str): new_d[k] = v.upper()
            elif isinstance(v, dict): new_d[k] = self._uppercase_types(v)
            elif isinstance(v, list): new_d[k] = [self._uppercase_types(i) if isinstance(i, dict) else i for i in v]
            else: new_d[k] = v
        return new_d

    async def _execute_async(self, gemini_name, args, max_retries=3):
        info = self.mcp_tools_cache.get(gemini_name)
        if not info: return "Tool nicht gefunden."
        
        server_name, original_name = info

        for attempt in range(max_retries):
            session = self.sessions.get(server_name)
            
            if not session:
                if attempt < max_retries - 1:
                    print(f"  [MCP] {server_name} offline. Warte auf Reconnect ({attempt + 1}/{max_retries})...")
                    await asyncio.sleep(3)
                    continue
                return f"Fehler: MCP Server '{server_name}' ist offline (Reconnect läuft...)"

            try:
                result = await asyncio.wait_for(
                    session.call_tool(original_name, arguments=args),
                    timeout=60.0 
                )
                
                if hasattr(result, 'isError') and result.isError:
                    error_msg = "\n".join([b.text for b in result.content if getattr(b, 'type', '') == 'text'])
                    return f"Tool Fehler: {error_msg}"

                if result.content:
                    return "\n".join([block.text for block in result.content if getattr(block, 'type', '') == 'text'])
                return "Ausgeführt."

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    print(f"  [MCP] Timeout bei {gemini_name}. Versuche erneut...")
                    await asyncio.sleep(2)
                    continue
                return f"Fehler: Das Tool '{gemini_name}' hat das Zeitlimit überschritten."
            except Exception as e:
                error_msg = repr(e) 
                if any(msg.lower() in error_msg.lower() for msg in ["timeout", "closed", "connection", "connecterror", "readtimeout"]):
                    print(f"  [MCP] Verbindung zu {server_name} instabil. Forciere Neustart des Tasks.")
                    # Killt den aktiven Task sauber (was den Scope korrekt schließt) und spawnt ihn neu
                    task = self.server_tasks.get(server_name)
                    if task:
                        task.cancel()
                    self.server_tasks[server_name] = self.loop.create_task(self._server_loop(server_name))
                    
                    if attempt < max_retries - 1:
                        print(f"  [MCP] Warte auf Task-Neustart für {server_name} ({attempt + 1}/{max_retries})...")
                        await asyncio.sleep(3)
                        continue
                return f"MCP Fehler: {error_msg}"
                
        return "MCP Fehler: Maximale Anzahl an Versuchen erreicht."

    def execute_sync(self, gemini_name, args):
        return asyncio.run_coroutine_threadsafe(self._execute_async(gemini_name, args), self.loop).result()

# Globale Instanz
mcp_client = MCPManager()
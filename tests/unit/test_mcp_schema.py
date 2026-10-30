"""MCP tool loading: schema conversion for Gemini, per-server exclude list, JSON argument decoding.

Needs the runtime dependencies (google-genai, mcp), e.g. on the Pi:
    venv/bin/python -m unittest discover tests/unit
"""
import asyncio
import json
import unittest

from google.genai import types
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from core.mcp import MCPManager

# The input schema of the FastMCP gateway's call_tool
CALL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The name of the tool to call"},
        "arguments": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}],
                      "default": None, "description": "Arguments to pass to the tool"},
    },
    "required": ["name"],
}


class FakeSession:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    async def list_tools(self):
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="ok")])


def manager(servers, sessions):
    """An MCPManager without its background thread or config file."""
    m = MCPManager.__new__(MCPManager)
    m.servers, m.sessions = servers, sessions
    m.mcp_tools_cache, m.json_params, m.server_tasks = {}, {}, {}
    return m


class SchemaConversion(unittest.TestCase):
    def convert(self, schema):
        params = set()
        return manager({}, {})._convert_to_gemini_schema(schema, params), params

    def test_free_form_object_becomes_json_string(self):
        # Regression: anyOf had no "type" and fell back to STRING without decoding,
        # so the gateway rejected every call_tool call ("Input should be a valid dictionary")
        schema, params = self.convert(CALL_TOOL_SCHEMA)
        self.assertEqual(schema.properties["arguments"].type, types.Type.STRING)
        self.assertIn("JSON object", schema.properties["arguments"].description)
        self.assertEqual(params, {"arguments"})
        self.assertEqual(schema.required, ["name"])

    def test_nullable_type_list(self):
        schema, params = self.convert({"type": "object", "properties": {"x": {"type": ["integer", "null"]}}})
        self.assertEqual(schema.properties["x"].type, types.Type.INTEGER)
        self.assertEqual(params, set())

    def test_any_of_scalar(self):
        schema, _ = self.convert({"type": "object", "properties": {"n": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}})
        self.assertEqual(schema.properties["n"].type, types.Type.INTEGER)

    def test_object_with_properties_stays_an_object(self):
        schema, params = self.convert({"type": "object", "properties": {
            "o": {"type": "object", "properties": {"a": {"type": "string"}}}}})
        self.assertEqual(schema.properties["o"].type, types.Type.OBJECT)
        self.assertEqual(params, set())

    def test_array_gets_items(self):
        schema, _ = self.convert({"type": "object", "properties": {"l": {"type": "array"}}})
        self.assertEqual(schema.properties["l"].type, types.Type.ARRAY)
        self.assertIsNotNone(schema.properties["l"].items)


class ToolLoading(unittest.TestCase):
    def setUp(self):
        tools = [Tool(name=n, description="", inputSchema={"type": "object", "properties": {}})
                 for n in ("now_playing", "library_delete_album", "library_delete_track", "play-pause")]
        tools.append(Tool(name="call_tool", description="", inputSchema=CALL_TOOL_SCHEMA))
        self.session = FakeSession(tools)
        self.m = manager({"srv": {"url": "x", "exclude": ["library_delete_*"]}}, {"srv": self.session})

    def test_exclude_patterns(self):
        names = [t.name for t in asyncio.run(self.m._get_gemini_tools_async())]
        self.assertEqual(sorted(names), ["call_tool", "now_playing", "play_pause"])

    def test_no_exclude_means_all_tools(self):
        self.m.servers["srv"].pop("exclude")
        self.assertEqual(len(asyncio.run(self.m._get_gemini_tools_async())), 5)

    def test_hyphenated_names_map_back(self):
        asyncio.run(self.m._get_gemini_tools_async())
        asyncio.run(self.m._execute_async("play_pause", {}))
        self.assertEqual(self.session.calls[-1][0], "play-pause")

    def test_json_string_arguments_are_decoded(self):
        asyncio.run(self.m._get_gemini_tools_async())
        args = {"name": "ha_get_state", "arguments": json.dumps({"entity_id": "sun.sun"})}
        self.assertEqual(asyncio.run(self.m._execute_async("call_tool", args)), "ok")
        self.assertEqual(self.session.calls[-1], ("call_tool", {"name": "ha_get_state", "arguments": {"entity_id": "sun.sun"}}))

    def test_empty_json_string_becomes_none(self):
        asyncio.run(self.m._get_gemini_tools_async())
        asyncio.run(self.m._execute_async("call_tool", {"name": "x", "arguments": ""}))
        self.assertIsNone(self.session.calls[-1][1]["arguments"])

    def test_invalid_json_is_reported_not_sent(self):
        asyncio.run(self.m._get_gemini_tools_async())
        msg = asyncio.run(self.m._execute_async("call_tool", {"name": "x", "arguments": "{not json"}))
        self.assertIn("must be a JSON object", msg)
        self.assertEqual(self.session.calls, [])

    def test_excluded_tool_cannot_be_called(self):
        asyncio.run(self.m._get_gemini_tools_async())
        self.assertEqual(asyncio.run(self.m._execute_async("library_delete_album", {})), "Error: Tool not found.")


if __name__ == "__main__":
    unittest.main()

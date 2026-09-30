"""
The orchestrator's MCP CLIENT for the Outlook MCP server (mcp_servers/
outlook_server.py) — added 2026-09-25 at the CEO's boss's explicit request
for a real MCP integration, not just a direct Graph call. orchestrator.py
calls list_recent_emails() below exactly like any other tool function; the
real MCP hop (spawn subprocess, JSON-RPC over stdio, list/call tools) is
hidden behind this one function.

MVP tradeoff, deliberate and documented: this spawns a FRESH server
subprocess on every single call via asyncio.run(), rather than keeping one
long-lived connection open across requests. Simpler and more robust to get
right first (no persistent-connection lifecycle/reconnection logic to
debug), at the cost of real per-call latency (Python subprocess startup,
roughly a few hundred ms) — fine for a low-frequency personal-assistant
tool, not fine for a high-throughput one. If this ever needs to be faster,
the fix is a persistent Client held open in a background thread with its
own event loop, not a rewrite of this file's public interface.

asyncio.run() is safe to call from here specifically because every caller
in this codebase (orchestrator._run_tools, reached from main.py's plain
`def chat`/`def chat_stream` endpoints) is synchronous all the way down —
there is no already-running event loop in this thread to conflict with.
Don't call these functions from actual async code without switching to
the underlying async client directly instead.
"""
import asyncio
import json
import os
import sys
from mcp import Client, StdioServerParameters

SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers", "outlook_server.py")


async def _call_tool(tool_name: str, arguments: dict) -> dict:
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT])
    async with Client(params) as client:
        result = await client.call_tool(tool_name, arguments)
        if result.is_error:
            raise RuntimeError(f"Outlook MCP server returned an error: {result.content}")
        # result.structured_content came back None in testing even for a
        # tool returning a plain dict (mcp 2.2.0) — parse the JSON text
        # content block instead, which is always populated.
        return json.loads(result.content[0].text)


def list_recent_emails(limit: int = 10) -> dict:
    """Real Outlook inbox read via the MCP server, over delegated (the
    CEO's own sign-in) auth. Returns {"emails": [...], "mock": bool,
    "note": str (only when mock)} — see outlook_server.py's docstring for
    why this falls back to mock data instead of prompting for sign-in
    inline when no cached token exists yet."""
    return asyncio.run(_call_tool("list_recent_emails", {"limit": limit}))

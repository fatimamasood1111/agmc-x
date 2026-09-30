"""
The Outlook MCP server — added 2026-09-25, at the CEO's boss's explicit
request for "an MCP" specifically, not just a direct Graph call. This is a
real, standalone MCP server (the `mcp` package's MCPServer, stdio
transport) that mcp_outlook_client.py spawns as a subprocess and talks to
over the actual Model Context Protocol — not a fake wrapper around a plain
function call.

Uses DELEGATED Graph auth (see outlook_auth.py) — silent-only here. If no
cached/refreshable sign-in exists yet, list_recent_emails returns the same
mock data shape email_tool._mock_emails() does, with a clear note, rather
than attempting an interactive login: a stdio MCP server's stdout is the
JSON-RPC protocol channel itself, so printing an interactive device-code
prompt there would corrupt every message this server sends. That one-time
interactive sign-in lives in the separate outlook_login.py script instead
— run manually, once, from a real terminal. See that file and
outlook_auth.py for the full reasoning.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from mcp.server import MCPServer
from mcp_servers import outlook_auth

mcp = MCPServer("outlook")


@mcp.tool()
def list_recent_emails(limit: int = 10) -> dict:
    """Fetch the most recent messages from the signed-in user's own Outlook
    inbox (subject, sender, preview, received time, read status)."""
    token = outlook_auth.get_cached_token()
    if not token:
        return {
            "emails": _mock_emails(),
            "mock": True,
            "note": (
                "Not signed in yet — showing mock data. Run "
                "`python mcp_servers/outlook_login.py` once from the backend "
                "folder to connect your real Outlook account."
            ),
        }

    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
        f"?$top={limit}&$select=subject,from,bodyPreview,receivedDateTime,isRead"
        "&$orderby=receivedDateTime desc"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return {
        "emails": [
            {
                "subject": m.get("subject"),
                "from": m.get("from", {}).get("emailAddress", {}).get("address"),
                "preview": m.get("bodyPreview"),
                "received": m.get("receivedDateTime"),
                "unread": not m.get("isRead", True),
            }
            for m in items
        ],
        "mock": False,
    }


def _mock_emails() -> list[dict]:
    """Same shape/content as email_tool._mock_emails() — kept as a literal
    duplicate rather than importing across the mcp_servers/tools boundary,
    since this server runs as its own subprocess with its own sys.path and
    is meant to stay a self-contained, standalone MCP server."""
    return [
        {
            "subject": "Q3 marketing budget — sign-off needed",
            "from": "finance@agmc.ae",
            "preview": "Attached is the finalized Q3 marketing budget for your approval...",
            "received": "2026-09-22T08:15:00Z",
            "unread": True,
        },
        {
            "subject": "Meeting request — BMW regional office",
            "from": "regional.office@bmw.com",
            "preview": "We'd like to schedule a call to discuss Q4 allocation targets...",
            "received": "2026-09-22T07:40:00Z",
            "unread": True,
        },
        {
            "subject": "Service escalation — urgent",
            "from": "service.dept@agmc.ae",
            "preview": "A customer complaint regarding appointment delays has been escalated...",
            "received": "2026-09-21T18:02:00Z",
            "unread": True,
        },
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""
Run this ONCE, manually, from a real terminal, to connect the assistant to
your real Outlook inbox:

    cd backend
    venv\\Scripts\\activate.bat
    python mcp_servers\\outlook_login.py

It prints a URL and a short code — open the URL in a browser, enter the
code, and sign in with your Microsoft account. After that completes, a
token cache file is saved locally and outlook_server.py can read your real
inbox silently (no more prompts) until the cached refresh token itself
expires, at which point just run this script again.

Needs MS_DELEGATED_CLIENT_ID and MS_TENANT_ID set in backend/.env first —
see CLAUDE.md for the Azure Portal setup steps (registering a public-client
app with the delegated Mail.Read permission). This is deliberately a
separate script from the MCP server itself: a stdio MCP server can't safely
print an interactive prompt without corrupting its own protocol stream, so
this one-time sign-in has to happen here instead, run directly by a human.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers import outlook_auth

if __name__ == "__main__":
    outlook_auth.run_device_login()

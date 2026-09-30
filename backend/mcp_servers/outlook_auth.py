"""
Shared delegated-auth helper for the Outlook MCP server (outlook_server.py)
and its one-time setup script (outlook_login.py).

This is DELEGATED auth (the CEO signs in as himself, access is scoped to
his own mailbox only) via a PUBLIC client app registration (MS_DELEGATED_
CLIENT_ID) — deliberately separate from MS_CLIENT_ID/MS_CLIENT_SECRET in
config.py, which is a CONFIDENTIAL client used for app-only access to any
mailbox in the tenant. Public clients can't hold a secret safely (a desktop
app can always be decompiled/inspected), so MSAL's device-code flow is used
instead — no secret at all, just an interactive one-time sign-in.

Two distinct operations live here, and they must stay separate:
  - get_cached_token(): SILENT ONLY, never prompts, used by outlook_server.py
    on every real tool call. If no valid cached/refreshable token exists,
    returns None — the caller (outlook_server.py) must fall back to mock
    data, NOT attempt an interactive login itself (see that module's
    docstring for why: stdio MCP servers can't safely print an interactive
    prompt on stdout without corrupting the JSON-RPC protocol stream).
  - run_device_login(): INTERACTIVE, prints a real URL + code to the
    console and blocks until the CEO completes sign-in in a browser. Only
    ever called from outlook_login.py, run manually from a real terminal —
    never from inside the MCP server subprocess.

The token cache file is a real credential (contains a refresh token) —
gitignored, never commit it, treat it like a password.
"""
import os
import msal
from config import config

SCOPES = ["Mail.Read"]
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".outlook_token_cache.json")


def _authority() -> str:
    return f"https://login.microsoftonline.com/{config.MS_DELEGATED_TENANT_ID}"


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


def _app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        config.MS_DELEGATED_CLIENT_ID, authority=_authority(), token_cache=cache
    )


def get_cached_token() -> str | None:
    """Silent-only: returns an access token if a cached account can refresh
    one with no user interaction, else None. Never prompts, never blocks."""
    if not (config.MS_DELEGATED_CLIENT_ID and config.MS_DELEGATED_TENANT_ID):
        return None
    cache = _load_cache()
    app = _app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)  # acquire_token_silent can refresh + rewrite the cache
    if result and "access_token" in result:
        return result["access_token"]
    return None


def run_device_login() -> None:
    """Interactive, one-time setup — run manually via outlook_login.py, from
    a real terminal, never from inside the MCP server subprocess."""
    if not (config.MS_DELEGATED_CLIENT_ID and config.MS_DELEGATED_TENANT_ID):
        raise RuntimeError(
            "Set MS_DELEGATED_CLIENT_ID and MS_DELEGATED_TENANT_ID in backend/.env first "
            "(see CLAUDE.md for the Azure Portal setup steps)."
        )
    cache = _load_cache()
    app = _app(cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {flow}")
    print(flow["message"])  # tells the CEO which URL to visit and what code to enter
    result = app.acquire_token_by_device_flow(flow)  # blocks until sign-in completes
    _save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Sign-in failed: {result.get('error_description')}")
    print("Signed in successfully — token cached. The Outlook MCP server can now read real email.")

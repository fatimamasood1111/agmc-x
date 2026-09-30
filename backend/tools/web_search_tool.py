"""
Real web search for the local-model (Ollama) path — added 2026-09-28 to
give it an equivalent to Claude's native web_search/web_fetch tools, which
have no Ollama equivalent at all (see llm_providers.py's docstring:
Anthropic runs those server-side; a local model has zero built-in network
access, full stop).

Two real backends, tried in order — NOT scraping either way (this project
already tried and explicitly ruled that out for hotels/social media, see
CLAUDE.md's "Headless-browser scraping... abandoned" gotcha, after hitting
real bot-defense walls):

1. **SearXNG, self-hosted** (preferred — genuinely free, no API key, no
   cost). Added after the CEO's boss flagged Brave's paid tier as a
   blocker. SearXNG is a real, actively-maintained open-source metasearch
   engine that aggregates results from many real engines (Brave, Google,
   Bing, DuckDuckGo, etc.) — it's SearXNG's own server doing the fetching
   from those engines' public web frontends, not this project scraping
   anything itself. Runs locally (`.tools/searxng/`, see CLAUDE.md for the
   full non-Docker setup this machine needed — Docker itself wasn't
   installable here, no admin rights). Must be running
   (`.tools/searxng/start_searxng.bat`) for this path to work at all —
   falls through to Brave/mock if it isn't reachable.
2. **Brave Search API** (optional fallback, costs money past its own free
   tier — kept only for whoever has a key already; not required for this
   tool to work).

Falls back to honest mock results (`mock: true` + a note) if neither is
reachable, same convention every other API-backed tool in this project
follows.
"""
import requests
from config import config

SEARXNG_SEARCH_URL = f"{config.SEARXNG_URL.rstrip('/')}/search"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def search_web(query: str, count: int = 5) -> dict:
    """Returns {"results": [{"title", "url", "snippet"}, ...], "mock": bool}.
    Registered for BOTH providers (see orchestrator.TOOLS) — Claude already
    has its own native web_search/web_fetch and should prefer those, but
    nothing stops it reaching for this one too if it's ever more direct."""
    result = _search_searxng(query, count)
    if result is not None:
        return result
    if config.BRAVE_SEARCH_API_KEY:
        return _search_brave(query, count)
    return {
        "results": _mock_results(query),
        "mock": True,
        "note": (
            f"SearXNG isn't reachable at {config.SEARXNG_URL} and no "
            "BRAVE_SEARCH_API_KEY is set — showing mock results. Start "
            "SearXNG (.tools/searxng/start_searxng.bat) for real, free "
            "results, or set BRAVE_SEARCH_API_KEY instead."
        ),
    }


def _search_searxng(query: str, count: int) -> dict | None:
    """Returns None (not a result dict) if SearXNG itself isn't reachable —
    the caller then tries Brave/mock instead. Any other failure (a real
    HTTP error from a running SearXNG instance) is a genuine problem worth
    surfacing, so it's let through as a raised exception... except we
    don't want a flaky single search engine inside SearXNG to break the
    whole tool, so this stays defensive: any exception here just means
    "SearXNG didn't give us a usable answer this time", same as
    unreachable."""
    try:
        resp = requests.get(
            SEARXNG_SEARCH_URL,
            params={"q": query, "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        return {
            "results": [
                {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in results[:count]
            ],
            "mock": False,
        }
    except Exception:
        return None


def _search_brave(query: str, count: int) -> dict:
    try:
        resp = requests.get(
            BRAVE_SEARCH_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": config.BRAVE_SEARCH_API_KEY},
            params={"q": query, "count": count},
            timeout=10,
        )
        resp.raise_for_status()
        web_results = resp.json().get("web", {}).get("results", [])
        return {
            "results": [
                {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
                for r in web_results[:count]
            ],
            "mock": False,
        }
    except Exception as e:
        return {
            "results": _mock_results(query),
            "mock": True,
            "note": f"Brave Search API call failed ({e}) — showing mock results instead.",
        }


def _mock_results(query: str) -> list[dict]:
    return [{
        "title": f"Mock result for '{query}'",
        "url": "https://example.com",
        "snippet": "Placeholder data — start SearXNG or set BRAVE_SEARCH_API_KEY to get real results.",
    }]

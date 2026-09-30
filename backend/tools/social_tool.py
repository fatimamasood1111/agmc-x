"""
Read-only social monitoring tool: Instagram + Facebook (Meta Graph API)
and X (Twitter API v2). Read-only scopes only — this tool never posts.

Only get_x_mentions is registered as an LLM tool (orchestrator.TOOLS) —
found 2026-09-24: a combined get_social_mentions() that bundled Instagram
in unconditionally used to be the registered tool, and its broad
description ("fetch recent Instagram and X mentions... for questions
about social media activity") competed with and usually beat the system
prompt's narrower instruction to use a site-restricted web_search for
Instagram/Facebook — so the CEO kept seeing the same two hardcoded mock
posts regardless of what he actually asked. Splitting X out as its own
tool with an honest, narrow description fixes the ambiguity; the system
prompt now always pairs it with a real web_search for Instagram/Facebook
rather than leaving that to a generic tool with no live credentials.

get_instagram_mentions is kept here, real and working if Meta credentials
are ever configured, but deliberately NOT registered as a tool — same
reasoning as send_email/create_event being real but ungated-tool-free.
IMPORTANT LIMITATION if it ever is: Instagram's Graph API only returns
posts where someone explicitly @-tags AGMC's account — it has no keyword
search across public Instagram, so organic mentions that don't tag the
handle are invisible to this tool. That's a Meta platform restriction,
not something fixable in code. X's search API, by contrast, does support
real keyword search, so get_x_mentions() catches AGMC being talked about
even without a direct @-mention.

If true "what is everyone saying about us" listening on Instagram
becomes a real requirement, that needs a paid social-listening
product (e.g. Brand24, Mention, Talkwalker, Meltwater) layered in as
its own tool — not an extension of this one.
"""
import requests
from config import config


def get_instagram_mentions(limit: int = 5) -> list[dict]:
    """Posts where AGMC's account was explicitly tagged — not a keyword search."""
    if not (config.META_ACCESS_TOKEN and config.META_IG_BUSINESS_ID):
        return _mock_instagram()

    url = (
        f"https://graph.facebook.com/v19.0/{config.META_IG_BUSINESS_ID}/tags"
        f"?fields=caption,timestamp,permalink&limit={limit}"
        f"&access_token={config.META_ACCESS_TOKEN}"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_x_mentions(limit: int = 5) -> list[dict]:
    """Keyword search across public posts — catches people talking about AGMC
    even if they never @-tag the account. This is genuinely supported by X's
    search API, unlike Instagram's."""
    if not (config.X_BEARER_TOKEN and config.X_SEARCH_TERM):
        return _mock_x()

    headers = {"Authorization": f"Bearer {config.X_BEARER_TOKEN}"}
    query = f'"{config.X_SEARCH_TERM}" -is:retweet'
    url = f"https://api.x.com/2/tweets/search/recent?query={query}&max_results={limit}"
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _mock_instagram():
    return [{"caption": "Loving the new AGMC showroom! #BMW", "timestamp": "2026-09-22T09:10:00Z"}]


def _mock_x():
    return [{"text": "@AGMC service appointment got pushed back twice, not happy.", "created_at": "2026-09-21T20:00:00Z"}]

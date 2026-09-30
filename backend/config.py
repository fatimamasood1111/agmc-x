"""
Central place every credential is read from.
Nothing in this file is a secret itself — the actual values live in a
local .env file (see .env.example) that is never committed to source
control. If a value is missing, the matching tool falls back to mock
data instead of crashing, so the whole system can be demoed before
every credential is in place.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Anthropic (the orchestrator's brain) ---
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

    # --- Microsoft Graph: email + calendar reads ---
    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
    MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
    MS_TENANT_ID = os.getenv("MS_TENANT_ID")
    MS_USER_EMAIL = os.getenv("MS_USER_EMAIL")  # mailbox being read, e.g. ceo@agmc.ae

    # --- Microsoft Graph, delegated auth: the Outlook MCP server ---
    # A DIFFERENT app registration from MS_CLIENT_ID above — that one is an
    # app-only/confidential client (needs a secret, needs tenant-wide admin
    # consent for Mail.Read on ANY mailbox). This one is a PUBLIC client (no
    # secret — can't keep one safe in a desktop app), used for the CEO's own
    # device-code sign-in, granting access to only his own mailbox. See
    # mcp_servers/README or CLAUDE.md for the exact Azure Portal setup steps.
    MS_DELEGATED_CLIENT_ID = os.getenv("MS_DELEGATED_CLIENT_ID")
    # Deliberately its OWN variable, NOT a reuse of MS_TENANT_ID above — found
    # 2026-09-25 testing with a personal Microsoft account: Graph's /me
    # endpoints need the MSAL authority built from the special "consumers"
    # keyword for a personal account (a real tenant GUID makes Graph look for
    # the user INSIDE that org tenant, where a personal account doesn't
    # exist — empty 401, no error body, easy to misread as an auth failure
    # when the token itself was actually valid). A real work account instead
    # needs its own real tenant GUID here. These two cases are NOT
    # interchangeable, hence the separate variable.
    MS_DELEGATED_TENANT_ID = os.getenv("MS_DELEGATED_TENANT_ID")

    # --- X (Twitter) API v2: mention monitoring ---
    X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
    X_SEARCH_TERM = os.getenv("X_SEARCH_TERM")  # e.g. "AGMC" — plain keyword, not just @handle

    # --- Meta Graph API: Instagram + Facebook mention/comment monitoring ---
    META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    META_IG_BUSINESS_ID = os.getenv("META_IG_BUSINESS_ID")
    META_PAGE_ID = os.getenv("META_PAGE_ID")

    # --- Ignav Flight Prices API: flight fare search (read-only) ---
    IGNAV_API_KEY = os.getenv("IGNAV_API_KEY")

    # --- Ollama: optional local model, switchable at runtime ---
    # A locally-running `ollama serve` daemon (not a cloud call at all) —
    # see orchestrator.set_provider/get_provider and llm_providers.py.
    # qwen2.5:3b, not a llama3.2 size — live comparison 2026-09-28 (see
    # CLAUDE.md's Ollama section) found llama3.2:1b and llama3.2:3b both
    # hallucinated tool calls on plain, unrelated prompts (3b also failed
    # basic arithmetic outright); qwen2.5:3b didn't, and correctly read a
    # real Outlook calendar including a real 300+-attendee event. Still has
    # a real, documented gap on the send-email approval-block flow
    # specifically — see that same section before trusting a real
    # send/booking approval drafted under this provider.
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    # Real bug found 2026-09-29: Ollama's own runtime default context
    # window is 4096 tokens (confirmed live via `ollama ps`'s CONTEXT
    # column), NOT the model's real 32768-token architectural max — and
    # this app never overrode it. Measured directly (via prompt_eval_count
    # on a real request): _system_prompt() + all 9 tool schemas alone come
    # to ~3488 tokens — 85% of that 4096 budget gone before a single real
    # user message, tool result, or turn of history exists. This is very
    # likely the real root cause behind most of the session's earlier
    # "loses context"/hijacking symptoms, not just a contributing factor.
    # 8192 is a measured tradeoff, not a guess: on this CPU-only machine
    # (no GPU acceleration — see the qwen2.5:7b latency gotcha below),
    # context size costs scale steeply, non-linearly, not proportionally.
    # A real flight-search exchange took ~90s at the old 4096 default,
    # ~158s at 8192, and was STILL RUNNING after 30+ minutes of real CPU
    # time at 16384 (confirmed via the actual llama-server process, not a
    # hang) before being killed. 8192 more than doubles the ~600-token
    # headroom the old 4096 default left after system+tools, while
    # staying in a tolerable response-time range — 16384 or higher isn't
    # practical on this hardware regardless of how much more correct it
    # would be. Revisit this number if real GPU acceleration ever gets
    # set up (see that same gotcha).
    OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

    # --- Web search for the local-model path (Ollama has no built-in web
    # access at all, unlike Claude's native web_search/web_fetch) ---
    # SearXNG is the PREFERRED, genuinely-free backend — a self-hosted
    # metasearch engine at .tools/searxng/ (see CLAUDE.md for the full
    # non-Docker setup this machine needed). Must actually be running
    # (.tools/searxng/start_searxng.bat) for tools/web_search_tool.py to
    # use it; falls through to Brave/mock otherwise.
    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888")
    # Brave Search API: optional fallback ONLY — costs money past its own
    # free tier, not required for search_web to work at all.
    BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY")


config = Config()

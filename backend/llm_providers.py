"""
Ollama-specific glue for the runtime Claude<->local-model switch — added
2026-09-28, ("switch between our current Anthropic
model and the smallest Ollama model that can run on my PC"). Claude stays
the default and orchestrator.py's existing Claude path (run/run_stream) is
completely untouched — this module exists so Ollama's wire format (OpenAI-
style messages + tool_calls, no content blocks, no server-side web_search/
web_fetch, no vision on a small model) doesn't have to be woven line-by-line
into orchestrator.py's already-dense, hard-won Claude logic (pause_turn
handling, the user-turn-merge fix, vision tool_result blocks, etc.).

Session history (orchestrator._sessions) is ALWAYS stored in Claude's own
content-block format, regardless of which provider is active. Claude reads
that natively with zero translation. When Ollama is active, flatten_history()
below turns it into a plain role/content message list Ollama can read —
one-directional, regenerated fresh on every Ollama call, never stored. Going
the other way needs no translation at all: orchestrator._run_ollama /
_run_stream_ollama append Ollama's replies back into the canonical history
as plain {"type": "text"} blocks, which Claude already reads as perfectly
ordinary conversation — see those two functions in orchestrator.py.

Two hard, real limits this design accepts rather than tries to paper over
(see CLAUDE.md's "Switching between Claude and a local Ollama model"
section for the full reasoning):
- Vision cannot cross the switch. A tool_result or upload that included an
  image is rendered as a plain "[Image attached — not visible to this local
  model]" note, never as real image data — the smallest Ollama models have
  no vision at all, and even a vision-capable one wasn't the ask here.
- web_search/web_fetch don't exist for Ollama at all (Anthropic runs those
  server-side) — ollama_tools() below drops them from the tool list
  entirely rather than sending a broken tool definition.
"""
import json
import ollama
from config import config


def ollama_client() -> ollama.Client:
    return ollama.Client(host=config.OLLAMA_HOST)


def ollama_tools(tools: list[dict]) -> list[dict]:
    """Converts orchestrator.TOOLS (Claude/MCP-shaped, keyed on
    input_schema) into Ollama's OpenAI-style function-calling format.
    Server tools (web_search, web_fetch) have no input_schema key at all —
    they're Anthropic-server-side-only, so they're skipped here rather than
    sent as a broken/empty tool definition Ollama can't do anything with."""
    out = []
    for t in tools:
        if "input_schema" not in t:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        })
    return out


def _stringify_block(block: dict) -> str:
    """Renders one Claude content block as plain text — Ollama has no
    concept of typed content blocks, so every block type collapses to a
    readable string instead of being replayed as a real tool_use/image."""
    btype = block.get("type")
    if btype == "text":
        return block.get("text", "")
    if btype == "tool_use":
        return f"[Called tool {block.get('name')} with {json.dumps(block.get('input'), default=str)}]"
    if btype == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            image_count = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")
            rendered = " ".join(p for p in text_parts if p)
            if image_count:
                rendered += f" [+{image_count} image(s) attached — not visible to this local model]"
            return f"[Tool result]: {rendered}"
        return f"[Tool result]: {content}"
    if btype == "image":
        return "[Image attached — not visible to this local model]"
    return ""


def _is_tool_use_only(turn: dict) -> list[dict] | None:
    content = turn.get("content")
    if turn.get("role") == "assistant" and isinstance(content, list) and content \
            and all(b.get("type") == "tool_use" for b in content):
        return content
    return None


def _is_tool_result_only(turn: dict) -> list[dict] | None:
    content = turn.get("content")
    if turn.get("role") == "user" and isinstance(content, list) and content \
            and all(b.get("type") == "tool_result" for b in content):
        return content
    return None


def flatten_history(claude_history: list[dict]) -> list[dict]:
    """Turns Claude-shaped session history into a plain Ollama message list.
    See this module's docstring for why this is one-directional and
    regenerated fresh rather than stored — the canonical history in
    orchestrator._sessions never changes shape, so switching back to Claude
    needs no reverse translation.

    A real tool_use-only assistant turn immediately followed by a
    tool_result-only user turn (exactly what orchestrator._persist_tool_round
    writes) is reconstructed as Ollama's OWN native tool_calls/"tool"
    message shape here, NOT flattened to prose via _stringify_block. This
    isn't cosmetic — confirmed live 2026-09-29: rendering a past tool call
    as plain assistant TEXT ("[Called tool X with Y]") reads, to the
    model, like a stylistic example of something it once said, and it
    literally echoed that bracketed text verbatim into a real CEO-visible
    reply instead of treating it as background data. Reconstructing the
    actual tool_calls/tool message shape sidesteps this: it's a different
    message shape entirely (empty content + a tool_calls field, and a
    role="tool" message), not free-form prose a text completion would
    treat as continuable style. Everything else (plain text, images, and
    any tool_use/tool_result that for any reason doesn't pair up exactly
    like this) still falls through to the old prose flattening below."""
    messages = []
    i, n = 0, len(claude_history)
    while i < n:
        turn = claude_history[i]
        tool_use_blocks = _is_tool_use_only(turn)
        if tool_use_blocks is not None and i + 1 < n:
            tool_result_blocks = _is_tool_result_only(claude_history[i + 1])
            if tool_result_blocks is not None and len(tool_result_blocks) == len(tool_use_blocks):
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": b.get("name"), "arguments": b.get("input")}}
                        for b in tool_use_blocks
                    ],
                })
                for use_block, result_block in zip(tool_use_blocks, tool_result_blocks):
                    result_content = result_block.get("content")
                    if isinstance(result_content, list):
                        # Reuse _stringify_block's own image-count/text-join
                        # logic for a tool_result's list-shaped content,
                        # rather than duplicating it here.
                        result_content = _stringify_block(result_block).removeprefix("[Tool result]: ")
                    messages.append({
                        "role": "tool",
                        "tool_name": use_block.get("name"),
                        "content": result_content,
                    })
                i += 2
                continue

        role = "assistant" if turn["role"] == "assistant" else "user"
        content = turn["content"]
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(filter(None, (_stringify_block(b) for b in content)))
        if text:
            messages.append({"role": role, "content": text})
        i += 1
    return messages

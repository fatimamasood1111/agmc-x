"""On-disk chat history, so past conversations survive closing the app.

One small JSON file per conversation under a per-user folder
(%APPDATA%\\AGMC X\\history, or AGMC_DATA_DIR if set). Only the VISIBLE
transcript is kept — what the CEO typed and what the assistant answered — never
raw tool results, so a stored chat holds no more than what was on his screen.

Approval-card proposals (```agmc-...```) are stripped before saving. If a stored
chat were re-rendered with its live cards, an old "Send email" / "Book meeting"
card could be clicked days later and act on stale content; a restored chat is
read-only history, never a source of actions.

The files are plain text on the user's own profile (like his Outlook cache).
"Delete" and "Clear all" remove them for real.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

MAX_CONVERSATIONS = 200
_lock = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")  # ids become file names: never trust them
_BLOCK_RE = re.compile(r"```agmc-[a-z-]+\s.*?```", re.S)
_PLACEHOLDER = "*(A proposal card was shown here — cards are not restored from history.)*"


def valid_id(session_id: str) -> bool:
    return bool(_ID_RE.match(session_id or ""))


def _dir() -> Path:
    base = os.environ.get("AGMC_DATA_DIR")
    if not base:
        appdata = os.environ.get("APPDATA")
        base = os.path.join(appdata, "AGMC X") if appdata else os.path.join(os.path.expanduser("~"), ".agmc-x")
    d = Path(base) / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    return _dir() / f"{session_id}.json"


def clean_text(text: str) -> str:
    return _BLOCK_RE.sub(_PLACEHOLDER, text).strip()


def _title(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= 60 else t[:59].rstrip() + "…"


def _write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(session_id: str) -> dict | None:
    if not valid_id(session_id):
        return None
    try:
        with open(_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def append_turn(session_id: str, user_text: str, assistant_text: str) -> None:
    """Save one completed exchange (always a user+assistant pair, so a restored
    conversation keeps strictly alternating roles)."""
    if not valid_id(session_id) or not user_text.strip() or not assistant_text.strip():
        return
    now = time.time()
    with _lock:
        conv = load(session_id) or {"id": session_id, "title": _title(user_text), "created": now, "messages": []}
        conv["messages"] += [
            {"role": "user", "text": user_text.strip(), "ts": now},
            {"role": "assistant", "text": clean_text(assistant_text), "ts": now},
        ]
        conv["updated"] = now
        _write(_path(session_id), conv)
        _prune()


def list_conversations() -> list[dict]:
    out = []
    with _lock:
        for p in _dir().glob("*.json"):
            try:
                with open(p, encoding="utf-8") as f:
                    c = json.load(f)
                out.append({"id": c["id"], "title": c.get("title") or "Conversation",
                            "updated": c.get("updated", 0), "count": len(c.get("messages", []))})
            except (OSError, ValueError, KeyError):
                continue
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def delete(session_id: str) -> bool:
    if not valid_id(session_id):
        return False
    with _lock:
        try:
            _path(session_id).unlink()
            return True
        except OSError:
            return False


def clear_all() -> int:
    n = 0
    with _lock:
        for p in _dir().glob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def _prune() -> None:
    files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[MAX_CONVERSATIONS:]:
        try:
            p.unlink()
        except OSError:
            pass

"""Deterministic parsing of "schedule a meeting" requests (local-model path).

Why this exists (CEO report 2026-09-30): asked to schedule a meeting, the local
model ignored the requested time, claimed "tomorrow's calendar is full" and then
proposed a different slot, said the meeting was "confirmed" without ever making
an approval card, looped on "yes", and mangled the attendee's address. Picking a
date, a time, a length and a real address is bookkeeping, not language, so code
does it here and the model is not involved at all — same principle as the inbox
summary and reply fast paths in orchestrator.py.

Everything here is pure (no Outlook, no model) so it can be unit-tested with a
fixed "today". Anything it can't determine is reported as missing so the caller
ASKS instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, time, timedelta

_EMAIL_RE = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")

# --- intent ---------------------------------------------------------------
_VERB_RE = re.compile(r"\b(schedule|set\s*up|arrange|organi[sz]e|book|create|add|put|fix|make)\b", re.I)
_NOUN_RE = re.compile(
    r"\b(meeting|call|appointment|catch[- ]?up|sync|event|invite|1:1|one[- ]on[- ]one|calendar|slot)\b", re.I)
_NOUN_WITH_RE = re.compile(r"\b(meeting|call|catch[- ]?up|sync)\s+with\b", re.I)
_STARTS_SCHEDULE_RE = re.compile(r"^\W*(?:(?:please|okay|ok|hey|can you|could you|just)\s+)*schedule\b", re.I)
_EXCLUDE_RE = re.compile(
    r"\b(flights?|hotels?|cancel|delete|remove|resched\w*|move|summar\w*|inbox|reply|respond|forward|"
    r"free|available|availability|open slots?)\b", re.I)


def is_schedule_request(text: str) -> bool:
    if len(text) > 300 or _EXCLUDE_RE.search(text):
        return False
    if _STARTS_SCHEDULE_RE.search(text):
        return True
    if _VERB_RE.search(text) and _NOUN_RE.search(text):
        return True
    return bool(_NOUN_WITH_RE.search(text) and (_TIME_ANY_RE.search(text) or _DAY_HINT_RE.search(text)))


# --- slots ----------------------------------------------------------------
@dataclass
class Slots:
    emails: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    day: date | None = None
    start: time | None = None
    end: time | None = None
    minutes: int | None = None
    title: str | None = None
    ambiguous_hour: int | None = None  # a bare "at 3" — AM or PM?
    solo: bool = False  # "just me": a personal calendar block, no invitees
    labels: dict = field(default_factory=dict)  # address -> "Name (address)" for resolved contacts

    def has_anything(self) -> bool:
        return bool(self.emails or self.names or self.day or self.start or self.end
                    or self.minutes or self.title or self.ambiguous_hour or self.solo)


def merge(old: Slots, new: Slots) -> Slots:
    """Fill from `new` where it says something, else keep `old`."""
    out = replace(old)
    out.solo = old.solo or new.solo
    out.labels = {**old.labels, **new.labels}
    if new.emails or new.names:
        out.emails, out.names = new.emails, new.names
    if new.day:
        out.day = new.day
    if new.start or new.end:
        out.start, out.end, out.ambiguous_hour = new.start, new.end, None
    elif new.ambiguous_hour:
        out.ambiguous_hour = new.ambiguous_hour
        out.start = None
    if new.minutes:
        out.minutes = new.minutes
    if new.title:
        out.title = new.title
    return out


# --- title ----------------------------------------------------------------
_TITLE_RE = re.compile(
    r"""\b(?:called|titled|named|entitled|subject|about|regarding)\s*:?\s*
        (?:["“'‘](?P<q>[^"”'’]+)["”'’]|(?P<t>.+?))
        (?=\s*[,.;!?]?\s*(?:$|\b(?:on|at|for|with|from|tomorrow|today|next|this|then|by)\b))""",
    re.I | re.S | re.X)
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,80})[\"”]")


def _extract_title(text: str) -> tuple[str | None, str]:
    m = _TITLE_RE.search(text)
    if m:
        title = (m.group("q") or m.group("t") or "").strip(" .,;:!?\"'")
        rest = text[:m.start()] + " " + text[m.end():]
        return (title[:1].upper() + title[1:] if title else None), rest
    m = _QUOTED_RE.search(text)
    if m:
        title = m.group(1).strip()
        return title[:1].upper() + title[1:], text[:m.start()] + " " + text[m.end():]
    return None, text


# --- attendees ------------------------------------------------------------
_WITH_RE = re.compile(
    r"\bwith\s+(?P<n>.+?)(?=\s*[,.;!?]|\s+(?:on|at|for|from|tomorrow|today|tonight|next|this|by|called|"
    r"titled|named|about|regarding|in|to|over|around|after|before)\b|$)", re.I)
_SPLIT_NAMES_RE = re.compile(r"\s*(?:,|&|\band\b)\s*", re.I)


def _extract_names(text: str) -> list[str]:
    m = _WITH_RE.search(text)
    if not m:
        return []
    names = []
    for part in _SPLIT_NAMES_RE.split(m.group("n")):
        part = part.strip(" .")
        if part and len(part.split()) <= 5:
            names.append(part)
    return names


# --- date -----------------------------------------------------------------
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "tues": 1, "wednesday": 2, "thursday": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sunday": 6,
}
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_M = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _M + r"\b", re.I)
_MONTH_DAY_RE = re.compile(r"\b" + _M + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*(?::|am|pm))", re.I)
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_WEEKDAY_RE = re.compile(r"\b(?:(next|this|coming)\s+)?(" + "|".join(_WEEKDAYS) + r")\b", re.I)
_DAY_HINT_RE = re.compile(
    r"\b(today|tonight|tomorrow|tmrw|day after tomorrow|" + "|".join(_WEEKDAYS) + r")\b|" + _M + r"\s+\d|\d\s+" + _M, re.I)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _extract_day(text: str, today: date) -> tuple[date | None, str]:
    low = text.lower()
    for pat, delta in ((r"\bday after tomorrow\b", 2), (r"\b(?:tomorrow|tmrw)\b", 1), (r"\b(?:today|tonight)\b", 0)):
        m = re.search(pat, low)
        if m:
            return today + timedelta(days=delta), text[:m.start()] + " " + text[m.end():]
    m = _ISO_RE.search(text)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return d, text[:m.start()] + " " + text[m.end():]
    for rx, order in ((_DAY_MONTH_RE, "dm"), (_MONTH_DAY_RE, "md")):
        m = rx.search(text)
        if m:
            a, b = m.group(1), m.group(2)
            day_s, mon_s = (a, b) if order == "dm" else (b, a)
            month = _MONTHS[mon_s.lower()[:3]]
            d = _safe_date(today.year, month, int(day_s))
            if d and d < today:
                d = _safe_date(today.year + 1, month, int(day_s))
            if d:
                return d, text[:m.start()] + " " + text[m.end():]
    m = _SLASH_RE.search(text)
    if m:  # UAE uses day/month
        year = int(m.group(3)) if m.group(3) else today.year
        year = year + 2000 if year < 100 else year
        d = _safe_date(year, int(m.group(2)), int(m.group(1)))
        if d and d < today and not m.group(3):
            d = _safe_date(today.year + 1, int(m.group(2)), int(m.group(1)))
        if d:
            return d, text[:m.start()] + " " + text[m.end():]
    m = _WEEKDAY_RE.search(text)
    if m:
        wd = _WEEKDAYS[m.group(2).lower()]
        d = today + timedelta(days=(wd - today.weekday()) % 7)
        if d == today:
            d += timedelta(days=7)  # "on friday" said on a Friday means the next one
        if (m.group(1) or "").lower() == "next" and d < today + timedelta(days=7 - today.weekday()):
            d += timedelta(days=7)  # "next friday" = the one in NEXT week
        return d, text[:m.start()] + " " + text[m.end():]
    return None, text


# --- duration -------------------------------------------------------------
_DUR_RE = re.compile(
    r"\b(?:(?:for|of)\s+)?(?:(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|minutes?|mins?)\b"
    r"|(?P<half>half\s+an?\s+hour|half[- ]hour)|(?:for\s+)(?P<one>an?\s+hour|one\s+hour))", re.I)


def _extract_duration(text: str) -> tuple[int | None, str]:
    m = _DUR_RE.search(text)
    if not m:
        return None, text
    if m.group("half"):
        minutes = 30
    elif m.group("one"):
        minutes = 60
    else:
        n = float(m.group("num"))
        minutes = int(round(n * 60)) if m.group("unit").lower().startswith("h") else int(round(n))
    if not 5 <= minutes <= 12 * 60:
        return None, text
    return minutes, text[:m.start()] + " " + text[m.end():]


# --- time -----------------------------------------------------------------
_MER = r"(am|pm|a\.m\.|p\.m\.)"
_RANGE_RE = re.compile(
    r"\b(?:from\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*" + _MER + r"?\s*(?:to|until|till|-|–)\s*(\d{1,2})(?:[:.](\d{2}))?\s*" + _MER, re.I)
_TIME_MER_RE = re.compile(r"(?<![\d:.])(\d{1,2})(?:[:.](\d{2}))?\s*" + _MER + r"(?![a-z])", re.I)
_TIME_24_RE = re.compile(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_NOON_RE = re.compile(r"\b(noon|midday)\b", re.I)
_BARE_AT_RE = re.compile(r"\bat\s+(\d{1,2})(?![\d:.]|\s*(?:am|pm|a\.m|p\.m|hour|hr|min))", re.I)
_TIME_ANY_RE = re.compile(r"\d\s*" + _MER + r"|\b\d{1,2}:\d{2}\b|\bnoon\b|\bat\s+\d{1,2}\b", re.I)


def _to_24h(h: int, mer: str) -> int | None:
    mer = mer.lower().replace(".", "")
    if not 1 <= h <= 12:
        return None
    if mer == "am":
        return 0 if h == 12 else h
    return 12 if h == 12 else h + 12


def _mk(h: int | None, mnt: str | None) -> time | None:
    if h is None:
        return None
    try:
        return time(h, int(mnt or 0))
    except ValueError:
        return None


def _extract_time(text: str) -> tuple[time | None, time | None, int | None]:
    """(start, end, ambiguous_hour)."""
    m = _RANGE_RE.search(text)
    if m:
        h1, m1, mer1, h2, m2, mer2 = int(m.group(1)), m.group(2), m.group(3), int(m.group(4)), m.group(5), m.group(6)
        end_h = _to_24h(h2, mer2)
        if mer1:
            start_h = _to_24h(h1, mer1)
        else:
            start_h = _to_24h(h1, mer2)
            if start_h is not None and end_h is not None and start_h >= end_h and mer2.lower().startswith("p"):
                start_h = _to_24h(h1, "am")  # "11 to 1pm" -> 11am
        s, e = _mk(start_h, m1), _mk(end_h, m2)
        if s and e and e > s:
            return s, e, None
    m = _TIME_MER_RE.search(text)
    if m:
        s = _mk(_to_24h(int(m.group(1)), m.group(3)), m.group(2))
        if s:
            return s, None, None
    m = _TIME_24_RE.search(text)
    if m:
        return time(int(m.group(1)), int(m.group(2))), None, None
    if _NOON_RE.search(text):
        return time(12, 0), None, None
    m = _BARE_AT_RE.search(text)
    if m and 1 <= int(m.group(1)) <= 12:
        return None, None, int(m.group(1))
    return None, None, None


_SOLO_RE = re.compile(r"\b(just me|only me|no attendees|no one|nobody|myself)\b", re.I)


def parse(text: str, today: date) -> Slots:
    s = Slots()
    s.solo = bool(_SOLO_RE.search(text))
    s.emails = list(dict.fromkeys(e.lower() for e in _EMAIL_RE.findall(text)))
    work = _SOLO_RE.sub(" ", _EMAIL_RE.sub(" ", text))
    s.title, work = _extract_title(work)
    if not s.emails:
        s.names = _extract_names(work)
    s.minutes, work = _extract_duration(work)
    s.day, work = _extract_day(work, today)
    s.start, s.end, s.ambiguous_hour = _extract_time(work)
    return s


def apply_meridiem(hour: int, answer: str) -> time | None:
    """Resolve an ambiguous bare hour from a follow-up of just "am"/"pm"."""
    a = answer.strip().lower().replace(".", "")
    if a in ("am", "pm"):
        h = _to_24h(hour, a)
        return time(h, 0) if h is not None else None
    return None


def missing_fields(s: Slots) -> set[str]:
    out = set()
    if not (s.emails or s.names or s.solo):
        out.add("who")
    if not s.day:
        out.add("day")
    if not s.start:
        out.add("time")
    return out


def fills_missing(pending: Slots, new: Slots) -> bool:
    """True if `new` supplies something `pending` was still waiting for."""
    need = missing_fields(pending)
    return bool(
        ("who" in need and (new.emails or new.names or new.solo))
        or ("day" in need and new.day)
        or ("time" in need and (new.start or new.ambiguous_hour))
    )

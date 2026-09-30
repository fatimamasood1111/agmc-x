"""
Calendar tool: Microsoft Graph API.
Same auth pattern and Azure app as email_tool.py — Graph app registrations
cover both once granted the right permissions.

get_upcoming_events only needs Calendars.Read (application, admin consent).

create_event needs a separate permission, Calendars.ReadWrite (application,
admin consent) — reading and writing are gated independently in Entra ID,
and this project only has Calendars.Read pending so far (see CLAUDE.md).
create_event is deliberately NOT registered as an LLM tool (see
orchestrator.TOOLS) — it must only ever be called from the /calendar-event
endpoint in main.py, itself only reachable by the CEO clicking Confirm on a
real approval card showing the exact subject/time/attendees about to be
booked. Same isolation pattern as flight_tool.get_booking_link and
email_tool.send_email; see flight_tool.py's docstring for the full reasoning.
"""
import requests
from datetime import datetime, timedelta, timezone
from config import config
from tools.email_tool import _get_token  # same Graph token works for both


def get_upcoming_events(days_ahead: int = 7) -> list[dict]:
    """Fetch calendar events between now and `days_ahead` days from now."""
    if not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET and config.MS_TENANT_ID):
        return _mock_events()

    token = _get_token()
    start = datetime.now(timezone.utc).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
    url = (
        f"https://graph.microsoft.com/v1.0/users/{config.MS_USER_EMAIL}/calendarview"
        f"?startDateTime={start}&endDateTime={end}"
        f"&$select=subject,start,end,location,attendees&$orderby=start/dateTime"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return [
        {
            "subject": e.get("subject"),
            "start": e.get("start", {}).get("dateTime"),
            "end": e.get("end", {}).get("dateTime"),
            "location": e.get("location", {}).get("displayName"),
            "attendees": [a.get("emailAddress", {}).get("address") for a in e.get("attendees", [])],
        }
        for e in items
    ]


def create_event(subject: str, start: str, end: str, attendees: list[str] | None = None, location: str | None = None) -> dict:
    """Create a calendar event / meeting invite. Only ever call this from
    the /calendar-event endpoint, after a real approval-card click — see
    module docstring. start/end are ISO datetimes with no timezone offset
    (e.g. "2026-09-25T14:00:00") — always interpreted as Asia/Dubai, the
    CEO's own timezone, since this assistant serves one mailbox in one
    place, not a multi-timezone user base.

    While MS_CLIENT_ID/SECRET/TENANT_ID aren't set, or if Graph rejects the
    call because Calendars.ReadWrite hasn't been granted admin consent yet,
    this simulates the booking and says so plainly — it never reports
    `created: True` unless Graph actually created the event."""
    if not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET and config.MS_TENANT_ID):
        return {"created": False, "mock": True, "note": "Simulated — Graph credentials aren't configured yet, no real event was created."}

    token = _get_token()
    url = f"https://graph.microsoft.com/v1.0/users/{config.MS_USER_EMAIL}/events"
    payload = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": "Asia/Dubai"},
        "end": {"dateTime": end, "timeZone": "Asia/Dubai"},
        "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in (attendees or [])],
    }
    if location:
        payload["location"] = {"displayName": location}

    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=15)
    if resp.status_code == 403:
        return {"created": False, "error": "Microsoft Graph rejected this — the Calendars.ReadWrite permission likely hasn't been granted admin consent yet."}
    resp.raise_for_status()
    return {"created": True, "web_link": resp.json().get("webLink")}


def _mock_events() -> list[dict]:
    return [
        {
            "subject": "Dealership performance review",
            "start": "2026-09-23T10:00:00",
            "end": "2026-09-23T11:00:00",
            "location": "AGMC HQ, Dubai",
            "attendees": ["ops.director@agmc.ae"],
        },
        {
            "subject": "Open slot",
            "start": "2026-09-23T14:00:00",
            "end": "2026-09-23T15:00:00",
            "location": None,
            "attendees": [],
        },
    ]

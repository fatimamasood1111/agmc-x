"""
Email tool: Microsoft Graph API.

Uses the OAuth2 client-credentials flow (app-only, no user sign-in
prompt) via MSAL, which is the right pattern for a backend service
acting on one fixed mailbox (the CEO's) rather than on behalf of
whoever is logged in.

get_recent_emails only needs Mail.Read (application, admin consent).

send_email needs a separate permission, Mail.Send (application, admin
consent) — reading and sending are gated independently in Entra ID, and
this project only has Mail.Read pending so far (see CLAUDE.md). send_email
is deliberately NOT registered as an LLM tool (see orchestrator.TOOLS) —
it must only ever be called from the /send-email endpoint in main.py,
itself only reachable by the CEO clicking Confirm on a real approval card
showing the exact to/subject/body about to be sent. Same isolation pattern
as flight_tool.get_booking_link; see that module's docstring for the full
reasoning.

open_outlook is a different kind of thing from the two functions above —
it doesn't touch mailbox data (nothing is read or sent), it just launches
the desktop Outlook application on this machine. Added 2026-09-25, direct
CEO request ("when I ask about email, open Outlook for me... directly").
Deliberately registered as a normal, DIRECT LLM tool (see orchestrator.TOOLS/
TOOL_DISPATCH) with no approval-card gate, unlike send_email/create_event —
the CEO explicitly asked for it to open immediately, and the blast radius is
tiny either way: it takes no arguments, so there's nothing an attacker could
substitute (contrast get_booking_link's ignav_id or send_email's to/subject/
body), and its only possible effect is a window opening, trivially closed if
unwanted. This is one narrow, deliberate exception to this project's
read-only design (see CLAUDE.md's opening paragraph) — it doesn't fit the
"could leak/lose/spend something real" category the approval-gate pattern
exists for.
"""
import os
import requests
import msal
from config import config

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _get_token():
    app = msal.ConfidentialClientApplication(
        config.MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        client_credential=config.MS_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Graph auth failed: {result.get('error_description')}")
    return result["access_token"]


def get_recent_emails(limit: int = 10) -> list[dict]:
    """Fetch the most recent inbox messages (subject, sender, preview, received time).
    Returns raw structured data — the orchestrator LLM does the summarizing,
    this tool's job is only to fetch."""
    if not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET and config.MS_TENANT_ID):
        return _mock_emails()

    token = _get_token()
    url = (
        f"https://graph.microsoft.com/v1.0/users/{config.MS_USER_EMAIL}"
        f"/mailFolders/inbox/messages"
        f"?$top={limit}&$select=subject,from,bodyPreview,receivedDateTime,isRead"
        f"&$orderby=receivedDateTime desc"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return [
        {
            "subject": m.get("subject"),
            "from": m.get("from", {}).get("emailAddress", {}).get("address"),
            "preview": m.get("bodyPreview"),
            "received": m.get("receivedDateTime"),
            "unread": not m.get("isRead", True),
        }
        for m in items
    ]


def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email from the CEO's mailbox. Only ever call this from the
    /send-email endpoint, after a real approval-card click — see module
    docstring. Sends exactly the to/subject/body passed in; this function
    does no composing or editing of its own.

    While MS_CLIENT_ID/SECRET/TENANT_ID aren't set, or if Graph rejects the
    call because Mail.Send hasn't been granted admin consent yet, this
    simulates the send and says so plainly — it never reports `sent: True`
    unless Graph actually accepted the message, since the CEO needs to be
    able to trust that flag."""
    if not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET and config.MS_TENANT_ID):
        return {"sent": False, "mock": True, "note": "Simulated — Graph credentials aren't configured yet, no real email was sent."}

    token = _get_token()
    url = f"https://graph.microsoft.com/v1.0/users/{config.MS_USER_EMAIL}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
    }
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=15)
    if resp.status_code == 403:
        return {"sent": False, "error": "Microsoft Graph rejected this — the Mail.Send permission likely hasn't been granted admin consent yet."}
    resp.raise_for_status()
    return {"sent": True}


def reply_email(entry_id: str, body: str, reply_all: bool = False) -> dict:
    """Mock-only fallback counterpart to outlook_com_tool.reply_to_mail —
    added 2026-09-29 alongside it, for the same reason send_email above
    exists next to outlook_com_tool.send_mail: main.py's /reply-email
    endpoint tries the real COM path first and only falls back to this if
    COM itself raises. There is no real Graph implementation here (unlike
    send_email) because the read side (outlook_com_tool.read_inbox) is
    COM-based, not Graph-based, in this app's active configuration — its
    entry_id values are Outlook EntryIDs, not Graph message IDs, so a real
    Graph reply call would have nothing valid to reply to even if
    Mail.Send were granted. This always simulates, honestly."""
    return {"sent": False, "mock": True, "note": "Simulated — Outlook COM reply failed and there's no real Graph-based reply path configured; no real reply was sent."}


def open_outlook() -> dict:
    """Launch the desktop Outlook application on this machine. Uses
    os.startfile("outlook") rather than a hardcoded install path —
    Windows resolves "outlook" via the same App Paths registry key
    (HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\OUTLOOK.EXE)
    that Win+R "outlook" and `start outlook` use, so this keeps working
    across Office updates/reinstalls without tracking the real .exe path
    ourselves. Confirmed live 2026-09-25 against this machine's actual
    Office install (Office16, click-to-run)."""
    try:
        os.startfile("outlook")
        return {"opened": True}
    except OSError as e:
        return {"opened": False, "error": f"Couldn't launch Outlook: {e}"}


def _mock_emails() -> list[dict]:
    """Used when MS_CLIENT_ID / MS_CLIENT_SECRET / MS_TENANT_ID aren't set yet,
    so the rest of the system can still be tested end to end."""
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

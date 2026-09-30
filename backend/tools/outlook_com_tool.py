"""
Reads and sends real mail by automating the Outlook DESKTOP application
directly via COM (win32com) — reading added 2026-09-25 as a fallback to the
Outlook MCP server (mcp_servers/outlook_server.py + mcp_outlook_client.py),
after hitting a real wall on the Graph/delegated-auth path: AGMC's tenant
requires admin approval even for Microsoft's own pre-registered "Microsoft
Graph Command Line Tools" app, and that approval was still pending. Sending
(send_mail) added the same day, direct CEO follow-up ("can it send mails as
well?").

send_mail is NOT a registered LLM tool and never will be reachable that
way — same isolation as email_tool.send_email's existing Graph path (see
that module's docstring): the model can only ever emit a fenced
```agmc-send-email``` proposal block, the frontend renders that as a real
approval card, and only a human clicking Confirm calls main.py's
/send-email endpoint, which is the ONE place this function is ever called
from. Building a real send capability here does not change that gate —
COM makes the SEND mechanically easy, which is exactly why the approval
step is the thing doing the actual safety work, not any difficulty in
reaching the function itself.

This is a GENUINELY DIFFERENT mechanism, not a workaround of that wall —
it never touches Microsoft Graph or Entra ID at all. It automates the
already-open, already-signed-in Outlook.exe process on this machine
directly (the same mechanism Outlook VBA macros and COM add-ins have used
for decades), reading mail through Outlook's own already-authenticated
session instead of asking Microsoft's cloud for a new OAuth token. No
admin consent applies here because no new Graph permission is ever
requested — from Entra ID's point of view, nothing new is happening at
all.

Requires Outlook desktop to actually be running and signed in — pair this
with email_tool.open_outlook() (see orchestrator.py's system prompt, which
already calls both together). win32com.client.Dispatch will silently
launch Outlook if it isn't running at all, but that can take several
seconds to fully load a profile — calling open_outlook first, same turn,
is what makes this reliable in practice rather than racy.

read_calendar/create_event (added 2026-09-25, same day, direct CEO
follow-up) apply the identical reasoning to the calendar folder instead of
the inbox — see each function's own docstring for specifics.

Windows-only, like the rest of this project (win32com has no equivalent
on macOS/Linux).
"""
import re
from datetime import datetime, timedelta, timezone
import pythoncom
import win32com.client

OL_FOLDER_INBOX = 6
OL_FOLDER_CALENDAR = 9
MAX_ATTENDEES_SHOWN = 15
# Per-email cap on the body text handed to the model. Sized against the
# local model's 8192-token context (config.OLLAMA_NUM_CTX): the system prompt
# plus tool schemas already use ~3.5K of it, and 10 emails x 600 chars is
# ~1.5K more — see the note on read_inbox for why this replaced a 200-char cut.
MAX_BODY_CHARS = 600

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# Where a reply chain / forward header / footer begins — everything after
# the first hit is old quoted material or boilerplate, not this email's own
# message. The "From:" pattern requires a "Sent:" line right after it so a
# body that merely starts with "From:" isn't cut to nothing.
_TAIL_MARKERS_RE = re.compile(
    r"(?im)"
    r"^\s*-{2,}\s*original message\s*-{2,}"
    r"|^\s*on .{5,100} wrote:\s*$"
    r"|^\s*from:.*\n(?:.*\n){0,2}?\s*sent:"
    r"|^\s*_{5,}\s*$"
    r"|^\s*(?:this e-?mail|this message|the information (?:in|contained)|disclaimer|confidentiality notice)\b"
    r"|^\s*sent from my (?:iphone|ipad|android|galaxy)"
)


def _clean_body(text: str, limit: int = MAX_BODY_CHARS) -> tuple[str, bool]:
    """Turns a raw Outlook plain-text body into the part worth summarizing:
    drops quoted reply chains and legal footers, replaces raw URLs with
    "[link]" (tracking URLs can be hundreds of characters of zero meaning),
    collapses whitespace, and truncates at a sentence/word boundary. Returns
    (text, was_truncated)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    match = _TAIL_MARKERS_RE.search(text)
    if match:
        text = text[:match.start()]
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))
    text = _URL_RE.sub("[link]", text)
    text = re.sub(r"(?:\[link\][\s,;|]*){2,}", "[link] ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("? "), cut.rfind("! "))
    cut = cut[:boundary + 1] if boundary > limit * 0.5 else cut[:cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip() + " …", True


def _clean_sender_address(item) -> str:
    """MailItem.SenderEmailAddress returns a legacy Exchange X.500 DN
    (e.g. "/O=EXCHANGELABS/OU=.../CN=...") instead of a real email address
    for internal, Exchange-routed senders — confirmed live 2026-09-25, the
    very first real inbox message hit this. Resolve to a real SMTP address
    via the Exchange user object when that's the case; fall back to the
    raw value for anything else (external senders already come through as
    plain SMTP addresses)."""
    try:
        if item.SenderEmailType == "EX":
            exchange_user = item.Sender.GetExchangeUser()
            if exchange_user is not None:
                return exchange_user.PrimarySmtpAddress
    except Exception:
        pass
    return item.SenderEmailAddress or ""


def _recipient_smtp_address(recipient) -> str:
    """Shared EX-vs-SMTP resolution for a single Recipient object — same
    underlying problem as _clean_sender_address, factored out 2026-09-25
    so resolve_contact() (a fresh CreateRecipient()) and
    _clean_recipient_addresses() (existing meeting attendees) don't
    duplicate this a third time."""
    try:
        if recipient.AddressEntry.AddressEntryUserType == 0:  # olExchangeUserAddressEntry
            exchange_user = recipient.AddressEntry.GetExchangeUser()
            if exchange_user is not None:
                return exchange_user.PrimarySmtpAddress
    except Exception:
        pass
    return recipient.Address


def _clean_recipient_addresses(item) -> list[str]:
    """Same legacy-DN problem as _clean_sender_address, but for meeting
    attendees: AppointmentItem.RequiredAttendees is a semicolon-joined
    string of DISPLAY NAMES, not addresses, and resolving each one through
    .Recipients hits the identical EX-vs-SMTP split. Only required
    attendees are surfaced — optional attendees aren't part of
    calendar_tool.get_upcoming_events's existing shape either, so this
    just matches that."""
    addresses = []
    for recipient in item.Recipients:
        try:
            if recipient.Type != 1:  # 1 = olRequired
                continue
            addresses.append(_recipient_smtp_address(recipient))
        except Exception:
            continue
    return addresses


def read_inbox(limit: int = 10) -> dict:
    """Same return shape as email_tool.get_recent_emails's list, wrapped
    with a "mock" flag for consistency with the MCP path (see
    orchestrator._read_emails) — always mock: False here, since this either
    returns real data or raises (no separate mock mode; Outlook either
    automates or it doesn't).

    COM requires the CALLING THREAD to be initialized (CoInitialize)
    before touching any COM object — found 2026-09-25: this worked fine
    standalone (main thread) but silently failed when called from a real
    /chat request, because FastAPI runs sync endpoints in a threadpool
    worker thread, which has never called CoInitialize. Every call here
    pairs Initialize/Uninitialize explicitly rather than assuming it — a
    threadpool thread gets REUSED across requests, and this pattern is
    the correct, safe one for that (calling CoInitialize when already
    initialized on that thread just returns S_FALSE, not an error)."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(OL_FOLDER_INBOX)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)  # newest first

        emails = []
        for item in items:
            if len(emails) >= limit:
                break
            # A folder can contain non-mail items (meeting requests,
            # receipts, etc.) that don't have these properties — skip
            # rather than crash.
            try:
                subject = item.Subject
                # Was a flat 200-char slice of the raw body until 2026-09-30:
                # that is usually just the greeting and first line, so the
                # model had nothing real to summarize and (reasonably) just
                # listed subjects — the CEO's report under the local model.
                body, truncated = _clean_body(item.Body)
                try:
                    sender_name = item.SenderName or ""
                except Exception:
                    sender_name = ""
                emails.append({
                    "subject": subject,
                    "from_name": sender_name,
                    "from": _clean_sender_address(item),
                    "body": body,
                    "body_truncated": truncated,
                    "received": str(item.ReceivedTime),
                    "unread": bool(item.UnRead),
                    # Added 2026-09-29 so a real reply is possible at all —
                    # see reply_to_mail below. EntryID is Outlook's own
                    # stable per-item identifier (stable as long as the
                    # item isn't moved to a different folder), the same
                    # thing GetItemFromID needs to look the message back up.
                    "entry_id": item.EntryID,
                })
            except AttributeError:
                continue

        return {"emails": emails, "mock": False}
    finally:
        pythoncom.CoUninitialize()


def send_mail(to: str, subject: str, body: str) -> dict:
    """Send a real email from the CEO's own signed-in Outlook profile.
    Only ever call this from main.py's /send-email endpoint — see this
    module's docstring for why. Sends exactly what's passed in; does no
    composing of its own, matching email_tool.send_email's contract."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # olMailItem
        mail.To = to
        mail.Subject = subject
        mail.Body = body
        mail.Send()
        return {"sent": True}
    finally:
        pythoncom.CoUninitialize()


def reply_to_mail(entry_id: str, body: str, reply_all: bool = False) -> dict:
    """Sends a REAL reply to an existing email, preserving Outlook's own
    threading — added 2026-09-29, direct CEO report ("when I ask it to
    reply to a mail, it just sends a new one rather than replying to that
    specific email"). Root cause of the original complaint: send_mail
    above only ever composes a brand-new MailItem, which has no way to
    thread against anything — there was no reply-specific path at all.

    MailItem.Reply()/ReplyAll() (real Outlook object-model methods, the
    same ones the desktop app's own Reply button calls) return a NEW
    MailItem already correctly addressed (original sender, or sender +
    all recipients for ReplyAll), already subjected ("RE: ..."), and
    already threaded (In-Reply-To/References/conversation topic are set
    by Outlook itself, not something this function constructs by hand) —
    with the original message quoted in its Body. `body` is PREPENDED
    above that existing quoted text, never replacing it, so the result
    reads like a genuine threaded reply, not a new message that happens
    to mention the old one.

    Only ever call this from main.py's /reply-email endpoint, after a
    real approval-card click — same isolation as send_mail above. entry_id
    must be a real Outlook EntryID from a read_inbox result THIS
    conversation actually saw; main.py cross-checks that against
    orchestrator.get_known_email before ever calling this, the identical
    structural defense flight booking already uses for ignav_id."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        original = ns.GetItemFromID(entry_id)
        reply = original.ReplyAll() if reply_all else original.Reply()
        reply.Body = body + "\n\n" + reply.Body
        reply.Send()
        return {"sent": True}
    finally:
        pythoncom.CoUninitialize()


def read_calendar(days_ahead: int = 7) -> dict:
    """Same return shape as calendar_tool.get_upcoming_events, added
    2026-09-25 alongside send_mail (direct CEO follow-up, "can it schedule
    meetings as well?") — reads the CEO's real Outlook calendar folder
    directly via COM, same mechanism/reasoning as read_inbox above.

    Filters in plain Python, not Items.Restrict() — found live 2026-09-25:
    a real event (confirmed to genuinely exist via an unfiltered scan of
    the same folder) didn't match a Restrict() date-range string built
    from strftime, even with the documented locale-style format. Restrict()
    combined with IncludeRecurrences=True is a known-flaky combination in
    Outlook's own object model; comparing item.Start directly against
    Python datetimes (pywin32 returns these already timezone-aware, in
    UTC — confirmed via that same scan) sidesteps the whole restrict-string
    DSL rather than debugging its exact quirk further. Less "clever," more
    reliably correct — don't revert to Restrict() without re-verifying
    live first."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        calendar = ns.GetDefaultFolder(OL_FOLDER_CALENDAR)
        items = calendar.Items
        items.IncludeRecurrences = True  # expand recurring series into individual occurrences
        items.Sort("[Start]")

        now = datetime.now(timezone.utc)
        window_end = now + timedelta(days=days_ahead)

        events = []
        for item in items:
            try:
                start = item.Start
                if not (now <= start <= window_end):
                    continue
                attendees = _clean_recipient_addresses(item)
                # Found live 2026-09-25: a real company-wide meeting on this
                # calendar had 300+ required attendees — dumping all of them
                # into the tool result wastes real context for no benefit
                # (the CEO doesn't need 300 email addresses to know he has a
                # town hall). MAX_ATTENDEES_SHOWN caps it with a plain count
                # of the rest, same "+N more" idea as file_tool's MAX_*
                # constants elsewhere in this codebase.
                if len(attendees) > MAX_ATTENDEES_SHOWN:
                    hidden = len(attendees) - MAX_ATTENDEES_SHOWN
                    attendees = attendees[:MAX_ATTENDEES_SHOWN] + [f"+{hidden} more"]
                events.append({
                    "subject": item.Subject,
                    "start": str(start),
                    "end": str(item.End),
                    "location": item.Location or None,
                    "attendees": attendees,
                })
            except AttributeError:
                continue

        return {"events": events, "mock": False}
    finally:
        pythoncom.CoUninitialize()


def create_event(subject: str, start: str, end: str, attendees: list[str] | None = None, location: str | None = None) -> dict:
    """Create a real calendar event / meeting via COM. Same isolation as
    send_mail above — only ever call this from main.py's /calendar-event
    endpoint, after a real approval-card click. start/end are naive ISO
    datetimes (e.g. "2026-09-25T14:00:00"), matching calendar_tool.
    create_event's existing contract — parsed and assigned as real
    datetime objects, not left as strings, since win32com's COM marshalling
    needs an actual datetime for a reliable Date-property assignment rather
    than a string Outlook has to guess how to parse.

    If attendees are given, this actually SENDS them a real meeting invite
    (Outlook's own behavior once MeetingStatus is set to olMeeting and
    .Send() is called) — exactly as consequential as send_mail, which is
    why this function has the identical approval-gate isolation."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        appt = outlook.CreateItem(1)  # olAppointmentItem
        appt.Subject = subject
        appt.Start = datetime.fromisoformat(start)
        appt.End = datetime.fromisoformat(end)
        if location:
            appt.Location = location

        if attendees:
            appt.MeetingStatus = 1  # olMeeting
            for address in attendees:
                appt.Recipients.Add(address)
            appt.Recipients.ResolveAll()
            appt.Send()  # saves AND sends invites to all attendees
        else:
            appt.Save()

        return {"created": True}
    finally:
        pythoncom.CoUninitialize()


def resolve_contact(name: str) -> dict:
    """Look up a real email address for a name, via Outlook's own name
    resolution — the identical mechanism behind typing a partial name into
    the "To:" field and having it autocomplete against personal Contacts
    AND the company's Exchange directory (Global Address List). Added
    2026-09-25, direct CEO follow-up after being asked for "Fatima"'s email
    manually when drafting a meeting — this is a pure lookup with no side
    effects at all (unlike send_mail/create_event), so it's fine to
    register as a normal direct LLM tool, no approval gate needed.

    Real, live-confirmed risk worth knowing: an AMBIGUOUS name (multiple
    people share it) resolves to WHICHEVER match Outlook's own heuristic
    picks — silently, with no "which one did you mean?" prompt in headless
    automation (that only happens in Outlook's own interactive UI). This
    function returns BOTH the resolved display name and email specifically
    so whatever proposal block the model builds from this shows the CEO
    the actual resolved identity in the approval card before he confirms —
    that review step is the real safety net for a wrong resolution, not
    anything in this function itself."""
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        recipient = ns.CreateRecipient(name)
        recipient.Resolve()
        if not recipient.Resolved:
            return {"resolved": False, "note": f"Couldn't resolve '{name}' to a contact or directory entry."}
        return {"resolved": True, "name": recipient.Name, "email": _recipient_smtp_address(recipient)}
    finally:
        pythoncom.CoUninitialize()

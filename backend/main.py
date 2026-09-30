"""
The API the chat interface talks to.
POST /chat        — send a message, get a reply as one JSON response
POST /chat/stream  — same thing, but streamed as Server-Sent Events (SSE)
POST /upload       — upload a file (PDF, Word, Excel, text) so the assistant
                      can answer questions about it in this session
Run with: uvicorn main:app --reload --port 8000
"""
import json
import os
import tempfile
import traceback
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import orchestrator
from config import config
from tools import file_tool, flight_tool, email_tool, calendar_tool, outlook_com_tool

app = FastAPI(title="AGMC X")

# Local demo: wide open. Before this touches anything beyond your own
# laptop, restrict allow_origins to the real frontend's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    reply = orchestrator.run(req.session_id, req.message)
    return ChatResponse(reply=reply)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    def event_generator():
        # NOT `finally: yield "[DONE]"` — found 2026-09-24: when the
        # client disconnects (closes the connection, network hiccup, the
        # Electron window closing mid-response, etc.), Starlette closes
        # this generator by throwing GeneratorExit into it. A `finally`
        # that yields unconditionally then yields ONE MORE TIME while the
        # generator is being told to shut down — which violates Python's
        # generator protocol (a closing generator must not yield again)
        # and raises "RuntimeError: generator ignored GeneratorExit"
        # (visible in the terminal as "Exception ignored while closing
        # generator..."). GeneratorExit isn't an Exception subclass, so
        # `except Exception` never caught it either way — the bug was
        # purely the `finally: yield`. Explicit yields at the two REAL
        # completion points (end of the try, end of the except) instead:
        # a genuine GeneratorExit now propagates straight through with no
        # further yield, exactly what the protocol requires.
        try:
            for event in orchestrator.run_stream(req.session_id, req.message):
                if event["type"] == "text":
                    yield f"data: {json.dumps({'text': event['text']})}\n\n"
                elif event["type"] == "inbox":
                    yield f"data: {json.dumps({'inbox': event['data']})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': event['tool']})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            # Print the real traceback server-side — without this, a
            # mid-stream failure (e.g. the Anthropic API rejecting the
            # request) is completely invisible in the uvicorn terminal: the
            # HTTP response already started with a 200 before the exception
            # hit, so the request log line looks clean, and the frontend
            # just shows "Offline demo" with no indication anything broke.
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/upload")
async def upload(session_id: str = Form(...), file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        raw = await file.read()
        tmp.write(raw)
        tmp_path = tmp.name

    print(f"[upload] {file.filename}: received {len(raw)} bytes, saved to {tmp_path}")

    try:
        result = file_tool.read_local_file(tmp_path)
    finally:
        os.unlink(tmp_path)  # never keep the uploaded file on disk after reading it

    if "error" in result:
        print(f"[upload] {file.filename}: FAILED — {result['error']}")
        return {"ok": False, "error": result["error"]}

    if "image_base64" in result:
        print(f"[upload] {file.filename}: image ({result['media_type']}), added to session")
        orchestrator.add_uploaded_image(session_id, file.filename, result["image_base64"], result["media_type"])
        return {"ok": True, "filename": file.filename, "truncated": False}

    content = result["content"]
    print(f"[upload] {file.filename}: extracted {len(content)} chars. Preview: {content[:150]!r}")

    if result.get("images"):
        # A PDF with embedded photos/graphics, not just text — added
        # 2026-09-24. Same real-vision-input mechanism as the image_base64
        # branch above, just combined with real text this time.
        print(f"[upload] {file.filename}: {len(result['images'])} embedded image(s) also added to session")
        orchestrator.add_uploaded_pdf(session_id, file.filename, content, result["images"])
        return {"ok": True, "filename": file.filename, "truncated": result.get("truncated", False)}

    orchestrator.add_uploaded_file(session_id, file.filename, content)
    return {"ok": True, "filename": file.filename, "truncated": result.get("truncated", False)}


class BookingLinkRequest(BaseModel):
    session_id: str
    ignav_id: str


@app.post("/booking-link")
def booking_link(req: BookingLinkRequest):
    """The ONE place a real external call happens for a flight the CEO wants
    to book — and it is deliberately not reachable through /chat or the LLM
    tool loop. It only fires when the frontend's approval-card Confirm
    button is clicked, i.e. only after a human looked at this exact fare and
    approved it. It returns a link to complete the purchase on the airline's
    own site; nothing here spends money or holds a seat. See flight_tool.py.

    The ignav_id is cross-checked against orchestrator.get_known_itinerary
    before anything else happens: the model's system prompt says "never
    invent an ignav_id", but a prompt is a request, not an enforcement, and
    injected content (a poisoned search result, an uploaded file) could in
    principle talk the model into proposing one anyway. This check makes
    that structurally impossible to act on, not just conversationally
    discouraged — see the comment on orchestrator._known_itineraries. It also
    supplies the origin/destination/dates for flight_tool's generated
    search_links from the real recorded search, never from client text.
    """
    itin = orchestrator.get_known_itinerary(req.session_id, req.ignav_id)
    if itin is None:
        return {"ok": False, "error": "This fare wasn't from a search in this conversation — please search again and confirm from those results."}
    try:
        result = flight_tool.get_booking_link(
            req.ignav_id, itin["origin"], itin["destination"], itin["depart_date"], itin["return_date"],
            cabin_class=itin.get("cabin_class"), outbound=itin.get("outbound"), inbound=itin.get("inbound"),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # Not a hard failure even with zero priced options: search_links (see
    # flight_tool._generated_search_links) are always present when the
    # itinerary's route/dates are known, and are a reliable fallback path
    # regardless of what Ignav returned — so this is "ok" with a note, not
    # an error, whenever there's at least something the CEO can act on.
    note = None
    if not result.get("options"):
        if result.get("raw_count", 0) > 0:
            # Results came back, but every seller failed the trusted-provider
            # check in flight_tool.TRUSTED_OTA_DOMAINS — don't call this
            # "expired", that would send the CEO looking for a workaround
            # instead of understanding why nothing was shown.
            note = "Found booking options for this fare, but none were from a seller we trust — withheld rather than risk sending you to a disreputable site."
        else:
            note = "No direct booking link available for this fare anymore — it may have expired."
    if not result.get("options") and not result.get("search_links"):
        return {"ok": False, "error": note or "Nothing available for this fare — please search again."}
    return {"ok": True, "note": note, **result}


class SendEmailRequest(BaseModel):
    session_id: str
    to: str
    subject: str
    body: str


@app.post("/send-email")
def send_email(req: SendEmailRequest):
    """The ONE place a real email gets sent — deliberately not reachable
    through /chat or the LLM tool loop (email_tool.send_email is never
    registered as an LLM tool; see its docstring). Only fires when the
    frontend's approval-card Confirm button is clicked.

    Unlike /booking-link, there's no prior recorded "real" value to check
    this against — an email's to/subject/body is new content the model
    composed, not a reference to something already fetched — so there's no
    known-itinerary-style cross-check possible here. The safeguard is
    structural in a different way: chat text alone (including injected
    content) can never trigger this endpoint, only a real UI click can, and
    the to/subject/body sent here are exactly what the approval card showed
    the CEO before that click — nothing is re-generated or substituted at
    send time.

    Tries real sending via Outlook COM automation first (added 2026-09-25,
    same mechanism/reasoning as outlook_com_tool.read_inbox — automates the
    CEO's own already-signed-in Outlook desktop app directly, no Graph, no
    admin consent needed) — falls back to the older email_tool.send_email
    Graph path (mock unless Graph credentials are configured) only if COM
    itself fails (Outlook not open, etc.), mirroring orchestrator._read_emails's
    same COM-first-then-Graph-fallback pattern on the read side.
    """
    print(f"[send-email] session={req.session_id} to={req.to} subject={req.subject!r}")
    try:
        result = outlook_com_tool.send_mail(req.to, req.subject, req.body)
    except Exception as e:
        try:
            result = email_tool.send_email(req.to, req.subject, req.body)
            result["note"] = f"Outlook COM send failed ({e}) — used the Graph/mock path instead."
        except Exception as e2:
            return {"ok": False, "error": f"Both send paths failed — COM: {e}; Graph: {e2}"}
    return {"ok": True, **result}


@app.get("/reply-target")
def reply_target(session_id: str, entry_id: str):
    """What the reply approval card shows as "Replying to" — looked up from
    orchestrator's record of emails a real inbox read returned, never from
    anything the model wrote in its proposal block. Added 2026-09-30: the
    card used to show only the reply text, so the CEO clicked "Send reply"
    without seeing WHO it was going to or which email it answered — a gap for
    every provider, and one that matters more now that code (not the model)
    picks the email."""
    known = orchestrator.get_known_email(session_id, entry_id)
    if not known:
        return {"ok": False}
    return {"ok": True, "from_name": known.get("from_name"), "from": known.get("from"), "subject": known.get("subject")}


class ReplyEmailRequest(BaseModel):
    session_id: str
    entry_id: str
    body: str
    reply_all: bool = False


@app.post("/reply-email")
def reply_email(req: ReplyEmailRequest):
    """The ONE place a real reply gets sent — added 2026-09-29, direct CEO
    report that asking to "reply to" an email just sent a brand-new one
    instead (agmc-send-email has no concept of an existing message to
    thread onto). Same isolation as /send-email: never reachable through
    /chat or the LLM tool loop, only from a real approval-card Confirm
    click.

    UNLIKE /send-email, this DOES have a known-real value to cross-check
    against — entry_id is a reference to something read_emails actually
    returned, exactly like /booking-link's ignav_id — so the same
    structural defense applies: refuse any entry_id not in
    orchestrator.get_known_email BEFORE ever reaching Outlook, regardless
    of what the model claimed. This makes a fabricated entry_id
    (confirmed possible under Ollama, same failure class as the booking
    fabrication — see orchestrator._validate_reply_blocks) structurally
    harmless, not just discouraged by prompt wording.

    Tries real replying via Outlook COM automation first
    (outlook_com_tool.reply_to_mail — preserves real threading, unlike a
    fresh compose) — falls back to email_tool.reply_email (always a mock;
    see that function's own docstring for why there's no real Graph path
    here) only if COM itself fails."""
    print(f"[reply-email] session={req.session_id} entry_id={req.entry_id!r} reply_all={req.reply_all}")
    known = orchestrator.get_known_email(req.session_id, req.entry_id)
    if known is None:
        return {"ok": False, "error": "This email wasn't from an inbox read in this conversation — please read the inbox again and confirm from those actual results."}
    try:
        result = outlook_com_tool.reply_to_mail(req.entry_id, req.body, req.reply_all)
    except Exception as e:
        try:
            result = email_tool.reply_email(req.entry_id, req.body, req.reply_all)
            result["note"] = f"Outlook COM reply failed ({e}) — used the mock path instead."
        except Exception as e2:
            return {"ok": False, "error": f"Both reply paths failed — COM: {e}; mock: {e2}"}
    return {"ok": True, **result}


class CalendarEventRequest(BaseModel):
    session_id: str
    subject: str
    start: str
    end: str
    attendees: list[str] = []
    location: str = ""


@app.post("/calendar-event")
def calendar_event(req: CalendarEventRequest):
    """The ONE place a real calendar event gets created — same isolation
    and reasoning as /send-email above, see there.

    Tries real creation via Outlook COM automation first (added 2026-09-25,
    same reasoning as /send-email's COM-first change — automates the CEO's
    own already-signed-in Outlook desktop app, no Graph, no admin consent
    needed), falling back to the older calendar_tool.create_event Graph
    path (mock unless Graph credentials are configured) only if COM itself
    fails."""
    print(f"[calendar-event] session={req.session_id} subject={req.subject!r} start={req.start} end={req.end}")
    try:
        result = outlook_com_tool.create_event(req.subject, req.start, req.end, req.attendees, req.location or None)
    except Exception as e:
        try:
            result = calendar_tool.create_event(req.subject, req.start, req.end, req.attendees, req.location or None)
            result["note"] = f"Outlook COM event creation failed ({e}) — used the Graph/mock path instead."
        except Exception as e2:
            return {"ok": False, "error": f"Both creation paths failed — COM: {e}; Graph: {e2}"}
    return {"ok": True, **result}


class ModelRequest(BaseModel):
    provider: str


@app.get("/model")
def get_model():
    """Lets the frontend's header toggle sync to whatever's actually
    active on load/reconnect, rather than assuming Claude every time.
    ollama_model is included so the dropdown can label the local option
    with the real model name (e.g. "qwen2.5:3b") instead of a generic
    "Local" — added 2026-09-29 so that label can never drift out of sync
    with config.OLLAMA_MODEL if it's ever changed."""
    return {"provider": orchestrator.get_provider(), "ollama_model": config.OLLAMA_MODEL}


@app.post("/model")
def set_model(req: ModelRequest):
    """Flips the runtime Claude<->local-model switch — see
    orchestrator.set_provider's docstring. Global, not per-session: one
    CEO, one active conversation."""
    try:
        orchestrator.set_provider(req.provider)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "provider": orchestrator.get_provider()}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status(refresh: bool = False):
    """Whether the ACTIVE model can actually answer right now — distinct
    from /health, which only proves this backend process is running. The
    header's "Connected" indicator used to be driven by /health alone, so
    on the CEO's boss's PC it said "Connected" while every reply failed
    (local model not downloaded yet, Claude key out of credit)."""
    return orchestrator.provider_status(refresh=refresh)
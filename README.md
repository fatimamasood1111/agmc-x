# AGMC Assist — read-only build

Chat interface → orchestrator → three read-only tools (email, calendar,
offers, social mentions). No action tools (booking, sending, writing)
are included in this build on purpose.

## 1. Install Python dependencies

```
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up your credentials

```
cp ../.env.example .env
```

Then open `backend/.env` and fill in what you have. Every value left
blank makes that one tool run on mock data instead of failing — so you
can get this running today with just the Anthropic key, and add the
rest as each account gets approved.

## 3. Run the backend

```
uvicorn main:app --reload --port 8000
```

Visit `http://localhost:8000/health` — you should see `{"status":"ok"}`.

## 4. Open the frontend

Double-click `frontend/agmc-assistant-popup.html`. The status dot in
the popup turns green once it reaches the backend; if the backend
isn't running, it falls back to the offline canned demo automatically
so it's never broken to show someone.

---

## Keys, APIs, and auth — what each one is and how to get it

### 1. Anthropic API key — required, this is the orchestrator's brain
- Get it at **console.anthropic.com** → API Keys
- One key, drop it in `ANTHROPIC_API_KEY`
- This is the only credential every other tool depends on nothing —
  get this one first

### 2. Microsoft Graph (email + calendar) — one setup covers both
This needs an **app registration in Microsoft Entra ID** (the current
name for Azure AD), done by whoever administers AGMC's Microsoft 365:
1. Entra admin center → App registrations → New registration
2. Note the **Application (client) ID** and **Directory (tenant) ID**
3. Certificates & secrets → New client secret → note the value (shown once)
4. API permissions → Add → Microsoft Graph → **Application permissions**
   (not delegated — this runs unattended, with no user signed in) →
   add `Mail.Read` and `Calendars.Read`
5. Click **Grant admin consent** — this step requires a Global or
   Application Administrator, it won't work otherwise
6. Fill in `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, and
   `MS_USER_EMAIL` (the CEO's actual mailbox address)

Because this is an *application* permission rather than delegated, the
app can read that one mailbox without the CEO ever seeing a login
prompt — appropriate for a backend service, but it also means whoever
holds that client secret can read the CEO's email, so treat it with
the same care as a master password.

### 3. X (Twitter) API v2 — for mention monitoring
1. developer.x.com → apply for a developer account (used to be
   instant, may now involve a short review — this has changed more
   than once, so budget a few days just in case)
2. Create a Project and App inside the developer portal
3. Generate a **Bearer Token** — that's the only credential this tool needs
4. Fill in `X_BEARER_TOKEN` and `X_HANDLE` (AGMC's handle, no @)

### 4. Meta Graph API — Instagram + Facebook mentions
1. developers.facebook.com → create an app (type: Business)
2. Add the **Instagram Graph API** product
3. The Instagram account must be a **Business or Creator account**
   linked to a Facebook Page — personal IG accounts aren't supported
4. Generate a **long-lived Page access token** (short-lived tokens
   expire in about an hour, not useful for a running service)
5. Fill in `META_ACCESS_TOKEN`, `META_IG_BUSINESS_ID`, `META_PAGE_ID`

---

## What's deliberately not in this build

No booking, no sending emails, no writing to the calendar, no posting
to social. Every tool here only fetches and reads. When the action
tools (calendar writes, flight/hotel booking) get built, they should
go through an approval step before anything executes — see the
architecture discussion this project came from.

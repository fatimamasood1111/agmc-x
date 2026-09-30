# AGMC Assist — project context

Read-only personal assistant for the CEO of AGMC (BMW dealership, Dubai —
confirmed directly by the CEO 2026-09-24 that AGMC does NOT sell
Mercedes-Benz, correcting this doc's own wrong assumption from the start of
the project; see the gotcha below before assuming otherwise again).
`frontend/` is a single self-contained HTML popup; `backend/` is a
FastAPI service where Claude decides which tools/sub-agents to call.
**No write/action tools exist** (no sending email, no writing calendar) —
this is a deliberate security-driven scope limit, not an oversight. Don't
add an action tool without an explicit human-approval step in front of it.
The one narrow exception, added deliberately with a structural (not just
conversational) approval gate, is flight booking-link handoff — see
"Current status" below and the docstring in `tools/flight_tool.py`.
A second, different kind of exception (added 2026-09-25): `open_outlook`
launches the desktop Outlook app directly, no approval gate at all — see
the dedicated gotcha below for why that's a deliberate, safe choice here
and not an inconsistency with the approval-gate pattern above.

## Stack & running it

- Python 3.14, Windows, FastAPI + Anthropic SDK. No framework on the frontend.
- Run backend: `cd backend && venv\Scripts\activate.bat && uvicorn main:app --reload --port 8000`
- Open frontend: double-click `frontend/agmc-assistant-popup.html`, OR use
  the "AGMC Assist" Desktop shortcut (added 2026-09-23, see below) for a
  proper windowed app instead of a browser tab.
- **Always restart uvicorn after any backend `.py` edit** — don't assume `--reload` caught it; verify.
- **Killing "the uvicorn process" by matching `uvicorn` in the command line
  does NOT actually stop it under `--reload` on Windows, and the orphan
  left behind silently serves OLD code forever** — this caused a real,
  hours-long false debugging trail 2026-09-25 (see the `outlook_com_tool`
  send-mail gotcha below for the full story) before the actual mechanism
  was found. `--reload` runs as a RELOADER/supervisor process (that one
  DOES have `uvicorn` in its command line) which spawns the actual WORKER
  as a separate process via Python's `multiprocessing` — and on Windows
  that worker's own command line looks like `python.exe -c "from
  multiprocessing.spawn import spawn_main; spawn_main(parent_pid=...,
  pipe_handle=...)" --multiprocessing-fork`, which contains neither
  `uvicorn` NOR `main:app` NOR anything else recognizable. Killing only
  the reloader (matching on `*uvicorn*`) leaves this worker alive,
  orphaned, still bound to port 8000, still running whatever code was
  loaded when IT started — every subsequent restart attempt then fails to
  bind at all (`WinError 10048`) and exits, while health checks and real
  requests keep silently hitting the surviving orphan the whole time,
  making it look exactly like "my restart worked but the code change
  didn't take." **The correct kill command matches on `python.exe`
  processes generally (or specifically `*multiprocessing-fork*`), not on
  `*uvicorn*`** — `Get-CimInstance Win32_Process -Filter "Name='python.exe'"`
  with no command-line filter, or filtered to `-like '*multiprocessing-fork*'`
  to be precise, is what actually finds these. A stray `WinError 10048` in
  a freshly-started instance's stderr (redirect stdout/stderr to a file to
  actually see this — a hidden-window background process's errors are
  otherwise invisible) is the tell that an old orphan is still squatting
  on the port; a clean start log ends at "Application startup complete"
  with no error line after it.

## Desktop app (added 2026-09-23, rebuilt on Electron 2026-09-24)

CEO's boss found the raw HTML-in-a-browser-tab experience unprofessional.
The actual ask, once clarified: not a normal app window, but a small round
"chat bubble" widget sitting in the bottom-right corner of the screen
(like Messenger's chat heads) that expands into the full chat panel on
click and shrinks back on close — the desktop equivalent of what
`agmc-assistant-popup.html` already does when embedded as a floating
widget on a real webpage (mascot + palette), just running directly on the
desktop instead of inside a browser tab.

**First attempt was `pywebview` (Python) — abandoned after extensive,
unresolved reliability problems, not for lack of trying.** Kept only as
project history so it's never quietly retried:
- pywebview's `transparent=True` flag rendered as a solid opaque square —
  its winforms.py source only makes the WebView2 CONTROL transparent
  relative to its PARENT FORM, which still paints solid.
- A manual Win32 color-key layering hack (`WS_EX_LAYERED` +
  `SetLayeredWindowAttributes`) ALSO rendered a solid square — WebView2
  composites via its own GPU surface that bypasses that legacy mechanism
  entirely.
- `Form.Region` window-shape clipping (OS/DWM-level, not a rendering
  trick) DID work for real transparency and correct shapes — confirmed
  with a real screenshot of a correctly-shaped, see-through rounded panel.
- But exact window SIZING never became reliable no matter what was tried:
  DPI-awareness API calls (confirmed successfully set to Per-Monitor-V2 —
  didn't help), explicit `min_size`, raw `SetWindowPos`, and a self-
  correcting measure-and-retry loop. The same requested size (e.g.
  120x120) produced wildly different actual results across otherwise-
  identical runs (80x80, 133x133, 176x124, 180x180) — width and height
  sometimes distorted by DIFFERENT amounts, which rules out DPI scaling
  as a single, fixable root cause. Concluded this is a fundamental
  reliability gap in hosting WebView2 inside a WinForms window via
  pythonnet for anything beyond a normal rectangular window — pywebview
  is a thin, lightly-tested wrapper for that specific (much more common)
  case, not for shaped/precisely-sized widgets.

**Rebuilt on Electron** (`desktop/` — `main.js` main process, `preload.js`
contextBridge IPC, `package.json`). Electron's `BrowserWindow` transparency
and exact sizing are both just reliable out of the box — this is one of
Electron's best-trodden use cases (real chat-bubble/overlay apps are
commonly built this way), unlike pywebview where it's a rarely-exercised
corner of a much smaller project. Verified live 2026-09-24: bubble
requested at 72x72 landed at EXACTLY 72x72; a real simulated mouse click
on the bubble correctly expanded it to EXACTLY 400x640 at the EXACT
computed bottom-right-anchored position — no drift, first try, repeatable.
`desktop_app.py` was deleted; `pywebview`/`pythonnet`/`pillow` removed from
`backend/requirements.txt` and uninstalled from the venv (pillow was only
ever a one-time icon-generation tool, not a real runtime dependency).

**No admin rights on this machine** (same constraint as the OCR gotcha
below) is why Node.js is a portable, unzipped install at `.tools/node/`
(from nodejs.org's win-x64 zip, not the installer) rather than a system
install — gitignored, not committed. `desktop/node_modules` is also
gitignored (run `.tools/node/npm.cmd install` inside `desktop/` on a
fresh checkout, with `.tools/node` on PATH, or invoke `npm.cmd` by full
path).

Architecture: `main.js` starts the backend itself via `child_process.spawn`
if `/health` isn't already responding (same non-destructive check as the
old pywebview version — never touches an already-running server), creates
a frameless/transparent/always-on-top `BrowserWindow` sized to
`BUBBLE_SIZE` (72px) positioned via Electron's own `screen.workArea`
(automatically excludes the taskbar, no manual `SPI_GETWORKAREA` call
needed unlike the old Win32 approach), and loads
`frontend/agmc-assistant-popup.html` with `#app=1` in the URL hash — same
`.app-mode` CSS/JS detection as before, now talking to
`window.electronAPI.expand()/collapse()` (exposed via `preload.js`'s
`contextBridge`, `contextIsolation: true`, no direct Node access from the
page) instead of `window.pywebview.api`. Expand/collapse are real IPC
handlers in `main.js` (`ipcMain.handle('widget:expand'/'widget:collapse')`)
that call `win.setBounds(...)` — no shape-clipping hack needed, CSS
`border-radius`/transparent background just work.

**Quitting**: frameless windows have no native close button — the ✕ in
the chat header only collapses back to the bubble (matches the CEO's
explicit choice: stay resident, don't fully quit on close). Real quit is
a system-tray icon (`Tray`/`Menu` in `main.js`) with an "Open"/"Quit"
context menu — a cleaner solution than anything pywebview offered, since
Electron's `Tray` API is first-class or supported.

The Desktop shortcut ("AGMC Assist.lnk", custom icon at `icon.ico`,
generated once with Pillow — kept as a static file, Pillow itself is
gone) now points at
`desktop/node_modules/electron/dist/electron.exe` with argument `"."` and
working directory `desktop/`, instead of `pythonw.exe`.

**Relaunching from the shortcut without quitting the previous instance via
the tray stacks up duplicate processes** — found 2026-09-24 while
debugging the first-launch shape bug below: 9 separate stray `electron .`
process trees were running at once (each its own window/tray/backend-check
tree), from earlier test launches that were only ever closed via the ✕
(which just collapses to the bubble by design, see "Quitting" above — it
never calls `app.quit()`). Not a code bug, just a real gotcha: always quit
via the tray's "Quit" item before relaunching, or use Task Manager to
clean up `electron.exe` processes under
`desktop\node_modules\electron\dist\` if instances pile up.

**Bubble rendered as a squashed ellipse (not a circle) on first cold
launch only** — found 2026-09-24, self-corrected the moment the CEO opened
and closed the panel once. Took six attempts across two wrong layers
before a real screenshot (with a temporary bright debug outline on
`.mascot`) finally showed what was actually happening — a native
scrollbar, not a shape-rendering glitch. Full history, so none of these
get relitigated:
- **Attempts 1-3 (all confirmed live as NOT fixing it, all in
  `desktop/main.js`, all about the native window's bounds):** (1) call
  `win.setBounds(bubbleBounds())` inside `win.once("ready-to-show", ...)`,
  no `show:false` — `GetWindowRect` read back exactly 72x72 right after,
  but the CEO still saw the wrong shape, because `ready-to-show` fires
  AFTER the first (wrong) frame is already rendered/auto-shown; the resize
  landed a moment too late even though the final rect measured correctly.
  **Lesson: `GetWindowRect` after the fact only proves the final size, not
  whether a wrong frame was ever actually shown — not sufficient proof for
  this class of "wrong then self-corrects" bug.** (2) added `show:false` +
  moved `win.show()` after `setBounds()` in the same handler — still wrong,
  ruling out "shown too early." (3) replicated the real bubble->panel->
  bubble transition once before `show()`, theorizing identical setBounds()
  values are a no-op — still wrong. All three shared a wrong assumption
  that this was about the native window's pixel bounds at all; `main.js`
  was eventually simplified back to a plain `setBounds(bubbleBounds())` +
  `show()` in `ready-to-show`, which is fine in principle but was never
  what fixed anything.
- **Attempts 4-5 (also confirmed NOT fixing it, moved to the content
  layer but still guessing):** (4) theorized `closeBtn`'s incidental
  `mascotBtn.style.display` toggle (a side effect of the separate giant-
  mascot-on-scroll fix) was somehow forcing a correct relayout, and
  replicated it once at startup — still wrong. (5) switched `.app-mode
  .mascot` from `width/height:100%` to a literal `72px`/`72px`, theorizing
  percentage sizing wasn't resolving correctly on the very first layout
  pass — also did not fix it (never got a clean before/after confirmation
  either, since automated screenshot analysis of this window turned out to
  be unreliable — see the note below on why).
- **Why self-verification kept failing:** repeated attempts to confirm
  fixes via `PrintWindow`-based screenshot capture and pixel/color-distance
  analysis were fundamentally unreliable for this specific transparent
  window — captured "background" pixels varied by corner (pure black in
  two, near-white in another) because the window's real alpha transparency
  was compositing with whatever was ACTUALLY behind it on the real desktop
  (a photo wallpaper with its own light/dark/brown tones), not a clean
  isolated capture of the window's own content. A color-distance match
  against the AGMC accent brown even matched contaminating wallpaper
  pixels. **Don't trust `PrintWindow`/pixel-matching self-capture for this
  window again without first proving the capture isn't compositing with
  real desktop content** — a real screenshot from the CEO's own eyes,
  which was the actual source of every correct diagnosis in this whole
  saga, is what should have been trusted from the start.
- **Actual root cause and fix (attempt 6):** a temporary bright green
  `outline` was added to `.app-mode .mascot` purely as a debugging aid (an
  outline, unlike a fill-color match, can't be confused with background
  bleed-through) and the CEO sent a real screenshot showing actual Windows
  **scrollbar arrows and a scrollbar thumb** squeezed into the right/
  bottom edges of the 72x72 window, cropping the circle down to only its
  top-left arc. That reframed the whole bug: something was causing real
  page OVERFLOW, and a native scrollbar eating into a 72x72 window is what
  turns a small positioning bug into a visibly broken shape. Root cause:
  `.badge` (the red notification dot, a DOM child of `.mascot`) is
  `position:absolute; top:-2px; right:-2px` — which only means "2px
  outside THIS BUTTON's corner" if `.mascot` is itself a positioned
  element. `.app-mode .mascot` was set to `position:static`, which breaks
  that containing-block relationship, so `.badge` instead positioned
  against the viewport/window itself, landing 2px outside the window's
  actual top-right corner — real overflow, hence the scrollbar. This also
  explains the "one click permanently fixes it" pattern precisely:
  `mascotBtn`'s click handler sets `badge.style.display='none'` (to hide
  the notification dot) and nothing ever resets it — the very first click
  removes the overflowing element from layout for good, so the bug could
  never recur after that, in every single test, which is exactly the
  pattern reported from the start. Fixed by changing `.app-mode .mascot`
  to `position:relative` (same layout behavior as `static` here — no
  top/left is set on it — but correctly re-establishes it as `.badge`'s
  containing block), plus `.app-mode, .app-mode body { overflow:hidden;
  }` added as defense-in-depth so a native scrollbar can never visually
  appear in this borderless widget regardless of what causes a future
  overflow (`.thread`'s own internal `overflow-y:auto` is a separate,
  nested scroll context and is unaffected). **If this bug's shape ever
  looks wrong again, check for an absolutely-positioned child of an
  element that got `position:static`d for app-mode layout purposes,
  first** — that's the actual bug class, not window bounds or DOM
  reflow timing.

**Clicking the Desktop icon while an instance was already running launched
a second full app** (own window, tray icon, taskbar entry) — found
2026-09-24. Both windows still worked and even shared the same
conversation, since `SESSION_ID` is a hardcoded constant in
`agmc-assistant-popup.html`, not per-window — but two taskbar entries for
"one chat" is confusing. Fixed with Electron's standard
`app.requestSingleInstanceLock()` in `main.js`: a second launch attempt
now just refocuses the existing window and exits immediately rather than
creating its own app instance. Don't remove this thinking it's redundant
with the tray-resident "don't quit on window close" behavior — that
handles closing, this handles re-launching.

**Follow-up bug in that same fix, found minutes later: the Desktop icon
stopped opening anything at all.** Root cause: the window had been
destroyed (exact trigger unconfirmed — possibly Alt+F4, since this
frameless window has no other native close path) while the app PROCESS
stayed alive holding the single-instance lock, with `win` now `null`.
Every launch attempt correctly detected the lock and handed off to that
zombie process via `second-instance` — which then did nothing, because
the handler was a bare `if (win) { win.show(); win.focus(); }`. Fixed
with a `showWindow()` helper that calls `createWindow()` when `win` is
null instead of silently no-op'ing, used everywhere a window needs to be
(re)shown — `second-instance`, the tray's "Open" item, and the tray
icon's own click. **Don't go back to a bare `if (win) win.show()`
anywhere** — that's exactly what let a destroyed-but-not-quit window
brick every future launch until the process was killed manually.

**Excel upload crashed with `WinError 32` ("used by another process")
even after closing the file in Excel** — found 2026-09-24, real root
cause was in OUR OWN process, not Excel. `file_tool._read_excel` opened
the workbook with `openpyxl.load_workbook(..., read_only=True)` but never
called `wb.close()` — `read_only` mode keeps a lazy file handle open on
the underlying zip archive, so Windows kept the temp file locked
indefinitely. `main.py`'s `/upload` handler always `os.unlink()`s the temp
file right after reading it, and that unlink is what threw. Fixed with a
`try/finally: wb.close()`. If a future edit touches `_read_excel`, keep
the try/finally — it's not optional cleanup, the crash is 100%
reproducible without it.

**The notification badge and the send button both needed real behavior,
not just decoration** — found 2026-09-24 from direct CEO feedback:
- The red badge used to show on every fresh page load regardless of
  whether anything had actually happened — pure decoration, not a real
  notification. Now hidden by default (`display:none` in CSS) and only
  shown by `send()` when a reply finishes streaming while the panel is
  closed (the one real "you might have missed something" case this app
  can produce — the CEO asked something, then closed the chat before the
  answer arrived). Cleared again the instant the panel reopens (already
  handled by `mascotBtn`'s existing `badge.style.display='none'`).
- The shortcut chips and the "+" attach menu were still clickable while a
  reply was streaming, silently queuing another request behind
  `orchestrator._lock_for`'s per-session lock with zero indication
  anything was happening. `setGenerating()` now disables `input`, every
  `.chip`, `plusBtn`, and `sendBtn` while a request is in flight — this
  part stands on its own and is unrelated to the stop button saga below.
  (A stop button was also added the same day, alongside this, specifically
  to let the CEO cancel a response he didn't want to wait for — see the
  dedicated entry further down for its full arc and why it was ultimately
  removed entirely.)

## Packaging a standalone distributable (added 2026-09-25)

Built once, under real time pressure ("need to send before eod" so the
CEO's boss could test it on his own PC) — a single portable `.exe` that
needs NO Python or Node install on the target machine at all. This is
NOT the normal dev workflow (still `uvicorn main:app --reload` + `npm
start`, see above) — it's a separate, occasional build step for handing
this app to someone else's machine.

**The Anthropic API key is embedded in this build, deliberately, on the
CEO's own explicit instruction** ("use my key, since it's the one he gave
me") — it's the boss's own key already, not a secret being leaked to a
stranger. If this is ever rebuilt for a DIFFERENT recipient, re-ask
whether to embed a key or have them supply their own — don't assume this
answer carries over.

**How to rebuild, in order:**
1. Freeze the Python backend (from `backend/`, with the venv activated):
   ```
   pyinstaller --onedir --name agmc-backend --noconfirm --hidden-import win32timezone run_frozen.py
   ```
   Then copy `backend/.env` into `backend/dist/agmc-backend/.env` (the
   frozen exe loads it from next to itself, not from `load_dotenv()`'s
   normal cwd-relative search — see `run_frozen.py`'s own docstring).
2. Build the Electron package (from `desktop/`, with `.tools/node` on
   PATH):
   ```
   $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
   npm run dist
   ```
   Output lands at `desktop/release/AGMC Assist 1.0.0.exe` — a single
   portable file, no installer wizard, just double-click and run.

**Why `run_frozen.py` exists as a separate entry point, instead of just
freezing `main.py`**: PyInstaller needs a plain Python API call to trace
imports statically — `uvicorn.run(app, ...)` — not uvicorn's own CLI-style
string resolution (`"main:app"`), which PyInstaller's import scanner can't
follow reliably. Don't try to freeze `main.py`/the `uvicorn` CLI directly.

**Two real build failures hit and fixed, not just anticipated:**
- `electron-builder` failed outright trying to download and extract a
  `winCodeSign` package (macOS code-signing binaries, `.dylib` files —
  completely irrelevant to a Windows-only build) — extraction needs
  symbolic-link creation privilege, which this machine's user account
  doesn't have (same **no-admin-rights** constraint noted elsewhere in
  this doc, e.g. the OCR gotcha). `CSC_IDENTITY_AUTO_DISCOVERY=false`
  alone did NOT avoid the download. The actual fix: `"signAndEditExecutable":
  false` under the `win` key in `desktop/package.json`'s `build` config —
  this skips the post-build icon-embedding/exe-editing step that pulls in
  `winCodeSign` in the first place. Known tradeoff: the built `.exe`'s own
  file icon (as seen in Windows Explorer before running it) may show a
  generic icon rather than the AGMC one — cosmetic only; the running
  app's window/taskbar/tray icons (set at runtime via `ICON_PATH`, not
  baked in at build time) are unaffected.
- `desktop/main.js` needed real branching for packaged vs. dev mode, not
  just a path tweak: `FRONTEND_HTML`, `ICON_PATH`, and how the backend
  gets spawned (`BACKEND_EXE` vs `VENV_PYTHON -m uvicorn`) all differ
  based on `app.isPackaged`, because a packaged Electron app's `__dirname`
  resolves inside `app.asar`, not next to sibling folders like `frontend/`
  the way it does in dev — those have to be pulled in explicitly via
  `extraResources` in `package.json` and read back from
  `process.resourcesPath` at runtime. Confirmed live (not just assumed
  correct): ran the actual packaged `.exe`, confirmed via the real process
  tree that `agmc-backend.exe` started as a child process, and confirmed
  `/health` responds — the first health check attempt (8s wait) came back
  too early and looked like a failure; the packaged app's real cold-start
  (self-extraction + PyInstaller onedir startup) needs more like 15s+, not
  the ~4s the dev `--reload` workflow is used to.

**Residual risk, explicitly NOT fully verified — be upfront about this if
it comes up**: `pywin32`'s COM automation (everything in
`outlook_com_tool.py`) was confirmed working from inside the FROZEN
executable — but only on THIS dev machine, which already has `pywin32`
pip-installed and its one-time COM registration already done at the
Windows-registry level (a system-wide thing, not per-venv). PyInstaller's
bundled runtime hooks (`pyi_rth_pywintypes.py`, `pyi_rth_pythoncom.py`,
visible in the build log) are specifically designed to make this work on
a machine that's NEVER had `pywin32` installed at all — which is exactly
the boss's PC — but this could only be verified with real confidence on
an actually-clean machine, which wasn't available here. If Outlook
reading/sending doesn't work on his first real test, this exact
mechanism (COM registration inside the frozen exe) is the first place to
look, not the FastAPI/orchestrator code above it.

`backend/build/`, `backend/dist/`, and `desktop/release/` are all
gitignored (large, regeneratable build output, not source) — don't
commit them; rebuild from the commands above instead. `run_frozen.py`
IS committed (it's source, small, and needed to reproduce the build).

**Renamed "AGMC Assist" → "AGMC X" for this specific build**, direct CEO
request right before sending it to his boss ("tiny tiny change"), along
with removing the AGMC/BMW logo from the header's `.mark` box (the
`<div class="mark">` element was deleted outright from
`agmc-assistant-popup.html`, not just hidden). Renamed everywhere
user-visible: the frontend `<title>`/header text/mascot `aria-label`,
`desktop/main.js`'s tray tooltip, `desktop/package.json`'s
`productName` (which is also what names the built `.exe`,
`AGMC X 1.0.0.exe`), and `orchestrator.SYSTEM_PROMPT`'s own
self-identification ("You are AGMC X..." — kept in sync with the visible
product name rather than leaving the bot introducing itself with the old
name). `FastAPI(title=...)` in `main.py` too, for consistency, though
that's only visible in the auto-generated `/docs` page, not real user UI.
**The mascot bubble's own icon still shows the BMW+AGMC logo** — only the
header bar's logo was asked to be removed, the floating bubble is a
different element and wasn't part of this request; don't remove that one
without being asked separately. If "AGMC Assist" is ever the name again,
grep for "AGMC X" across these same files rather than assuming this
rename was the last word — check the CURRENT product-naming intent first.

## Real zero-manual-setup packaging: NSIS installer, silent Ollama install, frozen SearXNG (added 2026-09-29)

Direct follow-up to the CEO's boss hitting real friction testing the
portable `.exe` from the packaging section above: it wouldn't even launch
until he manually unblocked it via file Properties, then a stale
still-running process from an earlier build silently ate every relaunch
attempt (see the "won't open on repeated clicks" investigation — same
session, same day), and separately he'd have needed to run
`setup_local_model.ps1` by hand for the local-model toggle to work at all.
The whole point of this round was making the **`.exe` alone** — no
Properties dance beyond the one unavoidable SmartScreen click, no separate
script, no manual anything — actually true. **This section supersedes the
"portable, no installer wizard" description and the `AGMC X 1.0.0.exe`
filename earlier in this doc's packaging section above** — read this one
first if the two disagree.

**Switched `desktop/package.json`'s `win.target` from `"portable"` to
`"nsis"`.** The portable target self-extracts to a fresh random temp folder
on every single launch — which is exactly what let an old, still-resident
tray instance's single-instance lock silently swallow every attempt to run
a newer build (different temp path each time, same app identity/lock file
underneath). NSIS installs once to a **fixed** per-user location
(`%LocalAppData%\Programs\agmc-assist-desktop\`, no admin needed — same
per-user pattern this whole project already uses elsewhere), creates a
real Desktop + Start Menu shortcut, and — configured `oneClick: true` +
`runAfterFinish: true` — installs and launches with a single click, no
wizard screens. **Verified live end-to-end on this dev machine**: ran the
built `AGMC X Setup 1.0.0.exe`, confirmed it installs with no admin prompt,
creates both shortcuts, auto-launches, and the backend comes up and
`/health` responds within its normal cold-start window.

**Real bug hit rebuilding this: `signAndEditExecutable: true` (needed to
embed the real icon) reintroduced the exact winCodeSign download/symlink
failure already documented in the original packaging section** — tried
re-enabling it on the theory that a previously-cached, already-extracted
copy of winCodeSign might let it succeed this time; instead electron-builder
kept fetching FRESH copies under new cache-hash folders and failing
identically each time (`Cannot create symbolic link: A required privilege
is not held by the client`, on the `darwin/*.dylib` entries specifically —
completely irrelevant to a Windows build, same as before). **Fixed
differently this time, without giving up on the real icon**: an earlier,
partially-successful extraction attempt had already left a genuine, working
`rcedit-x64.exe` sitting in electron-builder's own cache
(`%LocalAppData%\electron-builder\Cache\winCodeSign\<hash>\rcedit-x64.exe`)
before it got to the darwin symlinks and failed — confirmed this was a
real, non-broken 1.3MB binary, not a stub. `desktop/afterPack.js` (new,
wired in via `package.json`'s `"afterPack"` hook) searches that cache
directory for any such already-extracted `rcedit-x64.exe` and runs it
directly against the packed exe (`--set-icon`), completely bypassing
electron-builder's own signing/icon pipeline (and the darwin symlink step
that breaks it) while still getting the exact same real result. `win.
signAndEditExecutable` stays `false`. **Verified this actually worked, not
just "didn't error"**: the naive verification method (.NET's
`System.Drawing.Icon` + `ToBitmap()`) rendered BOTH the installed exe's
icon AND the source `icon.ico` itself as visibly wrong/garbled — a real
scare that turned out to be a bug in that verification method for this
specific multi-resolution `.ico`, not the icon itself; re-checked properly
with Pillow (`Image.open(...).load()`) and confirmed both match: the real
green/gold "A" mark, correctly embedded. **If a future rebuild ever needs
this cache repopulated from scratch** (e.g. a clean machine with no prior
attempt), the fallback is `afterPack.js`'s own warning message — it logs a
clear "no cached rcedit found, run with signAndEditExecutable temporarily
true once" note rather than failing the whole build silently.

**Ollama's own install is now fully automated too — no more
`setup_local_model.ps1` for this part.** `desktop/main.js` gained
`ensureLocalModel()`, called fire-and-forget (never awaited, never blocks
the widget opening) right after the window/tray are created. On every
launch: checks whether `%LocalAppData%\Programs\Ollama\ollama.exe` exists;
if not, downloads the real installer (`https://ollama.com/download/
OllamaSetup.exe`, confirmed live to be ~1.57GB — it bundles CUDA/ROCm
backends, hence the size) via a small hand-rolled `https` redirect-following
downloader, then runs it with real silent-install flags. **Confirmed via
direct experimentation that this is genuinely Inno Setup based** (scanned
the binary for the "Inno Setup" signature string) and that `/VERYSILENT
/SUPPRESSMSGBOXES /NORESTART` produces a truly unattended install — zero
window, confirmed via a real install log
(`install.log` via Inno's own `/LOG=` flag) showing "Installation process
succeeded." **A real false alarm during testing, worth recording so it's
never re-chased**: the very first attempts at this all reported exit code 1
("setup failed to initialize") and no log file at all — looked exactly like
the silent flags themselves were broken. Root cause was much simpler and
had nothing to do with Inno Setup: the installer download (via a backgrounded
`curl`) was still mid-download every time it was tested — the file grew from
~154MB to its real ~1.57GB size across several minutes, and every "failed"
test ran against a truncated, corrupt exe. Confirmed by waiting for the file
size to actually stabilize before retesting: same exact flags, against the
complete file, worked perfectly first try. **If silent-install testing ever
looks broken again, check the file size against a fresh download first**
before suspecting the flags or the installer itself.

Separately: if Ollama is already installed but its own tray app/server
isn't currently running (a very normal case — e.g. this is the first launch
after that silent install, or the user just hasn't opened Ollama today),
`ensureOllamaServerRunning()` starts it. **Real bug found here too**:
spawning `ollama app.exe` directly (inherited environment) left its process
running but the API port never came up — confirmed by checking
`Get-Process` (alive) against `http://localhost:11434/api/tags` (never
responding) at the same time. Root cause, found by reading the real Inno
Setup install log's own post-install "Run" entry verbatim: Ollama's
installer launches it as `cmd /C set PATH=<install dir>;%PATH% & "ollama
app.exe"` — its own server-spawning logic depends on finding `ollama.exe`
via `PATH`, not via its own directory. Fixed by replicating that exact
env-prepending when spawning it from Electron
(`env: { ...process.env, PATH: `${OLLAMA_DIR};${process.env.PATH}` }`).
**Don't spawn `ollama app.exe` with a plain inherited environment again** —
it's a silent failure (process alive, port never answers), not an obvious
crash, and easy to miss without explicitly checking the API afterward the
way this was caught. Once the server responds, `modelIsPulled()` checks
`qwen2.5:3b` against `/api/tags` and `pullModel()` runs `ollama pull` if
it's missing — same model default as `config.OLLAMA_MODEL`, kept in sync
by hand since this side can't read the Python file's value directly.
Everything here logs to `local-model-setup.log` in the app's userData
folder (never shown in any UI) so a silent failure on someone else's
machine can still be diagnosed after the fact.

**SearXNG (real web search under local-model mode) is handled differently
from Ollama — frozen once here into a standalone executable, rather than
automating its from-source setup on the target machine.** Setting it up
from source (git sparse-checkout, a Python venv, the `valkeydb.py` Windows
patch, `settings.yml` edits — all documented in the Ollama section
elsewhere in this file) is fragile enough that automating the WHOLE chain
unattended on someone else's PC felt like multiplying the failure surface,
not reducing it. Instead: `.tools/searxng/run_frozen.py` (new, two lines —
`import searx.webapp as webapp; webapp.run()`, mirroring the same
"PyInstaller needs a plain function call, not a CLI string" reasoning
`backend/run_frozen.py` was already built around) gets frozen with
`pyinstaller --onedir --collect-all searx` from inside the already-set-up
`.tools/searxng/venv` — `--collect-all` was necessary and sufficient to
catch both SearXNG's ~200 dynamically-loaded engine plugin modules (loaded
via `importlib`, invisible to PyInstaller's normal static import analysis)
and its bundled templates/static assets in one shot. **Verified live, not
assumed**: the frozen `searxng.exe` (a real ~96MB onedir build) starts
cleanly (same two already-documented, harmless engine-load failures as the
non-frozen version — `ahmia` and `bilibili`/`torch` needing `tzdata`, see
the existing SearXNG gotcha elsewhere in this file) and a real
`/search?q=BMW+Dubai&format=json` call returns genuine, live results sourced
through Brave's engine — confirmed twice: once running the frozen exe
directly, and again through the full packaged Electron app after bundling
it via `extraResources` (`{ "from": "../.tools/searxng/dist/searxng", "to":
"searxng-dist" }` in `package.json`) and adding `ensureSearxng()` to
`main.js` (same fire-and-forget, never-blocks-the-widget philosophy as
`ensureLocalModel()` — checks if something's already answering at
`127.0.0.1:8888/`, and if not, starts the bundled `searxng.exe` in packaged
mode or the existing dev venv in dev mode). If it's not bundled/set up at
all on a given machine, `search_web` already falls back to its existing
honest mock results — this changes nothing about that fallback, it just
makes the real path actually reachable without manual setup most of the
time. **If SearXNG is ever upgraded/re-cloned from its real upstream, this
frozen build needs re-running too** — it's a snapshot of whatever's in
`.tools/searxng/` at freeze time, same caveat this file's SearXNG section
already gives for the `valkeydb.py` patch itself.

**Full fresh-machine test done 2026-09-29, on this dev machine**: uninstalled
Ollama (its own `unins000.exe /VERYSILENT`), moved `~/.ollama` aside to
`~/.ollama.bak-2026-09-29` (NOT deleted — holds the old `qwen2.5:7b` +
`qwen2.5:3b`), then ran the real installer. Timeline from the app's own
setup log: installer download ~2.5 min, silent install ~75s, server up
~25s later, model pull ~1.5 min — **about 6 minutes total from first
launch to Local mode working**, with no manual step. SearXNG was up in ~7s.
**The one real gap it exposed**: asking in Local mode DURING that window
returned a raw `model 'qwen2.5:3b' not found (status code: 404)` to the
chat — almost certainly what the CEO's boss hit on his own machine (Ollama
already installed there, model not yet pulled). Fixed in
`orchestrator._run_ollama`/`_run_stream_ollama`: `_local_model_not_ready()`
catches exactly `ollama.ResponseError` with `status_code == 404` (model not
pulled yet) or a plain `ConnectionError` (server not up yet — confirmed
against the installed ollama client's own source, it raises the builtin
`ConnectionError`, not an httpx type) and returns a friendly
"still setting up, try again shortly or switch to Claude" reply instead.
Any OTHER Ollama error still raises normally — don't widen this to a bare
`except Exception`, it would hide real bugs behind a misleading
"setting up" message. Verified on dev code AND the frozen backend exe.

## Inbox summaries under the local model: code does the structure, the model only writes blurbs (added 2026-09-30)

**CEO report: under the local model, "summarize my inbox" "just lists the
last emails" — he wants the content summarized, who it's from, and "if it's
from the same person, say that."** Three real causes, fixed in layers, each
verified against the CEO's real inbox (on a throwaway port 8001, never the
CEO's own 8000 session):

1. **The tool only ever gave the model 200 characters of each body** —
   usually just the greeting — so a list of subjects was all it COULD
   produce. `outlook_com_tool.read_inbox` now returns `body` (not
   `preview`): `_clean_body` cuts quoted reply chains, "Original Message"
   / "On ... wrote:" / "From:+Sent:" headers, legal footers, `>` lines,
   replaces URLs with `[link]`, and truncates at a sentence boundary to
   `MAX_BODY_CHARS = 600` (sized against the 8192-token local context; the
   CEO's real bodies run 830-3,500 chars, median ~930). Also adds
   `from_name` (the display name — what a person actually recognizes).
   Unit-tested on synthetic emails only, not on real ones.
2. **`orchestrator._group_emails_for_model`** reshapes the flat list into
   `{"senders": [{from_name, from, email_count, messages: [...]}],
   "reply_ids": {n: entry_id}}` — grouped by DISPLAY NAME (one real person
   showed up under two addresses), identical copies collapsed
   (`identical_copies`; four identical Facebook codes become one entry), and
   the 140-hex-char Outlook EntryIDs moved out of each email into one
   `reply_ids` map (the model kept printing them into summaries).
   `_record_known_emails` reads that shape, so `/reply-email`'s
   anti-fabrication check (`get_known_email`) still only accepts ids a real
   inbox read produced — unit-tested including that a fabricated id is
   rejected and a real one accepted. Applies to both providers.
3. **The big one — a prompt alone can't make qwen2.5:3b do this.** With the
   grouped data and an explicit format instruction it still invented a
   duplicate sender entry, ordered groups wrong, printed "Subject:/Body:"
   labels and took **4.5 minutes** (CPU-only, ~1,500 tokens of output).
   So for a plain summarize/check request (`_is_inbox_summary_request`:
   inbox/email word + summarize/check/what's-in/any-new/unread/etc., AND no
   action word such as reply/send/forward/schedule/book/then, AND <=120
   chars — verified on 25 phrasings) `_ollama_events` bypasses the model's
   tool loop: code calls `read_emails` + `open_outlook`, writes the
   structure itself (who, count, unread, newest-first order), and the model
   is called ONCE PER SENDER for a two-sentence blurb of what they say
   (`_summarize_sender_group`, ~35 words, worked examples as prior chat
   turns). Same event shapes as `_ollama_turns` (status / tool_round /
   text), so history persistence and the reply cleaning apply unchanged;
   the tool round is persisted so a follow-up can still find `reply_ids`.
   Falls back to the normal loop for mock data or an empty inbox. Result:
   ~21-26s (was 270s), correct counts and order every run.
   - **Blurb guards, both found live**: `_blurb_is_grounded` — an unrelated
     sender's blurb came back as "Asks for Facebook confirmation code
     63548", i.e. the WORKED EXAMPLE's own facts. Any number in a blurb, and
     any example-only term, must appear in the source emails, else it retries
     without examples, else falls back to a plain extract. `_clean_blurb`
     drops "no specific deadline is mentioned"-style filler (the model was
     echoing my own checklist back) and caps at two sentences. Don't remove
     the examples' own vocabulary from `_EXAMPLE_ONLY_TERMS` if the examples
     change.
   - Known limit: blurb QUALITY is still a 3B model's — e.g. it wrote
     "Asks for verification code" for an email that SENDS one. Structure is
     now reliable; nuance is not.
   - New status label `summarize_emails` in `TOOL_STATUS_LABELS`.

### Replying to an email under the local model — FIXED the same way (2026-09-30)

**Root problem, measured not assumed**: compared the original flat data
shape, grouped-with-inline-ids, and the current shape, twice each, on the
identical request ("reply to the Administration memo and say thank you,
noted"): none produced an `agmc-reply-email` block on the first ask. In a
summarize -> reply -> push sequence it emitted an `agmc-send-email` to a NEW
message (the exact bug reported 2026-09-29) and then invented a flight
booking, which `_validate_booking_blocks` stripped. Not a data-shape problem
— qwen2.5:3b can't reliably pick the right email AND emit a well-formed block
with a 140-hex id. So `_reply_events` (wired into `_ollama_events`, same
event shapes and same fall-through as the inbox summary) makes CODE do the
bookkeeping and asks the model only for a sentence or two:

- **Intent**: `_is_reply_request` — reply/respond/write-back verb, <=300
  chars, and no compound word (summarize/forward/schedule/...), which keeps
  those requests in the normal loop. Nothing said about WHAT to reply
  ("reply to Fatima") -> asks what to say instead of inventing it.
- **Which email**: `_pick_reply_target`, two stages. (1) WHO by display name;
  two different people tying on name ("Sara" with a Sara Khan and a Sara Ali)
  is ASKED, never guessed — a first version let an address bonus
  (`sara@x.com`) silently break that tie; caught by the unit test, fixed.
  (2) WHICH email within that sender by subject/address words; a tie inside
  one sender takes their newest and the chat text says so ("their most
  recent of 7 matching emails"). "last/latest" -> newest overall; no words at
  all ("reply to that email") -> asks with candidates. The question is held
  in `_pending_reply` for exactly one following message (an inbox-summary
  request or a non-answer drops it). Reads 25 emails to match against, and
  falls through to the model loop if the data is mock / has no real ids.
- **The text**: the greeting ("Hi <first name>,", or "Hello," for
  organisations — `_ORG_NAME_WORDS`) and "Best regards," are written by code;
  the model writes only one or two sentences (`_compose_reply_body`). Asked
  for a whole letter it dropped the greeting, printed the closing twice and
  omitted half the instruction. Exact words in quotes are used verbatim with
  no model call at all.
- **Guards on model-written text, each added after a real failure**:
  `_blurb_is_grounded` (numbers / example vocabulary must come from the
  instruction or email), `_COMMITMENT_RE` (a promise or first-person claim
  the CEO didn't make: "will review the promotion details", "I've entered
  the code as instructed" — retried with a stricter prompt, and if it keeps
  failing the CEO is asked instead of shown a draft), and for
  courtesy-only instructions (`_is_courtesy_only`: "say thanks", "thank you,
  noted") only the FIRST sentence is kept, since the second is where
  invention lives. A first version applied that cut to every short
  instruction and would have dropped "say I will call him tomorrow"; the
  regression suite caught it.
- **Result** (real model, real inbox, never sent): 3-11s per draft (was
  120-210s and no block), right email every time, three repeat runs of the
  previously-invented-claim request came out identical and clean.
  `_validate_reply_blocks` / `/reply-email` still only accept ids a real
  inbox read produced; a fake id is rejected server-side.

**A safety gap found and closed along the way, for ALL providers**: the reply
approval card only ever showed the reply TEXT — never who it was going to or
which email it answered. New `GET /reply-target` (main.py) returns
from_name/from/subject from `_known_emails` (now storing `from_name`), and
the card shows "Replying to: <name> — <subject>" from THAT, not from
anything in the model's block; an unknown id shows "this reply can't be sent"
and disables Send. **When testing the reply UI, never click "Send reply"** —
it sends a real email from the CEO's real Outlook.
- Testing note: the static file server for the browser preview served a
  cached older copy of the page at first (the card had no "Replying to" row
  until a hard reload) — if a frontend edit seems not to take effect, check
  `fn.toString()` in the page before assuming the edit is wrong.
- New status label `compose_reply` in `TOOL_STATUS_LABELS`. **NOT yet in the
  installer** — needs a rebuild (`pyinstaller ...` then `npm run dist`).

## UI pass 1: welcome screen, live progress, inbox cards (added 2026-09-30)

CEO ask: "a better and smarter UI for a better user experience — the colours
are okay, just the overall feel." Colours are untouched (every new rule uses
existing theme variables; dark mode checked). Changes, all in
`agmc-assistant-popup.html` unless noted:

- **Live progress instead of dots** (`createProgress`): the reply bubble
  shows the real steps so far ("Reading your inbox…" ticked, "Summarizing your
  emails…" with a spinner) driven by the same `status` SSE events as before,
  plus an elapsed timer, plus — after 12s and only when the LOCAL model is
  selected — "Running on this PC — the local model can take a minute or two."
  The structure is rebuilt only when a step changes and the timer only updates
  text: rebuilding every second restarts the CSS spinner (first design flaw
  avoided on paper, not found live).
- **Welcome screen + shortcuts that get out of the way**: an empty chat shows
  a time-of-day greeting and the six shortcuts as a 2-column grid; once a
  conversation exists (`.has-chat`, kept in sync by a MutationObserver on the
  thread, so approval cards count too) they shrink to ONE scrollable row —
  they used to permanently take about a third of the panel.
- **"New chat"** is the first chip in that row, visible only once there is a
  conversation. It swaps `SESSION_ID` (now `let`) so the backend starts a
  fresh history too, and is disabled mid-reply like every other chip.
  **Real regression found while building it and fixed**: it first went in the
  header as an icon button, and the measured header width showed the status
  text squeezed from "Invalid key" to "Invalid" (the same crowding as the old
  gear icon, per the header-layout notes in the model-toggle section). The
  header is full — don't add anything to it without re-measuring
  `.head-status` (`scrollWidth > clientWidth`).
- **Inbox summary as cards** (`renderInboxCards`): the summary fast path now
  ALSO emits an `inbox` event (`_inbox_cards_payload`; `chat_stream` forwards
  it as `{"inbox": ...}`, which would otherwise have been mis-sent as a
  "status" and crashed) — one card per person: initials avatar (5 brand
  colours picked by a hash of the name), name, "N emails" / "N unread" pills,
  the blurb, and a "Reply" link that pre-fills "reply to <name> and say " in
  the input. The markdown text is still produced and stored in history (and is
  what an older client shows); when cards render, the page ignores the text
  event. **Cards are built with `textContent` only** — names and blurbs come
  from email content. Only the LOCAL fast path emits cards; Claude's summary
  is still plain markdown.
- Smaller: green send button (was near-black), hover-revealed Copy button on
  assistant replies (`attachCopy`), long answers scroll to the START of the
  reply instead of the end, summary text in `--text` not grey.
- **Verified in the browser pane against the real backend and real inbox**:
  welcome screen, live progress mid-flight (steps, spinner, timer, 12s hint),
  chips collapsing, finished cards, New chat returning to the welcome state,
  dark mode, header measurements, no console errors. **NOT verified**: the
  website-embedded mode (no `#app=1`) — the browser tool's safety check
  failed repeatedly on navigation at the end of the session; its CSS should be
  fine (the panel auto-sizes and the welcome block sits inside the thread) but
  it has not been looked at. **NOT rebuilt into the installer.**
- Testing gotchas: coordinates for `computer` clicks use the SCREENSHOT's own
  frame (630x1020 here), not the larger image shown — a click computed from
  the wrong frame silently hit empty space; and the page must be hard-reloaded
  after a frontend edit (the browser pane cached an old copy once).
- Not done yet from the original list: a "Claude" summary as cards too,
  conversation history across restarts, and the website-embedded check above.

## Model toggle redesign, bring-your-own API key, and a fresh-machine setup script (added 2026-09-29)

**Model toggle redesigned as a two-button segmented control**, replacing
the native `<select>` (direct CEO ask, "make the toggle more
user-friendly") — a binary choice reads and taps more clearly as two
always-visible buttons than a dropdown that has to be opened to see what's
even in it. `agmc-assistant-popup.html`'s `.model-toggle`/`.model-toggle-btn`
CSS + `initModelToggle()` JS. Same backend contract as before (`GET`/`POST
/model`), just a different frontend control — `GET /model` now also
returns `ollama_model` so the local button can show the REAL model name
("qwen2.5:3b") instead of a generic "Local," without drifting out of sync
if `config.OLLAMA_MODEL` ever changes.

**Real layout regression found and fixed while building this**: adding the
new settings gear button (see below) crowded the header enough that
`.head-name` ("AGMC X") started wrapping onto two lines — it had no
`flex-shrink:0`/`white-space:nowrap` of its own, so the flex layout shrank
it instead of something less important. Fixed by pinning `.head-name` and
`.model-toggle` (`flex-shrink: 0`), and letting `.head-status` (the
connection-status text — the least critical piece) be the one that
truncates first if the header is ever this tight again
(`overflow:hidden; text-overflow:ellipsis`). Verified via
`getBoundingClientRect()` before/after, not just eyeballed — the same
"don't trust automated screenshot capture" lesson documented earlier in
this file about this app's own transparent-widget rendering.

**Bring-your-own Anthropic API key, added directly inside the running
app** — direct CEO ask ("my boss might have his own api key he wants to
use, how do we account for that"). A new gear/settings button in the
header opens an approval-card-styled panel (reusing the existing
`.approval` CSS, same visual language as the booking/email/calendar
cards) with a password-type input. Backend: `config.set_anthropic_api_key`
persists the new key by rewriting the `ANTHROPIC_API_KEY=` line in `.env`
in place (a plain relative path — both the frozen entry point,
`run_frozen.py`, and the normal dev `uvicorn` invocation already have
`.env` sitting directly in the process's own working directory) and
`orchestrator.set_anthropic_client_key` swaps the live `anthropic.Anthropic`
client immediately, no restart needed — `run`/`run_stream` just reference
the module-level `client` global, so they pick up the new one on their
very next call. **Verified in full isolation** (a real HTTP round-trip
against an isolated scratch `.env`, never the real one this dev machine's
key lives in) before ever touching anything real — confirmed the scratch
file updates correctly and the actual `backend/.env` is untouched
throughout. The key is accepted and stored, never echoed back — `GET
/settings/anthropic-key` only reports whether one is configured, never
its value.
**REMOVED 2026-09-29, same day, direct CEO ask before sending the build to
his boss** — the gear button, its panel, both `/settings/anthropic-key`
endpoints, `config.set_anthropic_api_key`, and
`orchestrator.set_anthropic_client_key` are all gone. Consequence worth
remembering: the ONLY way to change the Claude key now is editing
`backend/.env`, re-copying it into `backend/dist/agmc-backend/.env`, and
rebuilding the installer (`npm run dist` — no re-freeze needed for a
key-only change). Don't re-add an in-app key field without being asked.

**The header's "Connected" dot now reflects whether the SELECTED model can
actually answer, not just whether the backend process is up** (same day,
direct CEO report: on the boss's PC it said "Connected" while every reply
failed — backend up, local model not downloaded yet, Claude key out of
credit). New `GET /status` (`orchestrator.provider_status`): for Ollama,
checks the server answers AND `config.OLLAMA_MODEL` is in its model list;
for Claude, a real 1-token `messages.create` — the only way to detect
"out of credit" or a revoked key, since those only surface on a real
messages call — cached 60s (`_ANTHROPIC_STATUS_TTL_SECONDS`) so polling
doesn't spend on every tick, bypassed with `?refresh=true` right after a
real chat error. `/health` is unchanged and still means "process is up".
Frontend `checkBackend()`: red "Offline demo" = backend down; amber + a
short reason ("Setting up…", "Starting…", "Invalid key", "Out of
credit", "No internet", "No API key", "Unavailable") = backend up but the
selected model can't answer; green "Connected" = it can. Full explanation
in the hover tooltip (`hint`). **Labels must stay short**: the status slot
is ~77px wide (measured) and truncates with an ellipsis — "Claude:
invalid API key" was cut off in testing, which is why the model name was
dropped from the labels (the Claude/Local toggle beside it already says
which model). Re-checks every 15s while not green, including while
offline, so a finished first-run model download (or a slow cold start)
flips it green with no reload — verified live by swapping a "setting up"
backend for a ready one under an open page.

**`setup_local_model.ps1`, a new script at the project root**, for
demoing the local-model toggle on a machine that's never had any of this
installed — the CEO's boss's own laptop, specifically. Automates exactly
what this session did by hand the first time: installs Ollama (per-user,
no admin needed — downloads the official installer from ollama.com if not
already present, launches it visibly for the user to click through, never
silent/hidden), pulls `qwen2.5:3b`, and best-effort sets up SearXNG
(git clone with the same sparse-checkout exclusion this project's own
setup needed for the Windows-invalid `:` filenames, the same
requirements-first-then-`--no-build-isolation` install order, the same
`valkeydb.py` `pwd`-import patch, and the same `settings.yml` JSON-format +
real-secret-key edits — see the Ollama section below for the full story
behind each of these). SearXNG's own step is explicitly non-fatal if
anything about it fails (no git/Python on the target machine, or
anything else) — the app still works fine without it, `search_web` just
falls back to honest mock results. **The regex-based file patches were
verified for real before shipping this**, not assumed to just work:
fetched fresh, unpatched copies of the actual `valkeydb.py`/`settings.yml`
from SearXNG's real GitHub repo into an isolated scratch folder, ran the
exact same substitution logic the script uses, and confirmed the results
are both syntactically valid (`py_compile` for the Python file, `yaml.
safe_load` for the YAML one) and semantically correct (the right values
in the right places) — not just "the regex didn't error."

## Reply-to-email, and three Ollama fixes (added 2026-09-29, three CEO reports)

**Report 1 — replying to a specific email just sent a brand-new one.**
Root cause: `agmc-send-email` (and `email_tool.send_email`/`outlook_com_tool.
send_mail` underneath it) only ever composes a fresh `MailItem` — there was
no concept of "reply to an existing message" anywhere in this codebase at
all, so the model had no way to actually thread onto anything even if it
wanted to. Fixed with a real, separate feature, not a workaround:
- `outlook_com_tool.read_inbox` now includes a real Outlook `entry_id`
  (EntryID) per email — an internal reference only, same treatment as
  `search_flights`' `ignav_id` (never shown to the CEO, kept in mind
  silently for a later proposal).
- New `outlook_com_tool.reply_to_mail(entry_id, body, reply_all)` uses
  `MailItem.Reply()`/`ReplyAll()` — REAL Outlook object-model methods
  (the same ones the desktop app's own Reply button calls), which return
  a new item already correctly addressed, "RE: "-subjected, and threaded
  (In-Reply-To/References/conversation topic set by Outlook itself). The
  CEO's new text is PREPENDED above the existing quoted original, never
  replacing it.
- A new fenced block, `agmc-reply-email` (`{"entry_id", "body",
  "reply_all"}`), distinct from `agmc-send-email` — same approval-gate
  isolation as every other real action in this app (never a registered
  LLM tool; only main.py's new `/reply-email` endpoint, only reachable by
  a real Confirm click, ever calls `reply_to_mail`).
- Same structural defense as flight booking: `orchestrator._known_emails`/
  `get_known_email`/`_record_known_emails` record every real `entry_id` a
  session's `read_emails` call actually returned, and `/reply-email`
  refuses any id not in that map before ever reaching Outlook — a
  fabricated entry_id is structurally harmless, not just discouraged by
  prompt wording (confirmed live: qwen2.5:3b DID fabricate one on the
  first few attempts, and `orchestrator._validate_reply_blocks`, the
  Ollama-side twin of `_validate_booking_blocks`, caught and replaced it
  every time — same reliability ceiling as booking, same safety net
  holding). `email_tool.reply_email` is a mock-only fallback (no real
  Graph-based reply path exists, since the read side is COM-based here,
  not Graph-based — see that function's own docstring).

**Report 2 — raw `[Called tool search_flights with {...}]` text leaked
into a real CEO-visible reply, immediately followed by fabricated flight
data (wrong currency, an airline never seen in real results).** Real root
cause, not "the model is just being random": `llm_providers.
flatten_history` used to render a PAST tool_use/tool_result exchange as
plain prose ("[Called tool X with Y]" / "[Tool result]: ...") inside
ordinary user/assistant message content — from the model's own
perspective, this looks exactly like something the assistant itself SAID
in a previous turn, and it's a well-known failure mode for small models
to imitate/continue a stylistic pattern they see in their own prior
"output," even though it was never real dialogue. Fixed structurally:
`flatten_history` now detects the exact tool_use-only-assistant-turn +
tool_result-only-user-turn pairing (exactly what `_persist_tool_round`
writes) and reconstructs it as Ollama's OWN native `tool_calls`/`"tool"`
role message shape instead of prose — a completely different message
shape a text completion has no reason to treat as continuable style.
Backed by a second, cheap layer: `orchestrator._strip_leaked_tool_syntax`
regex-strips any stray "[Called tool ...]"/"[Tool result]: ..." fragment
that still makes it into a reply, called alongside `_validate_booking_blocks`/
`_validate_reply_blocks`. **Real, minor side effect found while testing
this fix**: occasionally the model's ENTIRE reply turns out to be nothing
but this fabricated bracket-narration with no real content and no actual
tool call behind it at all — stripping it (correctly) then left a
silently empty reply. Fixed with `orchestrator._fallback_if_empty`: a
short, honest "I wasn't able to put together a clear answer..." message
instead of nothing appearing.

**Report 3 — "the web search isn't working properly, these links are
broken."** The specific broken-looking links reported (`agmc.com/
promotions/x5-special-offer` etc.) were almost certainly a symptom of the
SAME leak bug above, not a separate defect — a model narrating a fake
tool call is exactly the situation where it also has nothing real to
report and free-associates a plausible-sounding but fake URL instead.
Reinforced `search_web`'s own tool description with an explicit rule:
every title/URL/snippet shown MUST be copied exactly from a real returned
result, never a guessed-at plausible URL, even when real results are
thin or an error occurred — say so plainly instead of inventing something.
**Verified live after all three fixes**: a real `search_web` call for
"AGMC current offers" returned genuinely real URLs, correctly copied
verbatim (confirmed — these were real, existing pages, not fabrications).
**A separate, real, NOT-a-bug limitation surfaced by this same test**:
the underlying search results were for "GMC" (the well-known American
truck brand), not "AGMC" (this dealer group) — the model passed the
correct query, but "AGMC" is obscure enough that the search engines
themselves effectively autocorrect/confuse it with the much more common
"GMC". Not something this app's code can fix; if this recurs, a query
enriched with disambiguating context (e.g. "AGMC Dubai BMW dealership
offers") might help, but hasn't been tried.

## Switching between Claude and a local Ollama model (added 2026-09-28)

Direct boss request: be able to switch, at runtime, between the normal
Anthropic/Claude path and a locally-running Ollama model — with
conversation history surviving the switch (explicit CEO requirement,
"otherwise what's the point"). This is NOT a replacement for Claude — it's
a toggle. Claude stays the default; Ollama is an optional, switchable
alternative for when nothing should leave the machine (or the Anthropic
account has an issue, as it did earlier this week — see the billing-error
gotcha elsewhere in this doc).

**New file, `backend/llm_providers.py`** — every Ollama-specific wire-format
detail lives here, deliberately kept OUT of `orchestrator.py`'s existing
Claude logic (the pause_turn handling, the user-turn-merge fix, vision
tool_result blocks, etc. are all hard-won and untouched by this change —
zero regression risk to the Claude path was the explicit goal). It has:
`ollama_client()` (wraps `ollama.Client(host=config.OLLAMA_HOST)`),
`ollama_tools()` (converts `orchestrator.TOOLS`' Claude/MCP-shaped
`input_schema` entries into Ollama's OpenAI-style `{"type": "function",
"function": {...}}` format — and drops `web_search`/`web_fetch` entirely,
since those have no `input_schema` at all and no Ollama equivalent exists),
and `flatten_history()` (see below).

**How history survives the switch, without a full bidirectional
translation layer**: `orchestrator._sessions` is ALWAYS stored in Claude's
own content-block format, regardless of which provider is active — Claude
reads that natively with zero translation, so the Claude path needed no
changes at all. When Ollama is active, `llm_providers.flatten_history()`
turns that same history into a plain Ollama message list fresh on every
call (never stored) — every block type (text, `tool_use`, `tool_result`,
`image`) collapses to a readable string via `_stringify_block()`. Going
the OTHER direction needs no translation at all: `orchestrator.
_run_ollama`/`_run_stream_ollama` append Ollama's own replies back into the
canonical history as plain `{"type": "text"}` blocks — which Claude already
reads as perfectly ordinary conversation the next time it's active. This
asymmetry (flatten one way, plain-text-append the other) is what avoids
needing a full round-trippable shared format while still meeting the real
requirement.

**Two hard, real limits accepted by this design, not engineering gaps to
"fix later" — set expectations with the boss about these directly:**
- **Vision cannot cross the switch.** An uploaded image, or a `tool_result`
  that included one, renders in Ollama's flattened history as a plain
  "[Image attached — not visible to this local model]" note — never real
  image data. The smallest Ollama models (the actual ask: "the smallest
  that can run on my PC") have no vision at all, so there's nothing to
  send even if the plumbing tried.
- **`web_search`/`web_fetch` don't exist for Ollama at all.** They're
  Anthropic *server-side* tools (see the existing gotcha on this above) —
  `ollama_tools()` just drops them from the tool list. Any question that
  needs current/live info (offers, showtimes, social mentions via
  web_search) will get a materially worse answer, or none, while Ollama is
  active. This is the single biggest capability gap of switching to local
  — real, not cosmetic.
- A small local model is also just less reliable at tool-calling and
  instruction-following than Claude generally (see the earlier
  conversation this was scoped in) — including the exact fenced-block
  format (`agmc-send-email`/`agmc-calendar-event`/`agmc-booking`) the
  whole approval-gate system depends on. **Don't trust a real send/booking
  approval card that was drafted while Ollama was active without extra
  scrutiny** until this has actually been exercised live — the JSON-in-a-
  fenced-block format is exactly the kind of precise output small models
  are weakest at.

**Wiring**: `orchestrator.set_provider`/`get_provider` hold one global,
in-memory `_active_provider` (`"anthropic"` or `"ollama"`) — global, not
per-session, since there's one CEO and one active conversation. `run()`/
`run_stream()` both branch to `_run_ollama`/`_run_stream_ollama` at the top
when Ollama is active; otherwise they're 100% unchanged from before this
feature existed. Tool dispatch goes through the exact same `TOOL_DISPATCH`
table either way, and `_record_itineraries` (factored out of `_run_tools`
so both providers' tool loops can call it) means a flight searched under
Ollama is just as bookable via the approval-card flow as one searched
under Claude, and vice versa after a switch — the booking safety in
`main.py`'s `/booking-link` doesn't care which model ran the search.
`main.py` exposes `GET /model` (lets the frontend's header toggle sync to
whatever's actually active on load) and `POST /model` (flips it). The
frontend adds a small `<select id="modelSelect">` in the header bar
(`agmc-assistant-popup.html`), next to the connection-status dot.

**Not yet truly streamed for Ollama**: `_run_stream_ollama` runs the whole
tool-calling loop to completion first (yielding a `status` event per tool
call, same shape the Claude path already uses, for UI parity), then yields
the finished answer as ONE `text` event rather than token-by-token — Ollama
CAN stream tokens, but not cleanly while a tool-calling loop is still being
resolved. Acceptable simplification; revisit only if the lack of streaming
animation for local-model replies is actually reported as a problem.

**A major, previously-undiagnosed root cause found 2026-09-29: the
context window was silently capped at 4096 tokens the entire time, and
this is very likely THE real explanation behind most of the
hallucination/"loses the thread" symptoms documented throughout this
whole section — not just a contributing factor.** Never assumed —
measured directly. Ollama's own runtime default context window (NOT the
model's real architectural max) is 4096 tokens, confirmed live via
`ollama ps`'s CONTEXT column, and this app never overrode it anywhere
(no `options: {"num_ctx": ...}` was ever passed to `ollama_client.chat()`
before this fix). Measured via a real request's `prompt_eval_count`:
`_system_prompt()` alone is ~1997 tokens, and with all 9 tool schemas
attached (`llm_providers.ollama_tools(TOOLS)`, exactly what's sent every
call) it's ~3488 tokens — **85% of the entire 4096-token budget gone
before a single real user message, tool result, or turn of history
exists.** Every prompt-strengthening fix made earlier in this same
session (the Dubai default, the exact-data-reporting instruction, the
resolve_contact/search_web tightening, etc.) made this measurably WORSE,
not better, since each one added more tokens to an already-95%-full
budget. This plausibly explains the hallucinated tool calls, the
inability to recall real search results, the topic-hijacking, and the
random destination substitutions documented throughout this file far
better than "the model is just small" does on its own — a model running
this close to its context ceiling will behave exactly this erratically
regardless of size.

**Fixed**: `config.OLLAMA_NUM_CTX` (new) is now passed as
`options={"num_ctx": ...}` on every `ollama_client.chat()` call (see
`_ollama_turns`) — never omit this again, it is not optional the way it
might look. **The actual value chosen, 8192, is a measured tradeoff, not
a guess** — this machine has no GPU acceleration for Ollama (Intel Arc,
see the qwen2.5:7b gotcha below), and context size cost scales steeply,
non-linearly here, not proportionally: the same real flight-search
exchange took ~90s at the old 4096 default, ~158s at 8192, and was
**still running after 30+ minutes of genuine CPU time** (confirmed via
the actual `llama-server` process, not a hang) at 16384 before being
killed. 8192 more than doubles the ~600-token headroom the old 4096
default left after system+tools, while staying in a tolerable
response-time range — don't casually bump this higher on this hardware
without re-measuring; 16384 specifically is already confirmed
impractical here. **Revisit this number entirely if real GPU acceleration
ever gets set up.**

**Re-tested the full bug battery after this fix, 2026-09-29 — a real,
measurable improvement, confirming the context window was indeed the
primary cause, not just "a" cause**: "book a flight for me" → "next week
to london" correctly defaulted to DXB with no re-asking, returned a
clean readable list with NO raw-JSON narration (a tic that showed up
repeatedly before this fix). Asked to book, it correctly reused the
ALREADY-PERSISTED real search data (confirmed via logs — no new/wrong
search_flights call, unlike every prior test run) instead of
hallucinating a new destination. Pushed once more for the actual
approval card, and it produced a real `agmc-booking` block — verified
end-to-end by POSTing its `ignav_id` to `/booking-link` directly and
getting back a genuine resolved itinerary (correctly filtered to Oman
Air's real IATA code "WY", matching route/date) — **the first time in
this entire investigation the full search → propose → resolve flow has
worked correctly end to end.** Separately, "read my inbox first" gave a
clean, accurate, real inbox summary with zero tool-hijacking.

**Not fully solved — one contradictory-phrasing issue remains, worth
knowing about**: on the FIRST unprompted "book the Oman Air one" (before
being explicitly asked for the approval card), the reply said "I have
booked the flight with Oman Air for you" AND, in the very next sentence,
"this booking is proposal-only and will be confirmed when you click on
the approval card" — internally contradictory, and no actual block was
produced that turn at all (only appeared on the following, more explicit
request). `_validate_booking_blocks` had nothing to strip since no block
existed yet — this is a text-phrasing problem, not a fabricated-data
problem, and sits outside what that validator can catch. If this comes
up again, the fix is stronger phrasing around "propose" vs "book" earlier
in the flow, not another data-fabrication guard.

**Real web search for the local-model path — added 2026-09-28, direct
boss ask ("are you sure qwen2.5:3b can't do web search? look for a
way")**. To be precise about what's actually true: Ollama itself has NO
built-in web access whatsoever — not a hidden setting, an architectural
fact (Claude's web_search/web_fetch are Anthropic running search
server-side; a local model is pure text-in/text-out), and there is no
downloadable model that changes this — "web search" always comes from
tooling wired around a model, never from the model file itself. The real
fix is a new client-side tool, `tools/web_search_tool.py` (`search_web`,
registered in `orchestrator.TOOLS`/`TOOL_DISPATCH` for both providers) —
deliberately NOT scraping (this project already tried and explicitly
ruled that out for hotels/social, see the "Headless-browser scraping...
abandoned" gotcha above, after hitting real bot-defense walls).

**Two backends, tried in order, neither of them scraping**:
1. **SearXNG, self-hosted — the preferred one, genuinely free forever, no
   API key at all.** Added same day after Brave's API turned out to cost
   money past its free tier and the boss ruled that out. SearXNG is a
   real, actively-maintained open-source metasearch engine — it queries
   real engines' (Brave, Google, Bing, DuckDuckGo, etc.) public web
   frontends itself, so this project isn't scraping anything directly.
   Lives at `.tools/searxng/` (gitignored, matching the existing
   `.tools/node` portable-install pattern), started via
   `.tools/searxng/start_searxng.bat`, binds to `127.0.0.1:8888` only.
   **Docker was the standard way to run SearXNG and wasn't viable here**
   (not installed, and given this machine's established no-admin-rights
   constraint — same one blocking Tesseract/Poppler PATH installs — is
   unlikely to be installable at all; WSL2, Docker Desktop's backend,
   also isn't installed and needs admin to enable). Installed instead via
   SearXNG's non-Docker path: `git clone` (with a `.git/info/sparse-checkout`
   exclude for `utils/templates/etc/httpd/` and `.../etc/systemd/` — their
   packaged filenames literally contain a `:` character, e.g.
   `searxng.conf:socket`, which is invalid on Windows/NTFS and made a
   plain clone fail outright with "unable to checkout working tree"), then
   a dedicated venv with `pip install -r requirements.txt` first followed
   by `pip install -e . --no-build-isolation --no-deps` (a plain `pip
   install -e .` failed with `ModuleNotFoundError: No module named
   'msgspec'` — its `setup.py` imports the `searx` package at build time,
   which needs its own dependencies already installed, a chicken-and-egg
   problem PEP 517's isolated build env doesn't solve on its own).
   **Enabled real live results, not scraping our own code around it** —
   confirmed with a real search returning genuine, current BMW pages
   sourced live through SearXNG's own Brave-frontend engine.
   - **Found and fixed a real Windows-incompatibility bug in SearXNG
     itself**: `searx/valkeydb.py` unconditionally `import pwd` at module
     level — `pwd` is POSIX-only, doesn't exist on Windows at all, so the
     server failed to start outright (`ModuleNotFoundError: No module
     named 'pwd'`) the instant anything imported the engine-loading code
     path, regardless of whether Valkey/Redis was even configured. The
     only actual use of `pwd` is one diagnostic log line inside a rare
     Valkey-connection-failure branch — not security- or logic-relevant.
     Patched locally (in this project's own clone under `.tools/searxng/`,
     not upstream) to make the import optional (`try: import pwd; except
     ImportError: pwd = None`) and guard its one call site. **If SearXNG
     is ever re-cloned/updated from upstream, re-check whether this fix
     needs reapplying** — it's a local patch, not merged upstream.
   - **Enabled JSON output explicitly** (`searx/settings.yml`'s
     `search.formats` only has `html` by default — added `json`) and set
     a real random `secret_key` (SearXNG ships an insecure placeholder,
     `"ultrasecretkey"`, in its default config — fine for this
     localhost-only, single-user, no-admin-rights setup, but don't ship
     that default if this is ever exposed beyond 127.0.0.1).
   - **A handful of SearXNG's ~200 bundled search engines fail to load on
     this machine and are automatically disabled** (`ahmia`, `bilibili`,
     `torch` seen so far — e.g. `bilibili` needs the `tzdata` package for
     `ZoneInfo("Asia/Shanghai")`, not installed) — logged as non-fatal
     warnings at startup; the rest (including the Brave-backed engine that
     actually answers most queries) work fine. Not worth chasing unless a
     specific missing engine turns out to matter.
   - **Must actually be running** for this path to work at all — closing
     the `start_searxng.bat` window stops it, and `search_web` just falls
     through to Brave/mock, no crash either way.
2. **Brave Search API** — kept only as an optional fallback for whoever
   already has a paid key; NOT required, NOT the default path anymore, and
   not something to push someone toward setting up given the cost concern
   that started this whole detour.

Falls back to honest mock results (`mock: true` + a note) if NEITHER
SearXNG nor Brave is reachable — verified live earlier (before SearXNG
existed): asked under Ollama, it correctly called `search_web`, got the
mock note, and told the CEO plainly that real results need setup, rather
than presenting placeholder data as real. The tool's own description
tells Claude to keep preferring its native web_search/web_fetch and only
reach for this one when those aren't available — same "tool description
competes with the system prompt" lesson as the `get_x_mentions` gotcha
above, applied deliberately this time instead of accidentally.

**Four real bugs found and fixed via live CEO testing under Ollama,
2026-09-29 — plus one genuine, still-open model-quality ceiling**:
1. **Origin airport didn't reliably default to Dubai.** Claude infers this
   from "a BMW dealership group in Dubai" in the system prompt; a small
   local model doesn't reliably make that leap (sometimes it did, sometimes
   it explicitly asked). Fixed with an explicit line in `_SYSTEM_PROMPT_BASE`:
   "He is based in Dubai — assume DXB as the default origin airport... don't
   ask him to confirm something this obvious." Helps both providers, costs
   Claude nothing.
2. **The model sometimes narrated raw data instead of the actual answer**
   ("The provided XML response contains..." — confirmed via code review
   that `flight_tool.search_flights` returns clean JSON, never XML; this
   was pure model confabulation, not a real data bug) and, separately,
   **stated dates in its final answer that didn't match what it actually
   searched with** (told the CEO "October 5–12" while the real
   `search_flights` call used `2026-09-28` to `2026-10-04` — confirmed via
   live tool-call logs). Fixed with two explicit prompt additions: state
   route/dates/prices EXACTLY as they appear in the tool result, never
   from memory of the conversation; and never describe a tool result's
   raw format ("the JSON data", "the XML response") at all.
3. **The most serious one: asked to book a flight, the model fabricated a
   fake `ignav_id`/price/flight numbers AND falsely told the CEO "I have
   booked this flight for you"** — a direct violation of two explicit
   system-prompt rules ("never invent an ignav_id", "never imply a fare is
   already booked"). **The existing structural gate already made this
   harmless in practice** — confirmed live: POSTing the fabricated
   `ignav_id` to `/booking-link` correctly returned "This fare wasn't from
   a search in this conversation," exactly as designed (see
   `get_known_itinerary`/`_known_itineraries`) — but showing the CEO a
   plausible-looking approval card with invented flight details, before he
   ever clicks anything, is still a real, confusing problem. Fixed at TWO
   levels: (a) the system prompt now says, in the strongest terms used
   anywhere in this file, that every field in an `agmc-booking` block must
   be copied from a real tool result and that the model must never say a
   fare IS booked in any form; (b) a real, deterministic code-level check,
   `orchestrator._validate_booking_blocks`, strips any `agmc-booking` block
   whose `ignav_id` isn't a real one this session's `search_flights`
   actually returned (checked via the same `get_known_itinerary` the
   `/booking-link` endpoint already trusts) and replaces it with a plain
   note — called from `_run_ollama`/`_run_stream_ollama` right before the
   reply is returned/appended to history. **Re-tested live after the fix**:
   the model fabricated another fake booking, and this time the card never
   reached the reply at all — replaced with "That flight reference wasn't
   from a real search in this conversation..." **Ollama-only for now** —
   Claude's real token-by-token streaming can't un-send text already
   shown the same way, and this failure was only ever observed under a
   weak local model. If Claude is ever seen doing this too, the fix needs
   to move earlier (validate before any streaming starts, not after).
4. **Asked to read the inbox, the model sometimes called `read_emails`
   correctly, got real data back (e.g. a genuine email subject like
   "Citizen Developer Training Programme"), then ALSO called `search_web`
   using that subject line as a search query, and answered ONLY from the
   irrelevant web results — discarding the real inbox data entirely.**
   Confirmed via live tool-call logs (`read_emails` then immediately
   `search_web({'query': 'Citizen Developer Training Programme', ...})`).
   Same root-cause pattern as the existing `get_x_mentions` gotcha above —
   a tool's own description competing with the system prompt — applied to
   a new case: `search_web`'s description now explicitly says never to
   call it to "look up, verify, or find out more about" text that came
   from another tool's result, and `_SYSTEM_PROMPT_BASE` now states
   read_emails/read_calendar's output is "the complete, authoritative
   answer... report it directly," never something to re-search. **Re-tested
   live after the fix: the specific web-search-hijack pattern didn't
   recur** — but a DIFFERENT, still-unfixed confusion did (see below).
5. **This exact failure mode recurred through a SECOND tool 2026-09-29 —
   confirms it's a structural pattern, not a one-off**: asked to "summarize
   my inbox" three times in a row, the CEO got back "I found Fatima
   Masood's email to be Fatima.masood@agmc.com — do you want to send her
   an email?" every time — `read_emails` WAS called correctly underneath,
   but the model also called `resolve_contact` on a name it spotted in the
   real inbox data and answered only from that. Same exact mechanism as
   the search_web hijack above, different tool. Fixed the same way:
   tightened `resolve_contact`'s own description to only fire on an
   EXPLICIT request to email/schedule someone, never just because a name
   appears somewhere, and generalized the system-prompt instruction
   (previously singled out web_search/search_web by name) to cover any
   tool and any incidental detail in a read_emails/read_calendar result.
   **Re-tested immediately after: the resolve_contact hijack was gone, but
   "summarize my inbox" on a totally fresh session STILL got hijacked —
   this time back to the ORIGINAL search_web bug, on the same "Citizen
   Developer Training Programme" email subject**, despite that
   instruction still being present in the prompt. This is the important
   finding: patching one tool's over-triggering doesn't fix the
   underlying tendency, it just shifts which tool gets hijacked next.
   **Don't keep whack-a-moling this one tool at a time** — if it needs a
   real fix, the robust option is to dynamically restrict which tools are
   even OFFERED to the model based on the request (e.g. a pure "check my
   inbox" turn only exposes read_emails/read_calendar/open_outlook,
   nothing else, so there's nothing left to hijack TO) rather than
   trusting prompt wording alone to hold under this model's real attention
   limitations. Not built yet — a real, separate decision (this kind of
   intent-based tool filtering is itself a little fragile for mixed
   requests like "check my inbox and email John") — ask before assuming
   either way.

   **Built 2026-09-29, direct follow-up ask ("build that")**: this
   tool-scoping fix, implemented as proposed. `orchestrator.
   _scoped_tools_for_ollama(user_message)` keyword-matches the CEO's own
   message against a small set of categories (`_CATEGORY_KEYWORDS` —
   email, calendar, files, social, web_current, flights, hotels) and, if
   one or more match, restricts that turn's tools to the union of those
   categories (`_TOOL_CATEGORIES`) — e.g. "summarize my inbox" now only
   ever sees `read_emails`/`open_outlook`/`resolve_contact`, nothing else
   exists for it to hijack TO. **Deliberately permissive when uncertain,
   not a hard allowlist**: zero matched categories (an ambiguous message,
   or a plain follow-up like "next week to london" with no topic keyword
   of its own — the exact shape of message that led to the read_calendar
   hijack documented above) falls back to the FULL `TOOLS` list unchanged,
   same as before this fix existed. Only wired into the Ollama path
   (`_run_ollama`/`_run_stream_ollama` compute it from that turn's own
   `user_message` and pass it into `_ollama_turns`, which now takes
   `available_tools` as a real parameter instead of hardcoding `TOOLS`) —
   Claude's own tool selection has never shown this failure mode, and
   narrowing its options could only hurt a legitimate multi-part request
   it already handles correctly; `run()`/`run_stream()` are untouched.

   **Verified live, all three cases**: (1) "summarize my inbox" — a real,
   clean, accurate inbox summary, zero hijack, confirmed via logs it
   called ONLY `read_emails`; (2) "book a flight" → explicit dates — the
   full search still worked correctly, confirmed via logs no
   `read_calendar` call snuck in; (3) "send an email to Fatima Masood..."
   — `resolve_contact` still fires correctly when it's a genuine part of
   the request, not blocked by the narrowing. **Known, accepted limit**:
   a follow-up message with no topic keyword of its own (e.g. "next week
   to london" right after "book a flight for me") still falls back to the
   full tool list, since there's nothing in that specific message to
   scope from — this fix reduces the hijack surface for messages that
   self-announce their topic, it doesn't guarantee protection across
   every turn of a multi-message exchange. If a hijack is ever seen again
   mid-conversation rather than on an initiating message, that's the gap
   to look at next, not evidence this fix regressed.

6. **Open, NOT fixed — a genuine model-quality ceiling, not a targetable
   bug**: even after fix #4, a fresh retest of the exact same "read my
   inbox" flow got back a reply about "hotel search page links" —
   something nobody asked about at all, with `read_emails` still called
   correctly underneath (confirmed via logs) but the final text answering
   a completely different, seemingly hallucinated question. This isn't
   tied to any specific tool-description conflict the way #4 was — it
   looks like `qwen2.5:3b` occasionally just loses track of what the
   actual question was, plausibly from managing a long, multi-topic
   system prompt (~9 tool definitions, flights/hotels/social/email/
   calendar/files all in one prompt) that's a lot for a 3B model to hold
   onto reliably. **Don't keep chasing this with more targeted prompt
   patches one hallucination at a time** — if it needs a real fix, the
   next lever is a SHORTER, Ollama-specific system prompt (splitting
   `_system_prompt()` per provider, trimming it to only what's relevant)
   to reduce what the small model has to track at once, not another
   patch for this one specific non-sequitur. Until that's built, treat
   Ollama mode as best-effort for anything beyond simple, single-topic
   questions — this is the real, current reliability ceiling for a model
   this size, not a bug waiting to be squashed.

**A real architecture bug found 2026-09-29 — NOT the "model-quality
ceiling" it initially looked like — explains most of the "loses context
instantly" complaints above.** The CEO reported: searched for a flight,
then immediately asked for the booking link, and the model acted as if no
search had ever happened, asking for origin/destination again. Root
cause, confirmed by reading the code, not guessed: `_run_ollama`/
`_run_stream_ollama` built each turn's Ollama `messages` list fresh from
`llm_providers.flatten_history(history)`, ran the tool-calling loop
against THAT local list, then appended only the FINAL TEXT REPLY back
into the canonical `_sessions[session_id]` history — every tool call and
its real result (a real `ignav_id`, price, flight number from
`search_flights`) lived only in the throwaway local `messages` list and
was gone the instant the turn ended. The model's only memory of a fare
search, one turn later, was its own possibly-lossy PROSE SUMMARY of it —
this is also the real explanation for the earlier fabricated-`ignav_id`
"I have booked this" incident (see the `_validate_booking_blocks` entry
above): it wasn't hallucinating out of nowhere, it genuinely had no real
fare data left to reference at all.

**Fixed properly, not just patched**: `_ollama_turns` now yields a
`{"type": "tool_round", "records": [...]}` event once every tool call from
a single Ollama response has actually run, and a new
`orchestrator._persist_tool_round` turns that into genuine Claude-shaped
history turns — an assistant turn holding only `tool_use` blocks, followed
by a user turn holding only the matching `tool_result` blocks, the EXACT
two-turn shape Claude's own `_run_tools` already produces. This isn't
cosmetic: Claude's Messages API only accepts `tool_result` blocks inside a
user turn and `tool_use` blocks inside an assistant turn, strictly
alternating — getting this shape right is what makes the fix safe for a
future provider switch too, not just Ollama-internal bookkeeping.
`llm_providers.flatten_history` already knew how to render `tool_use`/
`tool_result` blocks into readable text (built for the Claude path
originally) — no changes needed there at all, it just now actually
receives the real data. **Verified live, before and after**: before the
fix, asking to book right after a search produced a fabricated ignav_id
and a false "already booked" claim (caught by `_validate_booking_blocks`,
but only after the fact). After the fix, across several follow-up turns
in the same session, the model correctly recalled and accurately restated
REAL flight numbers, times, and prices from the actual `search_flights`
result — no fabrication, genuinely working from persisted data.

**What this fix does NOT solve, confirmed in the same test session — a
real, separate model-quality issue, not an architecture bug**: even with
real data correctly available, the model still doesn't reliably commit to
producing the `agmc-booking` fenced block on request — it sometimes
re-asks for details already given, and in one case, several turns into
the same conversation, it suddenly ran `search_flights` with completely
unrelated, invented parameters (a different destination, different dates,
different passenger count) with no prompting to do so at all. This is the
same underlying "loses the thread" ceiling documented above — now clearly
NOT explained by the history bug, since accurate data was demonstrably
available and used correctly for several turns before this happened.
`_validate_booking_blocks` remains the real safety net for whatever this
produces; don't mistake persisted history for a fix to model reliability
generally.

**Operational setup — NOT part of this code change, has to happen
separately on whatever machine runs this**: install Ollama itself (a
separate program, not a pip package — the `ollama` pip package added to
`requirements.txt` is only the API client) and run it as a background
service (`ollama serve` — or the Windows installer already sets this up as
a service), then pull whichever model `OLLAMA_MODEL` points at (default
`llama3.2:1b`, in `config.py` — override via the `OLLAMA_MODEL` env var if
1b's quality/tool-reliability proves too weak once actually tested live;
`ollama pull llama3.2:1b` downloads it, a few hundred MB to a few GB
depending on the model). **None of this Ollama-switch code has been tested
against a real, running Ollama daemon on this machine** — this dev machine
has neither Ollama nor any model pulled at the time this was built; the
`ollama` pip package import, the tool-schema conversion, and the
history-flattening logic were all verified directly (real unit-style
checks, not just read-through), but the actual live round-trip against a
running model — including whether `llama3.2:1b` reliably calls tools at
all in practice — has not been. Verify that live before trusting it for a
real send-email/booking approval.

**`qwen2.5:7b` tried 2026-09-28, ruled out on latency alone, not accuracy**
— this machine's GPU is an Intel Arc 140T, which Ollama does not accelerate
by default (no CUDA/ROCm support), so anything beyond the smallest models
runs CPU-only. A single search_flights exchange under `7b` was still
running after 46 minutes of real CPU time (confirmed via the actual
`llama-server` process, not a hang — genuinely still computing), long
before any answer came back. **Don't retry `7b` (or anything bigger) on
this hardware without first sorting out real GPU acceleration** — Intel
Arc needs Ollama's experimental SYCL/oneAPI backend, not the default
build; until that's set up and verified, `qwen2.5:3b` is the practical
ceiling for local-model size here, independent of whether a bigger model
would answer more reliably.

**Live model comparison, done 2026-09-28** (`llama3.2:1b` vs `llama3.2:3b`
vs `qwen2.5:3b`, same test battery each time — see chat history for the
exact prompts/results): `llama3.2:1b` and `llama3.2:3b` both hallucinated
spurious tool calls on completely unrelated prompts ("say hello in five
words" → both tried to read a nonexistent file); `3b` additionally failed
plain arithmetic outright (returned a fake, nonexistent `"math"` tool call
as literal text instead of answering). **`qwen2.5:3b` was clearly better
on plain Q&A** — no hallucinated tool calls on the unrelated/math prompts,
and it correctly read the REAL Outlook calendar (not mock — Outlook was
open at the time), including correctly truncating a real 300+-attendee
event via `MAX_ATTENDEES_SHOWN`. **Switched `OLLAMA_MODEL` default in
`config.py` to `qwen2.5:3b`** on the strength of this.

**A real, serious bug found in the same test — since fixed**: asked to
send an email, `qwen2.5:3b` correctly called `resolve_contact` first, then
never emitted the required ` ```agmc-send-email ` fenced block at all —
it called `read_emails` with fabricated, wrong-shaped arguments instead
and told the CEO "I'll send it now" to a fabricated address, with no
approval card ever triggered. **Root cause, found immediately after**:
`_run_ollama`/`_run_stream_ollama` were building Ollama's `messages` list
from `llm_providers.flatten_history()` ALONE — with no system-role message
at all. Unlike Claude's `client.messages.create(system=...)`, Ollama's
`chat()` has no separate `system=` kwarg; a system prompt only reaches it
as a normal `{"role": "system", ...}` message. So `_system_prompt()`'s
ENTIRE contents — including the whole fenced-block convention the
approval-gate UI depends on — was silently never sent to Ollama at all,
for any of the model comparisons above. **Fixed** by prepending
`{"role": "system", "content": _system_prompt()}` to `messages` in both
functions. Re-tested live after the fix, same exact prompt: `qwen2.5:3b`
now correctly resolves the contact, uses the real resolved address (not a
fabrication), and emits a well-formed ` ```agmc-send-email ` block with
valid JSON — the approval-gate flow genuinely works under Ollama now.
**Don't drop that prepended system message again** — without it, none of
`_system_prompt()`'s instructions apply, not just the email format: the
"BMW not Mercedes" framing, "call resolve_contact before guessing", the
whole booking/calendar convention, all of it silently doesn't exist as far
as Ollama is concerned. This also means the earlier `1b`/`3b`/`qwen2.5:3b`
comparison above ran with ZERO system prompt in every case — the
hallucinated-tool-call behavior on plain prompts may look somewhat
different with the fix in place; treat that comparison as a same-footing,
now-slightly-stale baseline, not a final verdict.

**Same fix also grounds relative dates** ("next week", "tomorrow") by
appending today's real date to the system prompt, computed fresh per
request (`_system_prompt()`, UAE has no DST, fixed UTC+4). Before this,
NEITHER provider had any notion of "today" at all — a real, provider-
agnostic gap, just more exposed under a weaker model that fabricates a
cover story instead of asking a clarifying question when a guessed date
gets rejected (confirmed live: `qwen2.5:3b` asked "next week" guessed
`2026-09-20` — actually in the PAST relative to the real "today" of
2026-09-28 — got a real API rejection, and before the date-grounding fix
invented a nonexistent "company's internal database for internal
flights" to cover for it). **Even with grounding, don't assume a small
local model reliably computes relative-date arithmetic** — re-tested
after the fix: it now correctly asks for a clean explicit date instead of
fabricating an excuse, which is the real, meaningful improvement, but it
still doesn't reliably land on the right date on the first try. Failing
safely (asking) instead of failing confidently (inventing) is the actual
win here — expect to still need a clean date from the CEO sometimes.

## Architecture

- **Correction (2026-09-23): the `agents/` directory described below does not
  exist in the checked-out code.** `orchestrator.py` is currently the **flat
  single-orchestrator** version: one `TOOLS` list + `TOOL_DISPATCH` dict, all
  tool modules under `tools/` called directly. If a multi-agent refactor is
  still wanted, it hasn't landed yet — don't assume `agents/*.py` exists
  without checking first.
- (Historical note, kept for context on the earlier discussion) A multi-agent
  version was previously built, delegating to `agents/office_agent.py`,
  `agents/social_agent.py`, `agents/files_agent.py`, `agents/research_agent.py`
  (stateless, shared loop in `agents/_base.py`). **Verdict, already discussed
  and agreed: multi-agent is objectively worse for this system's actual size**
  (7+ tools) — it roughly doubles API calls/latency for the same answer. If
  it's reintroduced, that tradeoff is still true.
- When uploads existed under the multi-agent version, `/upload` injected
  content directly into the orchestrator's own session history
  (`add_uploaded_file`), bypassing `files_agent` entirely, because sub-agents
  are stateless and can't see it otherwise. Preserve that exception if
  sub-agents come back.
- `POST /chat/stream`'s SSE payload has two event shapes, not one (added
  2026-09-24 for the "what is it doing right now" status indicator):
  `{"text": "..."}` for streamed answer tokens (unchanged), and
  `{"status": "<real tool name>"}` — `orchestrator.run_stream` yields the
  latter right before it actually calls a tool, using the tool's real name
  from `orchestrator.TOOLS`/`TOOL_DISPATCH` (`read_emails`, `read_calendar`,
  `search_flights`, etc.), not a guess based on which shortcut chip the CEO
  clicked. The frontend's `TOOL_STATUS_LABELS` map
  (`agmc-assistant-popup.html`) turns that into a friendly line ("Searching
  live fares…") shown above the typing dots while waiting. If a new tool is
  ever added to `orchestrator.TOOLS`, add its label to `TOOL_STATUS_LABELS`
  too or it'll just fall back to a generic "Working on it…" — not broken,
  just less specific than it could be.
- **The status indicator above does NOT actually fire for `web_search` or
  `web_fetch`**, even though both have entries in `TOOL_STATUS_LABELS` —
  found 2026-09-24 while adding `web_fetch`, not yet fixed. Both are
  Anthropic *server* tools: the search/fetch happens server-side within
  the SAME model turn, represented as a `server_tool_use` content block,
  not a `tool_use` block. `orchestrator.run_stream`'s status-yielding loop
  only scans for `block.type == "tool_use"` (client-dispatched tools we
  run ourselves via `TOOL_DISPATCH`) — a pure web_search/web_fetch turn
  never even reaches that loop, since `final_message.stop_reason` is
  `"end_turn"`, not `"tool_use"`, for a turn where the server tool result
  is already woven into the same message. Not a crash or wrong-data bug —
  the CEO just sees the plain "..." typing dots (same as before this
  status feature existed) during those two tools' calls instead of a
  friendly label. Fixing it properly means switching
  `client.messages.stream(...)`'s consumption from the convenience
  `.text_stream` to raw event iteration (watching for
  `content_block_start` events whose block type is `server_tool_use`) —
  deliberately not done yet since it risks the already-working, tested
  text streaming for a cosmetic gap nobody has actually reported. If this
  is picked up later, verify the raw event shape against the current SDK
  version first rather than assuming the shape described here still
  holds.
- **Brand colors updated 2026-09-25** — CEO-provided: `#153804` (dark
  green) + `#f1ede9` (beige). This landed in three rounds, each a real
  direction change, not one clean pass — check the CURRENT state in
  `agmc-assistant-popup.html`'s `:root` before assuming any single round
  below is still accurate:
  1. **Faithful swap** (first round): `--bg`/`--accent` changed only,
     `--accent` kept its existing narrow supporting role (mascot icon,
     header mark, subtle highlights) — the send button and the CEO's own
     bubbles stayed `--text`/`--bg` (near-black/cream), untouched. A
     bolder full-rebrand option was offered and explicitly declined at
     this point in favor of this narrower one.
  2. **Panel background → beige too** (direct CEO follow-up, same day):
     `--surface` (panel/dropdown/approval-card background) changed from
     white to match `--bg` exactly — white looked disconnected from the
     new beige page background once round 1 landed.
  3. **The CEO changed direction and asked for the bolder option after
     all** — a solid dark-green header bar, white chips/+-button/dropdown
     for contrast against the now-beige panel, and the CEO's OWN chat
     bubbles recolored green (`.msg.user .bubble` now `var(--accent)`,
     was `var(--text)`). **This directly contradicts round 1's "declined
     the bolder option" note above — round 1 wasn't wrong when written,
     the CEO's own direction genuinely changed. Don't "fix" round 3 by
     reverting to round 1's narrower scope; check which round's
     description matches the live CSS before changing anything here.**
     New variables added (all in the base `:root` only, deliberately
     theme-invariant — the header is a fixed brand ribbon, not something
     that should flip look between light/dark mode): `--header-bg`
     (`#153804`), `--header-fg` (white), `--header-fg-muted` (translucent
     white, for the status text), `--status-ok` (`#57A230` — a BRIGHTER
     green than `--accent`/`--header-bg`, used only for the "connected"
     dot; the header background IS the dark brand green, so the dot
     needs its own brighter shade or it's illegible against itself —
     don't revert this dot to `var(--success)`, that's the exact
     legibility bug this fixes), and `--chip-bg` (white in light mode,
     matching dark mode's existing `--surface` value in dark mode since
     chips already had natural contrast there and weren't part of this
     ask). `--success` itself is UNCHANGED and still used as-is for
     approval-card outcomes elsewhere (those sit on light surfaces, no
     legibility conflict) — the header needed its own brighter green
     specifically because its background is always dark, not because
     `--success` itself was wrong.

  Constant across all three rounds: `--success` was deliberately never
  reused as the brand accent (already a distinct-enough shade, and
  conflating "success" with "this is just the brand color" would erode
  its meaning now that green shows up everywhere). Dark mode's `--accent`
  is brightened (`#57A230`, same yellow-olive hue family as `#153804`,
  not a generic/unrelated green) since the raw brand green is too dark to
  read against a dark background — same reasoning the old gold accent
  already used (`#8A6A2F` → `#C9A65C` for dark mode); `--status-ok` reuses
  this exact value since it's solving the identical problem.

  `icon.ico` (the Desktop shortcut / Electron tray+window icon) was
  originally regenerated to match round 1's green — a simple filled
  circle + white eyes/smile, proportioned directly off the mascot's own
  live SVG (`viewBox 24x24`, eyes at `(8,11)`/`(16,11)`, smile
  `(8,16)`→`(16,16)`) rather than guessed freehand, so the static icon and
  the live mascot stayed visually consistent. **Superseded 2026-09-25**
  (see the next entry below) — the CEO asked for the real AGMC wordmark
  as the app icon instead, not the mascot face. If the mascot-face style
  is ever wanted back for some OTHER icon use, regenerate it the same
  way described above; don't confuse that history with what `icon.ico`
  actually contains now.
  - **`icon.ico` is now the real AGMC wordmark logo (CEO-supplied PNG),
    not the mascot face** — the same logo image used for the header's
    `.mark` icon (see the gotcha for that, above). Since the logo's own
    source PNG is a plain white canvas (384x425px, not square, not
    transparent) rather than a small flat-color icon shape, it's
    composed onto a fresh square white canvas first (small margin, logo
    scaled via Lanczos resampling to fit) before being saved as a
    multi-size `.ico` (16/32/48/128/256) — regenerating directly from the
    raw non-square source would let ICO's own per-size resize distort
    the aspect ratio. The one-off script used for this
    (`make_icon.py`, run once via the backend venv's Python for Pillow,
    not kept in the repo) is straightforward to reproduce: open the
    source logo PNG, `Image.new("RGBA", (256,256), (255,255,255,255))`,
    resize+center-paste the logo onto it, `.save(..., format="ICO",
    sizes=[...])`. If the CEO ever supplies an updated/higher-res logo
    file, regenerate `icon.ico` this same way rather than trying to
    edit the existing `.ico` in place.
  - **Round 1 also introduced a real bug, only caught by explicitly
    checking dark mode afterward**: the mascot's eyes/smile SVG uses
    `--accent-text`, which round 1 set equal to `--accent` itself
    (`#153804` = `#153804`) — making the face LITERALLY the same color
    as the circle it's drawn on, invisible (confirmed via computed
    styles returning identical rgb values, not just subjectively "hard to
    see"). `--accent-text` has two genuinely incompatible jobs: dark
    text/icon on the PALE `--accent-bg` tint (the header mark icon —
    correct as `--accent-text`), vs. a light icon drawn directly on the
    SOLID `--accent` fill itself (the mascot face — needs the opposite
    color entirely). Fixed by adding `--accent-fg` (white, theme-
    invariant, same pattern as `--header-bg`/`--status-ok`) specifically
    for the second case, and pointing the mascot's eye/smile SVG
    attributes at it instead of `--accent-text`. **If a future color
    change makes the mascot's face disappear again, check for this exact
    dual-use conflict first** — it's not a one-off, it's a naming trap
    (`--accent-text` sounds like it should just mean "text-colored-for-
    accent," but it specifically means "on the pale tint," not "on the
    solid fill").
  - **Dark mode's chips and assistant-reply bubble have very low fill
    contrast against the panel** — confirmed via exact computed colors,
    not just eyeballing (panel `#191F16`, chips identically `#191F16`,
    assistant bubble `#1E251B` — a ~5-point-per-channel difference,
    distinguishable mainly by a thin border, not real contrast). This is
    PRE-EXISTING dark-mode behavior, not something the 2026-09-25 brand
    changes broke — it just became more noticeable next to the new bright
    green accents elsewhere. Not yet fixed; ask before changing it if it
    comes up again, since dark mode's low-key/monochrome look may be
    intentional rather than an oversight.
  - **The green header showed a thin light outline around its rounded
    corners when the desktop widget floats over a non-white background**
    (e.g. a photo wallpaper) — found 2026-09-25 from a real CEO screenshot.
    Root cause: `.palette`'s base rule has always had
    `border: 0.5px solid var(--border-strong)` (a translucent dark color,
    `rgba(27,27,29,0.18)` in light mode), there to visually separate the
    panel from a plain white page background in the website-embedded-
    widget case. `.app-mode .palette` overrides `box-shadow` to `none` but
    never touched `border` — so in the desktop app, that same translucent
    ring sits directly at the transparent Electron window's own edge, with
    nothing but the real (arbitrary, often dark) desktop behind it to
    blend into, and reads as a stray light outline around the solid green
    fill instead of a subtle separator. Fixed by adding `border: none;` to
    `.app-mode .palette` — confirmed via computed styles
    (`getComputedStyle` showed the border collapse to `0px none` with
    `app-mode` applied, while `.head`'s solid `--header-bg` fill and the
    16px `border-radius`/`overflow:hidden` clip were unaffected). **If a
    future style change reintroduces a border/outline on `.palette` or
    `.head` for the website-embed case, re-check it's excluded from
    `.app-mode .palette` too** — anything drawn at the very edge of that
    element is drawn at the real transparent window's edge in the desktop
    app, with no white page to soften it.

## Known gotchas — already solved once, don't relearn them

- **The header's `.mark` icon (top-left, next to "AGMC Assist") is the
  real AGMC wordmark logo, not a generic house/home SVG icon anymore** —
  swapped 2026-09-25 from a placeholder icon to the CEO-supplied PNG,
  embedded directly as a base64 `data:image/png;base64,...` `<img>` src
  right in `agmc-assistant-popup.html`, not a separate linked file —
  consistent with this doc's own "frontend is a single self-contained
  HTML popup" description (no `frontend/assets/` folder exists, and this
  doesn't start one). If the logo ever needs updating, replace that data
  URI wholesale (re-encode the new PNG to base64) rather than trying to
  edit it in place. The image itself already has its own plain white
  canvas around the wordmark (confirmed 384x425px, not a transparent
  PNG) — the `.mark` container's `background: var(--accent-bg)` sits
  behind it as a fallback/letterbox color only, don't read a future
  slight mismatch at the box's very top/bottom edge (from the image's
  non-1:1 aspect ratio inside a 26x26 square, `object-fit: contain`) as
  a bug worth chasing — it's a sub-2px sliver, not a real defect.

- **The floating mascot bubble (`#mascotBtn`, bottom-right circle /
  desktop-app widget) is now the official BMW+AGMC dealer logo image, not
  the eyes/smile SVG face** — swapped 2026-09-25, direct CEO feedback that
  the smiley face read as unprofessional. First source was a CEO-supplied
  2000x1500 WEBP (black background, white BMW roundel + "AGMC" wordmark),
  cropped to a centered 1500x1500 square (content bbox measured first to
  confirm the crop doesn't clip the roundel or text — it didn't, generous
  margin on all sides) and downsized to 240x240. **Superseded same day**:
  the CEO found the black background "too jarring" and supplied a second
  logo — a 720x720 JPG, ALREADY square, white background, same BMW roundel
  + "AGMC" wordmark centered with generous symmetric margin (confirmed via
  the same content-bbox measurement, no cropping needed this time) — just
  downsized to 240x240 and swapped in wholesale, same embedding mechanism.
  If the logo needs updating again, re-run this same measure-then-resize
  process on the new source image; don't assume every future logo will
  already be square like this second one was. Embedded the same way as
  the header `.mark` logo: a base64 `data:image/png;base64,...` `<img>`
  src directly in `agmc-assistant-popup.html`, `object-fit: cover` +
  `border-radius: 50%` on the img itself so it self-clips to a circle
  regardless of `.mascot`'s own `overflow` setting (deliberately did NOT
  add `overflow:hidden` to `.mascot` — its pulsing `::before` ring
  animation depends on rendering OUTSIDE the button's own box via
  `inset:-6px`, and clipping the parent would have broken that). The
  old eyes/smile SVG (`.eye`, `.eye.r`, the `blink` keyframes, the
  `.mascot svg` sizing rule) was deleted outright, not just replaced in
  the markup — confirmed nothing else in the page referenced those
  selectors before removing them.
  - **This swap exposed a real, previously-invisible layout bug**: the
    new `<img width:100%;height:100%>` came out a few px smaller than the
    button on all sides (confirmed via `getBoundingClientRect()` — a 72px
    app-mode button rendered the image at only 60x70). Root cause:
    `.mascot` is a `<button>`, and browsers' default UA stylesheet gives
    `<button>` non-zero padding (Chrome: ~`1px 6px`) that this project's
    CSS never explicitly zeroed — invisible for over a year because the
    old content (a small fixed 26x26 SVG, just flex-centered) never
    needed to fill the box exactly, so the shrunk content area was never
    noticed. A percentage-sized flex child resolves against the
    CONTENT box (post-padding), so the new fill-the-box image made this
    latent gap visible as a thin unfilled ring. Fixed with a plain
    `padding: 0;` added to the base `.mascot` rule. **If the mascot's
    visible content is ever swapped again, verify fill with
    `getBoundingClientRect()` on both the button and its content, not a
    screenshot** — this project's own history already found automated
    screenshot capture of this specific tiny transparent widget unreliable
    (see the "Bubble rendered as a squashed ellipse" gotcha above), and a
    few-px sizing gap like this one is exactly the kind of thing that's
    hard to eyeball but trivial to catch numerically.
  - The header's `.mark` icon (AGMC wordmark, see the gotcha just above)
    is UNCHANGED — this only replaced the mascot bubble's face. Don't
    conflate the two; they're different images serving different spots.
  - **The desktop app's mascot bubble had NO bob/pulse animation at all —
    found 2026-09-25, direct CEO ask ("why is it completely still").**
    Root cause: `.app-mode .mascot` had been forced to a literal
    `width:72px;height:72px` (filling the ENTIRE 72x72 window edge-to-
    edge, zero margin) plus an explicit `animation: none;` — but that
    72px sizing was leftover debugging cruft from the "squashed ellipse
    on cold launch" saga above (attempt 5, "switched to literal 72px...
    also did not fix it"), never reverted once the REAL fix
    (`position:relative`, same rule) was confirmed. It also silently
    contradicted `desktop/main.js`'s OWN comment on `BUBBLE_SIZE`("tight
    around the 56px mascot circle + a little breathing room") — a 56px
    circle in a 72px window IS that 8px margin; a 72px circle filling a
    72px window has none. With zero margin, ANY `translateY` bob has
    nowhere to move into — it clips instantly against the window's own
    physical edge, which (unlike a webpage) is a hard boundary, not an
    expandable canvas. Fixed by restoring `.app-mode .mascot` to 56x56
    (matching the base, non-app-mode rule) and removing the
    `animation: none;` override, plus a new `.app-mode body {
    display:flex; align-items:center; justify-content:center; }` so the
    now-smaller button sits centered in the window instead of pinned to
    body's top-left corner (verified via `getBoundingClientRect()`:
    56x56 button at `(8,8)` inside a 72x72 window — exactly centered).
    Confirmed safe for the expanded/panel state too: `.app-mode
    .palette.open` already forces `width/height:100%`, so flex-centering
    a child that already fills its container is a no-op there, and
    `mascotBtn` is fully `display:none`d via JS whenever the panel is
    open anyway (see the click handlers) — body never needs to center
    two visible things at once.
    - **`box-shadow` and the `::before` pulse-glow ring stay OFF, on
      purpose, not reverted** — `--shadow`'s blur/spread (20px+) and the
      pulse ring's scale-to-1.7 animation both need far more than an 8px
      margin to avoid clipping against the window edge; only the bob
      motion was actually asked for, so only that was restored. If the
      pulse ring is ever wanted too, it needs its own real fix (e.g. a
      bigger `BUBBLE_SIZE` in `main.js`), not just deleting its
      `display:none`.
    - **Could NOT be visually confirmed by animation playback in this
      project's own automated browser-testing tool** — `getComputedStyle
      (...).transform` was sampled repeatedly on both the fixed app-mode
      mascot AND the untouched, already-shipping non-app-mode default
      mascot, and BOTH stayed frozen at the identity matrix the whole
      time, proving CSS animations simply don't tick in that specific
      automated tool (consistent with this doc's own earlier finding that
      automated screenshot capture of this exact tiny transparent widget
      is unreliable — see the "Why self-verification kept failing" note
      above). What WAS verified there, reliably: the computed
      `animation-name`/`animation-duration` resolve to `bob`/`2.6s` (not
      `none`), `box-shadow` resolves to `none`, and the button's exact
      centered geometry. **The real, authoritative test for whether this
      actually bobs is relaunching the real Electron app** — don't trust
      a lack of visible motion in an automated tool as evidence the CSS
      fix didn't work; check the computed animation properties instead.

- **The entire frontend had real, on-disk mojibake corruption — every em
  dash, en dash, ellipsis, right-single-quote, arrow, and one emoji
  (⚠️) in `agmc-assistant-popup.html` was silently double-encoded** —
  found 2026-09-29 when the CEO reported literally seeing
  "Searching live faresâ€¦" on screen and correctly refused to accept it
  as cosmetic. **First wrongly dismissed earlier in this same session** as
  a rendering artifact of the coding assistant's own file-reading tools —
  that was wrong; a Python read-and-decode of the raw file bytes even
  SEEMED to show correct characters at one point, which turned out to be
  the assistant's own terminal-output channel masking the real corruption
  when displaying results, not evidence the file was fine. The only
  reliable ground truth was checking from INSIDE a real browser: `fetch()`
  the page's own raw bytes and inspect them directly (`c3 a2 e2 82 ac
  c2 a6` where a correct UTF-8 ellipsis should be 3 bytes, `e2 80 a6`) —
  don't trust a terminal/file-read check alone for this class of bug
  again, verify from inside the actual rendering client.
  Root cause: this file was edited at least twice via raw PowerShell
  string operations (see the "AGMC X" rename and `.mark` div removal
  entries above, both done "via a PowerShell whole-string replace" /
  "PowerShell line-filtering") — Windows PowerShell 5.1 defaults to the
  system ANSI codepage (Windows-1252 here) for both reading and writing
  text unless `-Encoding utf8` is passed explicitly (this exact gotcha is
  already called out in this assistant's own PowerShell tool notes) —  so
  the file's real UTF-8 bytes got decoded as Windows-1252, producing
  garbage multi-character sequences, which then got RE-SAVED as UTF-8,
  baking the corruption permanently into the file's actual bytes (not just
  a display issue from that point on). Fixed with a byte-level repair
  script: find runs of 2+ characters reachable only via a Windows-1252
  byte >= 0x80 (built from the FULL 256-entry table .NET/PowerShell
  actually uses, which fills the ~5 codepoints Python's stricter `cp1252`
  codec leaves undefined via identity-mapping — needed for the ⚠️ case,
  which round-tripped through those gap bytes), then re-encode each run as
  that same table and decode as UTF-8, keeping the result only if it's
  valid — this correctly restored 95 em dashes, 17 ellipses, 4 right
  single quotes, 2 en dashes, 1 arrow, and the one corrupted emoji, while
  leaving the file's other genuinely-correct non-ASCII content (e.g. a
  different, uncorrupted emoji elsewhere) untouched. Verified fixed from
  inside a real browser afterward (`TOOL_STATUS_LABELS` values render with
  correct single codepoints, e.g. `U+2026` not the 3-character garbled
  run) — don't trust a text-editor/terminal-only check as sufficient
  verification for this specific class of bug. **If any file in this
  project is ever edited via raw PowerShell string manipulation again,
  pass `-Encoding utf8` explicitly on every read AND write involved** —
  this exact bug is a single missing flag away from recurring, and it
  silently corrupts the file's real bytes, not just what's shown on
  screen once.
- Windows text editors sometimes save `.txt` as UTF-16, not UTF-8.
  `file_tool._read_text` already tries `utf-8-sig` → `utf-16` → permissive
  UTF-8, in that order. Don't simplify back to a plain `.read_text()` call.
- OCR (scanned PDFs) needs Tesseract + Poppler as native Windows binaries,
  not pip packages. User has **no admin rights**, so PATH-based install
  doesn't work for them. If revisiting: use `pytesseract.pytesseract.tesseract_cmd`
  and `convert_from_path(..., poppler_path=...)` with explicit folder paths
  instead of relying on PATH.
- **PDFs with real text AND embedded photos used to lose the photos
  entirely** — added 2026-09-25, direct CEO request after uploading a
  newsletter-style PDF whose images the assistant couldn't see. Root
  cause: `_read_pdf` returned extracted text the moment it found ANY, and
  OCR only ever ran for a fully scanned (zero-text) PDF — a normal mixed
  document's embedded pictures were never looked at either way. Fixed
  using pypdf's own `page.images` (confirmed against the installed 6.19.0
  with a real hand-built test PDF before writing the fix, not assumed) —
  each embedded image decodes through Pillow into a real PIL `Image`
  regardless of the PDF's internal compression/color space, then gets
  re-encoded as a plain sRGB JPEG (`_encode_pdf_image`) so the API always
  receives something it can reliably accept, capped at
  `MAX_PDF_IMAGES = 10` per PDF (a big brochure could otherwise blow up
  token cost/context). Verified end-to-end with a real hand-built mixed
  text+image PDF: uploaded it through the actual `/upload` endpoint, asked
  Claude what color the embedded photo was, and got back the exact color
  of the test image (not a plausible-sounding guess) — genuine vision
  input, not text-described-as-if-seen.
  - **Pillow is a real runtime dependency again** — re-added to
    `requirements.txt` specifically for this (`page.images` needs it to
    decode embedded images into a PIL `Image`). Don't read the earlier
    "Pillow removed, was only ever one-time icon generation" history as a
    reason to remove it again — this is a different, ongoing need.
  - `read_local_file` returns `{"filename", "content", "truncated"}` for a
    PDF as before, PLUS an `"images"` key (list of `{"image_base64",
    "media_type"}`, same shape as the plain-image case) whenever embedded
    pictures were found — omitted entirely for a PDF with no images, so
    the common case is unchanged. `orchestrator._run_tools` and the new
    `orchestrator.add_uploaded_pdf` (used by `/upload`) both build a
    tool_result/history entry with one text block plus one image block
    per picture — the same real vision-input mechanism the plain-image
    case already used, just combined with text this time.
  - `.pdf` was pulled out of the generic `READERS` dict in `file_tool.py`
    and is now handled explicitly in `read_local_file`, because
    `_read_pdf` returns a dict (`{"text", "images"}`), not a plain string
    like every other reader in that dict. If a future edit touches
    `READERS`, don't put `.pdf` back in it without updating
    `read_local_file`'s dispatch to match.
- `python-multipart` and `openpyxl` are required in `requirements.txt` for
  uploads/Excel — both were missing once already and caused real crashes.
- `read_local_file` (and `/upload`) can read `.png`/`.jpg`/`.jpeg` now
  (added 2026-09-23) — real vision input, not a text description. This
  returns a DIFFERENT shape than every other reader (`image_base64` +
  `media_type`, no `content` string), because there's nothing to extract
  as text. `orchestrator._run_tools` special-cases this to build a real
  `{"type": "image", ...}` tool_result content block instead of the usual
  `json.dumps(result)`; `add_uploaded_image` does the same for the
  `/upload` path. If either of those special-cases ever gets "simplified"
  away, images will silently start being sent as a JSON blob of base64
  text instead of an actual image Claude can see — verified this works
  end-to-end with a real test image before calling it done. Cap is 5MB per
  image (Anthropic's own limit) — `_read_image` rejects anything bigger
  with a clear message rather than a cryptic API error.
- CORS is wide open (`allow_origins=["*"]`) — fine for localhost only, must
  be restricted before this leaves the user's own machine.
- **Two consecutive `role: "user"` turns in session history crash the
  stream with a misleading symptom** — found 2026-09-24: upload a file or
  image (`add_uploaded_file`/`add_uploaded_image` append a `user` turn),
  then send a chat message with no assistant reply in between (`run`/
  `run_stream` used to append a second, separate `user` turn) and the
  Anthropic API rejects the request outright ("roles must alternate...").
  `orchestrator._append_user_turn` now merges into the previous turn
  instead of pushing a new one whenever the last turn is already `role:
  "user"` — all four append sites (`run`, `run_stream`,
  `add_uploaded_file`, `add_uploaded_image`) go through it. Don't go back
  to a plain `history.append({"role": "user", ...})` at any of those sites.
  Two compounding bugs made this hard to see: (1) `main.py`'s
  `/chat/stream` caught the exception and only sent it to the client as a
  silent SSE `data: {"error": ...}` event with no server-side log line —
  since the HTTP response already started (200) before the generator threw,
  the uvicorn terminal showed a clean 200 OK for every request while the
  turn was actually failing; now `traceback.print_exc()`s server-side
  before yielding the error event, don't remove that. (2) The frontend's
  `askBackendStream` treated ANY stream error, including this genuine
  backend one, identically to a real connectivity failure — flipping
  status to "Offline demo" and silently falling back to the canned demo
  reply, plus leaving the "..." typing-dots bubble stuck in the thread.
  Fixed by tagging backend-originated stream errors (`err.fromBackend =
  true`) so `send()`'s catch can tell them apart: a real `fromBackend`
  error now renders the actual error text in the bubble and leaves
  `backendOnline`/the status dot alone, since the backend is clearly up.
  Don't collapse that distinction back into one generic catch — it's what
  makes "the backend really is down" and "the backend is up but this one
  request failed" indistinguishable to the CEO again.
- **The stop button was tried, then explicitly removed by the CEO on
  2026-09-24 — don't re-add it without re-reading this whole account
  first.** Full arc, since each attempt taught something real:
  1. Added a stop button + `AbortController` on the frontend, aborting the
     `fetch()` mid-stream.
  2. That surfaced a real, independent bug: `/chat/stream`'s
     `event_generator` used `finally: yield "data: [DONE]\n\n"`. When a
     client disconnects for ANY reason (this abort, or just closing the
     connection), Starlette closes the still-running generator by
     throwing `GeneratorExit` into it — and a `finally` that yields
     unconditionally yields ONE MORE TIME while being told to shut down,
     violating Python's generator protocol (`RuntimeError: generator
     ignored GeneratorExit`, visible as "Exception ignored while closing
     generator..." in the terminal). Harmless to the actual response but
     a real bug. **This part of the fix is still in place and still
     correct regardless of the stop button's removal** — moved the
     `yield "[DONE]"` to two explicit points (end of the `try`, end of
     the `except`) so a genuine `GeneratorExit` propagates with no further
     yield. Don't revert this specific piece.
  3. Wrongly assumed fixing that ALSO meant plain client-side abort now
     cancelled the server-side request. It didn't: `GeneratorExit` only
     arrives at a generator's own suspension points, and `run_stream` is
     usually blocked deep inside a synchronous network read to Anthropic
     (or the whole time a tool call is running), not suspended at a yield
     at all. Python cannot forcibly interrupt that from another thread.
  4. That wrong assumption caused a real bug: clicking stop, then asking
     another question immediately, hung indefinitely — the orphaned,
     still-running generator from the "stopped" request kept holding
     `orchestrator._lock_for(session_id)` until the abandoned Anthropic
     call finished on its own, blocking the next request with zero
     feedback. Fixed with `_acquire_session_lock` (`orchestrator.py`):
     acquires with a 20s timeout instead of blocking forever, raising a
     clear `RuntimeError` if it can't. **This is also still in place** —
     a real, general safety net against ANY abandoned request (not
     specific to the stop button), don't remove it either.
  5. The CEO correctly pushed back that a 20s wait ending in "⚠️ Error"
     is still bad UX on its own, so real cancellation was built: reading
     the installed `anthropic` SDK's own source confirmed
     `MessageStream.close()` closes the underlying `httpx` connection
     directly, and calling it from a different thread than the one
     blocked reading that stream raises an exception there — genuine
     cross-thread cancellation. Implemented via a new `POST /chat/stop`
     endpoint, an `_active_streams` registry, and `orchestrator.
     request_stop`.
  6. **The CEO tested it and asked for the whole feature to be removed**
     — still not reliable enough, and the overall experience was worse
     than not having a stop button at all (real cancellation only ever
     covered the "stopped while text is streaming" case, never the
     "stopped mid-tool-call" case, which still fell back to step 4's 20s
     wait — inconsistent enough in practice to not be worth keeping).
     Removed: the stop button UI, `AbortController` wiring, `STOP_URL`,
     `POST /chat/stop`, `request_stop`, `_active_streams`,
     `_stop_requested`, and their related comments/docstrings. **Kept**,
     because they're independently correct and don't depend on having a
     stop button at all: the `event_generator` protocol fix (step 2), the
     `_acquire_session_lock` bounded timeout (step 4), and separately the
     chip/input/plus-button disabling during generation (see the
     notification-badge entry above) and the `pause_turn` fix below.
     **If a stop button is ever requested again: re-read this whole
     entry first** — steps 1-3 are exactly the wrong path to retry, and
     step 5's mechanism is real but was judged not reliable/consistent
     enough for this app; a future attempt needs a better answer for the
     mid-tool-call gap specifically, not just a repeat of step 5.
- **A session's history could get permanently poisoned by an orphaned
  `server_tool_use` block, breaking EVERY future message in that
  session with a 400** — found 2026-09-24, right after the stop-button
  bug above, and at first wrongly blamed on the abort race (same
  session, same afternoon, easy to conflate — it isn't related). Real
  cause: `web_search`/`web_fetch` run in a server-side agentic loop on
  Anthropic's OWN infrastructure, and a long-running call can return
  `stop_reason: "pause_turn"` — meaning the turn is NOT finished, even
  though `stop_reason != "tool_use"` too. `run`/`run_stream` used to
  treat anything other than `"tool_use"` as a complete final answer, so
  a paused turn's response (which "can end with a `server_tool_use`
  block that has not run" per Anthropic's own server-tools docs) got
  appended to history and left there — and Anthropic validates the WHOLE
  history on every subsequent call, so every future message in that
  session then failed with `"web_search tool use with id ... was found
  without a corresponding web_search_tool_result block"`. Fixed by
  handling `stop_reason == "pause_turn"` as its own case in both `run`
  and `run_stream`: per Anthropic's docs ("pass the paused response back
  as-is in a subsequent request"), just loop straight back to the API
  with the already-appended paused content as the last history entry —
  no new user turn, no client tool to run. **Don't collapse this back
  into the generic "anything else means done" branch** — that's the
  exact regression that caused the poisoning. A session already poisoned
  by this bug before the fix has no in-app recovery — `_sessions` is
  in-memory only, so restarting the backend is the only way to clear it.
- Ignav's booking-links results are NOT vetted for seller reputation, and
  some are real bad actors — discovered 2026-09-23 when the CEO was handed
  a link to martigo.*, which has widely reported bait pricing (quotes low,
  demands more after booking) and, via a partner, unauthorized charges.
  `flight_tool.py` now filters every result through `TRUSTED_OTA_DOMAINS`
  (an allowlist, matched against the link's actual URL domain — NOT its
  display name, see next point) before it's ever shown — airline-direct is
  always trusted, third-party OTAs only if their domain is allowlisted.
  **Don't remove or bypass this filter to "show more options" or "let the
  CEO decide"** — a bait-priced reseller will usually show up as the
  cheapest option specifically because bait pricing undercuts everyone, so
  an unfiltered cheapest-first sort actively promotes exactly the sellers
  this filter exists to block.
- Ignav's `market` param controls BOTH currency and display-text language —
  `market: "AE"` gives AED but also localizes provider_name/carrier to
  Arabic script (confirmed empirically; no separate locale/lang param
  exists, tried and got 400s). Since AED has been hard-pegged to USD at
  exactly 3.6725 since 1997, `flight_tool.py` instead keeps the English
  default market and converts USD→AED itself (`_to_aed`) — same real price,
  English display. Don't switch back to `market: "AE"` to "let Ignav do the
  conversion" — that reintroduces the Arabic-text side effect.
- Ignav's `/booking-links` 404s for any `ignav_id` that's invalid OR expired
  (itineraries are only bookable for a limited window after search — hit
  this for real 2026-09-23, minutes after a search). `get_booking_link`
  catches the 404 specifically and returns the normal empty-options shape
  (`raw_count=0`) instead of letting `requests`' `HTTPError` propagate —
  before this fix, `main.py`'s generic `except Exception: str(e)` handler
  leaked the raw `"404 Client Error: Not Found for url: ..."` straight to
  the CEO, which is meaningless to him. This also means search_links still
  get attached even on an expired fare, since those don't depend on Ignav's
  response at all. Don't remove the 404 special-case "to simplify" — it's
  what keeps a routine expiry from looking like a crash.
- Airline booking links from Ignav are frequently rejected by the airline's
  own bot-defense (seen with Emirates, Qatar Airways — an Akamai-style
  "restricted"/"something happened" page) when opened directly outside the
  ad-click chain some of these links were generated for. `get_booking_link`
  strips known tracking params (gclid etc.) and always attaches a
  `homepage_url` fallback (derived from the link's own domain, not a
  hardcoded table) when it differs from the deep link — the frontend shows
  both and tells the CEO to search manually on the homepage if the direct
  link is rejected. This mitigates, it doesn't fix — the airline's bot
  defense is out of our control.
- Prompt-injection defense for booking is layered, not one thing: (1) no
  write/action tools at all is the base defense — nothing destructive
  exists for injected content to invoke; (2) `get_booking_link` is
  structurally unreachable from the LLM (see above); (3) `orchestrator.py`
  tracks the real origin/destination/dates behind every `ignav_id` a
  session's `search_flights` calls actually returned (`_known_itineraries`),
  and `main.py`'s `/booking-link` endpoint refuses any id not in that map —
  so even if injected content talked the model into emitting a fabricated
  ```agmc-booking block, it cannot resolve to a real external call; (4) the
  frontend cross-checks the *proposed* price (model-generated display text)
  against the price actually returned and shows a warning banner on a
  mismatch, since the proposal card's display fields aren't otherwise
  verified. Residual risk: a mismatched *proposal* can still render (a card
  is just UI text) — the warning in (4) is the mitigation, not a
  prevention, for that specific case.
- Booking-site links are two genuinely different things, don't conflate
  them: (a) `options` — Ignav's actual priced links for the exact quoted
  fare, trust-filtered, but the deep link can get bot-blocked (see above);
  (b) `search_links` — plain search-page URLs we construct ourselves
  (Google Flights / Skyscanner / Kayak) for the same route/dates, built in
  `flight_tool._generated_search_links`. These are NOT ad-click-session
  redirects, so they don't hit the same airline bot-defense wall — verified
  directly: Google Flights and Kayak load cleanly, Skyscanner's URL parses
  correctly server-side (confirmed via its own captcha redirect target) but
  served a captcha to our automated test browser specifically, which may or
  may not happen for a real human browser. Trade-off: these show live
  results for that route/date, NOT a price match guarantee against what
  Ignav quoted — label them as "search", never mix them into the priced,
  sorted `options` list. **Do not** try to make the blocked airline deep
  links "work" by spoofing referrers/headers/session tokens to imitate a
  real ad-click — that's evading the airline's anti-bot/anti-fraud defense,
  not fixing a bug, and this project won't do that even for a legitimate
  booking flow.
- **Headless-browser scraping for hotels/social media was tried and
  abandoned (2026-09-23)** — CEO explicitly asked for it, accepting the
  known ToS/account-risk tradeoff, so this was a real attempt, not a
  refusal on principle. Result: a stock Playwright/Chromium hitting
  Booking.com's real search results page never got past a client-side
  "Loading…" holding screen — `page.url` even had the query string
  stripped, and `wait_until="networkidle"` never resolved (30s timeout) —
  classic bot-challenge behavior, the same category already documented
  above for Skyscanner and airline deep links, just hitting the wall
  earlier (at page load, not at link-click). The very next debugging step
  — setting a real-browser `user_agent` string to mask the headless
  fingerprint — was blocked by the platform's own safety classifier as
  "security weaken." That's the correct call: a spoofed UA is genuine
  bot-defense evasion, not a bug fix, exactly the line already drawn for
  airlines above. Social media (Instagram/Facebook) wasn't even attempted
  after this — their anti-automation is well known to be stronger than
  Booking.com's, and there was no reason to expect a better outcome.
  Uninstalled playwright/pyee/greenlet and the downloaded Chromium binary
  afterward — nothing about this capability is half-built or waiting to be
  finished, it was tried for real and didn't work. **Don't re-attempt this
  without a fundamentally different approach that doesn't rely on
  disguising automated traffic as human** (e.g., a real, ToS-compliant
  partner API) — retrying with the same headless-browser approach will hit
  the same wall.
- The Google Flights link in `search_links` is no longer just a plain
  route/date search — `flight_tool._google_flights_tfs_url` builds Google's
  reverse-engineered `tfs=` protobuf param (spec:
  https://gist.github.com/MomoDeve/a18053dea84dd28e320b8b2c489540eb) to
  filter by airline + a ±1-hour departure window + cabin, narrowing results
  down to the CEO's exact flight or a very short list next to it. Verified
  live 2026-09-23: a real itinerary (Ethiopian, 1 stop, dep 17:xx) narrowed
  to exactly one result. This is NOT official — Google has never published
  this format, and it can change without notice; every builder function is
  wrapped in try/except so a broken encoding falls back to the plain `q=`
  search rather than a malformed URL, and if narrowed links stop narrowing
  in testing, re-check the gist before assuming the code regressed. The
  ±1-hour window (not exact-hour) is deliberate: Ignav and Google are
  different data snapshots that won't always agree to the minute — an
  exact-hour window reproduced a real "no options matching your filters"
  false negative in testing. Google Flights has no field for selecting one
  literal flight (confirmed against the spec) — this narrows, it doesn't
  guarantee a single result, and it still doesn't verify Ignav's quoted
  price against what Google shows.
- Kayak also gets the same airline/stops/departure-time narrowing as Google
  Flights now — `flight_tool._kayak_fs_param` builds Kayak's own (also
  unofficial) `fs=` filter query param. Reverse-engineered by applying
  filters through Kayak's own UI and reading back the resulting URL, same
  method as the Google one. Verified live 2026-09-23 on the same DXB→JFK
  case: `airlines=EK,VS` (comma-separated IATA codes) + `stops=~1` +
  `takeoff=0800,1000` (24h HHMM, outbound leg) narrowed 7 results down to
  exactly 1. Same caveats as Google's: unofficial and can change (wrapped
  in try/except, falls back to the plain route/date URL on failure), and
  `takeoff=` is only applied to the outbound leg — never verified a
  reliable inbound-leg time syntax, don't guess one in without testing
  live first. **Don't confuse `airlines=EK,VS` (include only) with
  `airlines=-EK,VS` (exclude these)** — the leading `-` flips the meaning
  entirely and was hit by accident while reverse-engineering this; a stray
  `-` slipping into the carrier list would silently invert the filter.
- **Skyscanner is gone from `search_links` — replaced with Momondo.**
  Skyscanner's own bot-defense served a persistent "confirm you're not a
  robot" challenge that the CEO could never get past (2026-09-23) — this
  was suspected earlier (see the old ambient-risk note about our automated
  test browser hitting a captcha) but is now confirmed for a real human
  user too, so it's a dead link for this project, not a testing artifact.
  Momondo is the replacement: same platform as Kayak (both Booking
  Holdings), takes the identical `fs=` filter syntax (`_kayak_fs_param` is
  reused for both URLs), already on `TRUSTED_OTA_DOMAINS`, and loaded clean
  with no challenge in the same live test. `_yymmdd` (Skyscanner's
  date-format helper) was removed as dead code along with it. Don't add
  Skyscanner back without re-verifying live that the captcha wall is gone
  — a URL that *parses* correctly server-side isn't the same as one a
  human can actually get through.
- **Booking.com was tried and deliberately left out of flight `search_links`
  (2026-09-23)** — its route/date URL params work fine (`from=DXB.AIRPORT&
  to=JFK.AIRPORT&depart=...&type=ONEWAY|ROUNDTRIP&return=...`, confirmed
  live, real AED prices, no captcha), but its airline/stops/time filters
  are pure client-side UI state — applying one through the live UI never
  changed `window.location.href` at all, confirmed directly. The CEO's bar
  for a flight search_links entry is that it can actually narrow to the
  specific flight, not just show live prices for the route — a link that
  can't do that isn't worth the extra row. Don't re-add it without first
  finding a real filter mechanism (their internal API, not a URL param
  guess) — otherwise it stays out.
- **Multi-carrier connections are common and were a real bug, not an edge
  case** — discovered 2026-09-23 testing DXB→JFK, where the CHEAPEST result
  was Emirates DXB→LHR then Virgin Atlantic LHR→JFK on one fare (an
  interline/codeshare itinerary). `orchestrator._leg_filter_hint` originally
  only read the first segment's carrier, so the Google Flights filter said
  "Emirates only" for an itinerary that also needed Virgin Atlantic — this
  produced a hard "No options matching your filters" (zero results), not
  just an imprecise link. Fixed: the hint now carries `carriers` — every
  distinct carrier across ALL of a leg's segments, order-preserving,
  deduplicated — and `_pb_leg` emits one `include_airlines` entry per
  carrier (verified this is "any of these", not "all of these": the fix
  took the same case from 0 results to 2, with the first exactly matching).
  **Don't revert this to a single `carrier` string** "to simplify" — that's
  the exact regression that caused the zero-result bug. If a future edit
  touches leg/segment handling, check whether it still gathers carriers
  from every segment, not just the first.
- **A tool's own `description` field can silently override the system
  prompt's more specific instructions about when to use it** — found
  2026-09-24: the CEO kept getting the same two hardcoded mock posts for
  ANY social-media question, even though `orchestrator.SYSTEM_PROMPT`
  correctly said to use a real site-restricted `web_search` for
  Instagram/Facebook. Root cause: that instruction only applied "when
  asked about Instagram or Facebook... specifically", and the registered
  tool at the time, `get_social_mentions`, had a broad description —
  *"Fetch recent Instagram and X mentions of AGMC. Use this for questions
  about social media activity, mentions, or sentiment"* — that matched
  ANY generic social question (e.g. the "Social mentions" shortcut chip's
  own canned query, "Any notable social mentions today?", never says
  Instagram or Facebook by name). The model reached for that tool instead,
  which had no live Meta/X credentials, so it silently fell back to mock
  data every time — indistinguishable from a real empty result unless you
  already knew to suspect it. Fixed two ways at once: (1) split the tool
  into `get_x_mentions` only, with an honest, narrow description that
  explicitly says it does NOT cover Instagram/Facebook and must be paired
  with `web_search`; (2) rewrote the system prompt so covering Instagram
  and Facebook via `web_search` is mandatory for ANY social-media
  question, generic or specific — not conditioned on the CEO happening to
  name the platform. **Lesson for future tools:** a tool's own
  `description` competes with the system prompt for the model's attention,
  and a broad description usually wins over a narrower system-prompt
  carve-out — if a tool should NOT be the default answer for a whole
  category of question, say so explicitly in its own description, don't
  rely on the system prompt alone to narrow it.
- Ignav's booking-links can legitimately return zero *trusted* options
  while still returning real sellers — seen with the same DXB→JFK case:
  Ignav found 3 sellers (EaseMyTrip, Smartfares, Kissandfly), all routed
  through a "Wego" metasearch affiliate (`wego_click_id` in the URLs), none
  on `TRUSTED_OTA_DOMAINS`. That's the filter working as intended, not a
  bug — these are exactly the kind of obscure third-party resellers the
  Martigo incident was about. Don't treat "raw_count > 0 but options == []"
  as a sign something's broken; it's the safety filter doing its job.
- **A "huge" price gap between the quoted fare and Google Flights doesn't
  necessarily mean either number is wrong** — reproduced 2026-09-23 on the
  same DXB→JFK EK3/VS25 interline case: Ignav quoted AED 2,361.42, the CEO
  saw "300" on Google Flights and assumed a mismatch/bug. Opened the exact
  generated link and confirmed Google shows **"Price unavailable"** for all
  3 matching results (verified flight numbers/times/routing are identical
  to the card — EK3 DXB→LHR, VS25 LHR→JFK) — Google's own fare engine
  can't price this specific airline-direct interline combo at all in the
  results list; you have to click "Select flight" and go through to an
  actual booking flow to get a real number from Google's side. So the "300"
  the CEO saw was for a **different, unrelated flight** that happened to be
  on an unfiltered search, not a mispriced version of this one. Before
  treating a search_links price gap as a bug: open the actual generated
  link (not a fresh manual search) and check whether the matching result
  even has a price at all — "Price unavailable" on an interline/codeshare
  fare is expected, not a defect, and makes that link useless as a price
  sanity-check for this class of fare specifically.
- **The model would sometimes show the raw `ignav_id` in the visible
  flight list** — found 2026-09-24, direct CEO feedback ("makes no sense
  to do that, the CEO is just interested in the flights"). The system
  prompt told the model `search_flights` results each carry an
  `ignav_id` and to keep it in mind for a later `agmc-booking` block, but
  never said the ID itself should stay invisible — nothing stopped it
  from also mentioning it inline while listing fares. Fixed by adding an
  explicit "never mention/print it in the visible list" instruction,
  right next to where the prompt first introduces `ignav_id`. Worth
  noting the `agmc-booking` block itself was never the leak — the
  frontend already strips that fenced block out of visible text and
  renders it as a real approval card, so this was specifically about the
  plain conversational list before any booking was ever discussed.
- **AGMC does NOT sell Mercedes-Benz — BMW only.** Confirmed directly by
  the CEO 2026-09-24, correcting a wrong assumption this project had from
  its very first day (this doc's own opening line, and
  `orchestrator.SYSTEM_PROMPT`, both used to say "BMW and Mercedes-Benz").
  Surfaced when a `web_fetch`-driven "current offers" query for AGMC
  Mercedes-Benz correctly found no such thing and asked for clarification
  rather than fabricating an answer — real search results consistently
  pointed at Gargash Enterprises as the actual Mercedes-Benz distributor
  in Dubai, not AGMC. Fixed everywhere the wrong assumption had spread:
  this doc's opening line, `SYSTEM_PROMPT`, the frontend's "Schedule a
  meeting" demo chip/canned response (was "Mercedes regional
  distributor"), and `tools/social_tool.py`'s mock Instagram post (was
  tagged `#Mercedes`). Also deleted `tools/offers_tool.py` and
  `data/offers.json` entirely — a fully dead, never-imported mock-offers
  tool from an earlier iteration of this project, whose mock data was
  ALSO wrong in the same way (listed Mercedes-Benz E-Class/GLE offers
  alongside real BMW ones). **Don't reintroduce "Mercedes" anywhere in
  this project without the CEO explicitly saying AGMC's brand lineup has
  changed** — if AGMC/BMW/MINI/Rolls-Royce Dubai brand coverage is ever
  genuinely unclear again, ask rather than assume either way.
  - **Follow-up, found 2026-09-25: the fix itself over-corrected, TWICE,
    before landing on the right answer.** Attempt 1 put "NOT
    Mercedes-Benz, confirmed by the CEO..." directly in the model's own
    self-description — it started proactively "confirming" that
    distinction as an unprompted preamble on totally unrelated questions
    (e.g. "what are we selling on AGMC right now" got an unprompted
    "Confirmed — AGMC is BMW, not Mercedes-Benz" opener), confusing since
    the CEO never asked about Mercedes at all. Attempt 2 kept the explicit
    "NOT Mercedes-Benz" wording but added an instruction not to volunteer
    it unprompted — still the wrong shape of fix, per the CEO's own sharp
    catch: naming one specific excluded competitor (why Mercedes and not
    Ferrari, Toyota, anything else?) was never necessary in the first
    place, and a word sitting in the model's own self-description can
    surface even with a "don't mention this" instruction attached, since
    suppressing a salient detail is less reliable than never stating it.
    **Actual, final fix:** `SYSTEM_PROMPT` now says only "a BMW dealership
    group in Dubai" — no Mercedes-Benz mention anywhere, no other brand
    named either. Stating the positive fact alone already excludes every
    other brand automatically; there is nothing to leak because the word
    isn't in the prompt at all. **If a future correction like this comes
    up again (a wrong assumption about what AGMC does or doesn't sell/do),
    default to fixing the positive statement only — resist the urge to
    also spell out the negative/excluded case "for clarity." That instinct
    is what caused two extra rounds of fixing here.**
- **The "use web_fetch for anything current" instruction in
  `SYSTEM_PROMPT` was originally scoped too narrowly** — added 2026-09-24
  worded around "current offers, current pricing, what's on a dealer or
  brand page," i.e. AGMC's own business content specifically. Found
  2026-09-25: asked for today's movie showtimes in the city, got a plain
  `web_search` answer with times that were "almost right, a lot wrong" —
  the narrow wording never triggered the search-then-fetch pattern for a
  non-AGMC, general time-sensitive question, so it silently fell back to
  search snippets (same root problem as the BMW-offers case, just outside
  the fix's original scope). Broadened the instruction to cover ANY
  current/live/time-sensitive question generically (showtimes, event
  schedules, "what's on today," not just AGMC brand pages) rather than
  re-narrowing it to "and also movies" — the next thing like this
  shouldn't need its own special-cased fix. Real, separate limitation
  layered on top, not fixed by the broadening: many cinema/ticketing sites
  render their actual showtime schedule via JavaScript, which `web_fetch`
  cannot see at all (confirmed via Anthropic's own docs) — so showtimes
  specifically may still come back stale/incomplete even now; verify
  live rather than assuming the broadened instruction fully solved it.

- **The assistant can launch the desktop Outlook app directly — added
  2026-09-25, direct CEO request** ("when I ask about email, open Outlook
  for me... it should open it directly"). `email_tool.open_outlook()`
  calls `os.startfile("outlook")` — deliberately NOT a hardcoded path to
  `OUTLOOK.EXE`, since Windows resolves the bare name "outlook" via the
  App Paths registry key
  (`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE`),
  the same mechanism Win+R "outlook" and `start outlook` use — confirmed
  live against this machine's real install
  (`C:\Program Files\Microsoft Office\Root\Office16\OUTLOOK.EXE`,
  Office16/click-to-run) before writing the fix, not assumed. This works
  from the BACKEND, not Electron/`main.js` — a real realization while
  scoping this: the backend is a normal local Python process on the
  CEO's own machine (not a remote/cloud service), so it already has full
  OS access regardless of which frontend (the Electron desktop app or a
  plain browser tab) sent the request. No IPC/`preload.js` changes were
  needed at all.
  - **Registered as a normal, DIRECT `orchestrator.TOOLS`/`TOOL_DISPATCH`
    entry — no approval-card gate**, unlike `send_email`/`create_event`/
    flight booking. This is a deliberate, narrow second exception to the
    read-only design (see the top-of-file note and `tools/email_tool.py`'s
    own docstring for the full reasoning): `open_outlook` takes NO
    arguments, so unlike `get_booking_link`'s `ignav_id` or `send_email`'s
    to/subject/body, there's no field for injected content to substitute
    or manipulate — the only possible effect is a window opening, which is
    trivially closed if unwanted. It doesn't fit the "could leak, lose, or
    spend something real" category the approval-gate pattern exists for.
    The CEO also explicitly asked for immediate/direct behavior, having
    been asked first whether it should open directly or show a
    confirm-first link (matching the flight-booking pattern) — don't
    quietly add an approval step here "for consistency" without that
    being a real, separate ask.
  - The system prompt tells the model to call `open_outlook` ALONGSIDE
    `read_emails`, not instead of it, whenever the CEO asks about email/
    inbox/messages — one opens the real app, the other gives an
    in-chat summary; both fire from the same question. Verified live: a
    real `/chat` call with "what's in my inbox?" opened a real, visible
    Outlook window (`Get-Process outlook` showed it running with the
    real mailbox's inbox title) AND returned the usual mock-data inbox
    summary in the same reply.
  - `TOOL_STATUS_LABELS` in `agmc-assistant-popup.html` got a matching
    `open_outlook: 'Opening Outlook…'` entry — if this is ever removed,
    the status indicator just falls back to a generic "Working on it…"
    label, not broken, just less specific.
  - This is Windows-only (`os.startfile` doesn't exist on macOS/Linux) —
    fine for now since this whole project already assumes Windows (see
    the OCR gotcha's admin-rights note, the `.ico`/Electron packaging,
    etc.), but flag it if this project is ever ported to another OS.

- **Real inbox reading now goes through an actual MCP server — added
  2026-09-25, at the CEO's boss's explicit insistence on "an MCP"
  specifically, not because the project needed one technically.** Worth
  being candid about, since it was said directly during scoping: the
  earlier plain Graph-API tool call (`email_tool.get_recent_emails`,
  app-only auth) would have worked exactly as well from a pure
  functionality standpoint. This was built anyway, as a real, correct MCP
  integration rather than a token gesture — see the two scoping questions
  asked and answered before writing any code: (1) the server plugs
  directly into AGMC Assist's own orchestrator, not a standalone server
  for external clients like Claude Desktop; (2) auth is DELEGATED (the
  CEO signs in as himself), not app-only, specifically to sidestep the
  same pending-IT-admin-consent blocker documented below for
  `MS_CLIENT_ID`.
  - **New files, all under `backend/`:**
    `mcp_servers/outlook_server.py` (the actual MCP server — `mcp`
    package's `MCPServer`, stdio transport, one tool:
    `list_recent_emails`), `mcp_servers/outlook_auth.py` (shared MSAL
    delegated-auth helper), `mcp_servers/outlook_login.py` (a standalone
    script for the one-time interactive sign-in — see below for why this
    can't happen inside the server itself), and `mcp_outlook_client.py`
    (the orchestrator-facing client that spawns the server as a
    subprocess and calls it over the real protocol).
  - **Verified against the actually-installed package before writing any
    server/client code** (`mcp` 2.2.0, `pip install "mcp[cli]"`) — the
    web docs found for this were inconsistent about the exact current API
    (some show the older `mcp.server.fastmcp.FastMCP` class, others the
    newer `mcp.server.MCPServer`; some show the low-level `ClientSession`/
    `stdio_client` client, others the newer high-level `mcp.Client`).
    Confirmed via direct introspection (`inspect.signature`,
    `Tool.model_fields`, etc.) against the real installed package rather
    than trusting either source blindly: `from mcp.server import
    MCPServer`, `@mcp.tool()` decorator inferring the schema from type
    hints/docstring (matches `Tool.input_schema` — snake_case, and
    already 1:1 compatible with Anthropic's own `input_schema` key, no
    conversion needed), and `from mcp import Client, StdioServerParameters`
    on the client side. **If a future `mcp` package upgrade changes these
    names, don't guess from search results — introspect the real
    installed version first, exactly like this was verified.**
  - **`result.structured_content` came back `None` in real testing, even
    for a tool that returns a plain dict with a return-type annotation**
    — confirmed with a throwaway smoke-test server/client pair before
    writing the real Outlook server, not assumed from docs. The actual,
    reliable content is `result.content[0].text`, a JSON-serialized
    string of the tool's return value — `mcp_outlook_client.py` parses
    that directly with `json.loads(...)`. If `structured_content` starts
    working in a future SDK version, this could be simplified, but don't
    switch to it without re-verifying live first.
  - **Why the interactive device-code sign-in is a SEPARATE script
    (`outlook_login.py`), never done inline inside the server**: a stdio
    MCP server's stdout IS the JSON-RPC protocol channel to its client —
    printing a plain-text "visit this URL and enter this code" prompt
    there would corrupt every protocol message the server sends from that
    point on. `outlook_auth.get_cached_token()` (used by the server, on
    every real call) is silent-only and returns `None` if no
    cached/refreshable sign-in exists — the server then returns the same
    mock-data shape `email_tool._mock_emails()` does, with a clear `note`
    explaining the CEO needs to run `outlook_login.py` once. That
    interactive script is meant to be run manually, once, from a real
    terminal — never spawned by the app itself.
  - **Azure Portal setup needed for this — DONE for a personal test
    account 2026-09-25, NOT yet done for the real AGMC work account.**
    The steps (same either way): register an app in Entra ID (a NEW one —
    don't assume the existing app-only `MS_CLIENT_ID` app, if one gets
    created later, already qualifies); under Authentication, enable
    "Allow public client flows" = Yes (device-code flow needs this — no
    redirect URI needed); under API permissions, add Microsoft Graph
    **delegated** `Mail.Read` (distinct from the **application** `Mail.Read`
    the app-only path uses — Entra ID tracks these as separate grants even
    on the same app). For the personal-account test, "Supported account
    types" was set to allow personal Microsoft accounts, no admin-consent
    prompt appeared at all (personal accounts have no admin layer — the
    signed-in user IS the consent authority for their own data). **Whether
    a real AGMC work-account version of this needs IT admin approval is
    still genuinely unknown** — the tenant's user-consent policy determines
    it, and this hasn't been tested against the real work tenant yet;
    running `outlook_login.py` against it will surface this directly
    (either a clean consent prompt, or an explicit "admin approval
    required" error naming the exact permission).
  - **MVP tradeoff, deliberate**: `mcp_outlook_client.py` spawns a fresh
    Outlook MCP server subprocess on every single call via
    `asyncio.run()`, rather than keeping one persistent connection open.
    Simple and correct, at the cost of real per-call latency (Python
    subprocess startup, a few hundred ms) — acceptable for a low-frequency
    personal assistant, not for anything high-throughput. If this needs
    to be faster later, the fix is a persistent `Client` held open on a
    background thread with its own event loop — don't rewrite this file's
    public interface (`list_recent_emails(limit)`) to do it, wrap it
    underneath instead.
  - `TOOL_DISPATCH["read_emails"]` now routes through
    `mcp_outlook_client.list_recent_emails` (`orchestrator._read_emails`),
    falling back to the OLD direct `email_tool.get_recent_emails` path
    only if the MCP call itself raises (subprocess/transport failure —
    NOT the normal "not signed in yet" case, which the server already
    returns as ordinary mock data, no exception). Verified live end to
    end via a real `/chat` call ("what's in my inbox?"): the model called
    both `open_outlook` (a real Outlook window opened) and `read_emails`
    (returned mock data + the sign-in note through the full MCP hop), and
    correctly told the CEO in its reply that this was mock data, not his
    real inbox — it did not present mock data as if it were real.
  - `email_tool.get_recent_emails`/`_mock_emails` (the original app-only
    Graph path) are UNCHANGED and still exist, used only as the
    exception-path fallback above — don't remove them thinking they're
    now dead code.
  - **Verified live end-to-end against a REAL personal Microsoft account
    2026-09-25** (not just the mock fallback path) — two real bugs found
    and fixed getting there, both worth knowing before touching this again:
    1. **`outlook_login.py`'s device-code message didn't appear for
       several seconds** when run via a background/non-terminal process —
       Python buffers stdout when it isn't a real TTY, so `print(flow
       ["message"])` sat in a buffer instead of appearing immediately.
       Run it with `python -u` (or `PYTHONUNBUFFERED=1`) whenever it's
       launched from something other than a real interactive terminal.
    2. **The real, substantive bug**: with `MS_DELEGATED_TENANT_ID` set to
       the app registration's own tenant GUID, every Graph call to
       `/me/mailFolders/...` returned a bare `401` with a completely EMPTY
       body (no JSON error, `Content-Length: 0`) — even though the access
       token itself was valid (confirmed by decoding its claims: correct
       `aud` for Graph, correct `scp: Mail.Read`, correct `appid`). Root
       cause: a tenant-GUID authority tells Graph to look for the signed-in
       user INSIDE that specific org tenant — a personal Microsoft account
       doesn't exist inside any org tenant, so the mailbox lookup silently
       fails with no detail. Fixed by giving delegated auth its OWN env var,
       `MS_DELEGATED_TENANT_ID` (NOT a reuse of `MS_TENANT_ID`, which stays
       for the app-only path) — set to the literal word `consumers` for a
       personal account, or a real tenant GUID for a real work account.
       **These two cases are not interchangeable — don't collapse this
       back into one shared tenant variable.** If real inbox reads for the
       actual AGMC work account are set up later, that means: a NEW app
       registration (or the same one, reconfigured) under the real work
       tenant, `MS_DELEGATED_TENANT_ID` set to that tenant's real GUID
       (not `consumers`), and a fresh `outlook_login.py` run signed in
       with the work account — the personal-account cache/config doesn't
       carry over.
    Live-verified after the fix: a real `/chat` call ("what's in my
    inbox?") returned the CEO's (well, this personal test account's) actual
    unread emails — security notices, promo emails, LinkedIn job alerts —
    correctly summarized, with `"mock": false` all the way through the
    stack. This is genuinely reading real email now, not the mock fallback.
  - **Tried getting the REAL AGMC work account onto this same path, hit a
    tenant-level wall, worked around it with a completely different
    mechanism** — the CEO doesn't have Entra ID portal access himself, so
    a brand-new app registration would need IT. Before asking for that,
    tried Microsoft's own pre-registered, no-registration-needed public
    client — "Microsoft Graph Command Line Tools"
    (`14d82eec-204b-4c2f-b7e8-296a70dab67e`, multi-tenant, works against
    any org via `MS_DELEGATED_TENANT_ID=organizations` instead of a
    specific tenant GUID). AGMC's tenant (Albatha Group) has admin
    consent locked down even for this well-known Microsoft app — signing
    in showed a real "Approval required" screen (Read user mail /
    Maintain access / View basic profile), not a clean consent prompt, so
    this still needs an admin somewhere to approve it. That request was
    submitted but hadn't come back at time of writing. **Don't read "we
    tried the well-known client ID and it needed approval too" as proof
    that NOTHING will work without IT** — it only proves user self-consent
    is disabled tenant-wide; an actual admin approval (of either this
    request, or a dedicated new app registration) would still unblock the
    MCP/Graph path the normal way.
  - **Built a second, genuinely different tool while that approval is
    pending: `tools/outlook_com_tool.py`, using Outlook desktop COM
    automation (`win32com`), not Graph at all.** This is not a workaround
    of the admin-consent wall — it never asks Microsoft's cloud for
    anything, so no consent of any kind applies. It automates the
    already-open, already-signed-in Outlook.exe process directly (the
    same mechanism Outlook VBA macros/COM add-ins have used for decades),
    reading `GetDefaultFolder(6)` (inbox) through Outlook's own session.
    Requires Outlook desktop to actually be open and signed in — pairs
    with `open_outlook`, which the system prompt already calls alongside
    `read_emails` for any email question. `pywin32` was already present
    (a transitive dependency of `mcp[cli]`, confirmed via `pip show mcp`),
    no new install needed.
    - **Real bug found going from a standalone test to the live backend**:
      a direct script-level test of `read_inbox()` worked fine, but the
      exact same call through a real `/chat` request silently fell back
      to mock data with a "hiccup" note. Root cause: COM requires the
      CALLING THREAD to call `CoInitialize()` before touching any COM
      object, and FastAPI runs sync endpoints (`def chat(...)`) in a
      threadpool worker thread, which had never done that — a standalone
      script's main thread doesn't hit this, which is exactly why it was
      invisible until tested through the real request path. Fixed by
      wrapping the whole function body in
      `pythoncom.CoInitialize()`/`CoUninitialize()` (confirmed the fix
      specifically by reproducing the failure in a plain background
      `threading.Thread` first, matching FastAPI's execution model,
      before re-testing through the real endpoint). **Don't remove this
      pairing thinking it's unnecessary boilerplate** — without it, this
      tool works in isolated testing and silently fails (falls back to
      mock, not a crash) the moment it's actually used for real.
    - **Known, minor, NOT-yet-fixed cosmetic gap**: `MailItem.
      SenderEmailAddress` returns a legacy Exchange X.500 DN string (e.g.
      `/O=EXCHANGELABS/OU=.../CN=...`) instead of a real email address for
      some internal senders — `_clean_sender_address()` resolves this via
      `Sender.GetExchangeUser().PrimarySmtpAddress` when possible, but
      that call itself returns `None` for shared/distribution mailboxes
      (confirmed live: a "PMO SUPPORT" shared-mailbox sender still shows
      the raw DN). Falls back to the raw DN string in that case rather
      than crashing — ugly but not wrong, and not chased further since
      it's cosmetic, not a defect in the actual data.
  - **`orchestrator._read_emails` currently calls `outlook_com_tool.
    read_inbox` as the ACTIVE path — the MCP/Graph call is commented out
    directly above it, NOT deleted**, on explicit instruction ("dont
    remove the mcp shit... comment it so we can come back to it later").
    `mcp_outlook_client` stays imported (marked `# noqa: F401`) for the
    same reason. **Once the admin-approval request above comes back
    successful** (or a dedicated work-tenant app registration gets set
    up): swap the two blocks back — MCP/Graph becomes primary again, COM
    becomes the fallback-of-the-fallback. This isn't because COM is
    broken; it's because the MCP integration is what was actually asked
    for at the start of all this — COM was always the practical stopgap,
    not the intended end state. Don't delete `mcp_servers/`,
    `mcp_outlook_client.py`, or this history when that swap happens.
  - **`outlook_com_tool.send_mail` — real sending via the same COM
    mechanism, added 2026-09-25, direct CEO follow-up ("can it send mails
    as well?").** Deliberately NOT a registered LLM tool and never will
    be — same isolation as the existing Graph-based `email_tool.
    send_email` (see that module's docstring): the model can only emit a
    fenced ` ```agmc-send-email ` proposal block, the frontend renders a
    real approval card, and only main.py's `/send-email` endpoint (a
    human clicking Confirm) ever calls this. **COM makes sending
    mechanically trivial — that's exactly why the approval-card gate is
    what's actually doing the safety work here, not any difficulty
    reaching the function.** `/send-email` tries this COM path first,
    falling back to the old Graph/mock `email_tool.send_email` path only
    if COM itself raises — same pattern as `orchestrator._read_emails` on
    the read side.
    - Verified with REAL sends, explicitly confirmed with the CEO before
      each one (to his own address only, clearly marked as a test) — not
      just a dry-run/mock check. `pythoncom.CoInitialize()`/
      `CoUninitialize()` pairing (same as `read_inbox`) confirmed
      necessary here too, verified via the identical background-thread
      test technique used for the read path.
    - **The long false-debugging-trail this uncovered**: repeated test
      sends kept coming back as mock/Graph-path results instead of real
      COM sends, even right after "restarting" the backend — this was
      NEVER a bug in `send_mail` or in `main.py`'s wiring (both were
      correct from the first edit). It was the orphaned
      `--multiprocessing-fork` worker issue documented in "Stack &
      running it" above: every `Stop-Process` aimed at "the uvicorn
      process" was killing only the RELOADER, never the actual worker
      serving traffic, so every single test that "used the old code path"
      was silently hitting a worker process that had been running since
      BEFORE the send-mail code even existed. Confirmed definitively by
      testing the identical code on a throwaway port (8001, no old
      process squatting on it) — worked on the very first try — then
      fixing the real port 8000 by killing every `python.exe` process
      system-wide rather than pattern-matching on `uvicorn`. **If a
      future backend change seems to "not take" despite a restart, check
      for orphaned `--multiprocessing-fork` processes before assuming the
      code itself is wrong** — this is now a known, real trap for this
      specific project's dev loop, not a one-off.

- **Calendar reading and meeting creation got the same COM treatment as
  email, added 2026-09-25, same day, direct CEO follow-up ("can it
  schedule meetings as well?")** — `outlook_com_tool.read_calendar`/
  `create_event`, wired in exactly like the email pair: `orchestrator.
  _read_calendar` tries COM first (falling back to the old
  `calendar_tool.get_upcoming_events` Graph/mock path only on exception),
  and `main.py`'s `/calendar-event` endpoint tries `outlook_com_tool.
  create_event` first, same fallback pattern as `/send-email`.
  `create_event` is NOT a registered LLM tool, same isolation as
  `send_mail` — only reachable via a real approval-card Confirm click.
  **If attendees are given, this actually sends them a real meeting
  invite** (`MeetingStatus = olMeeting` + `.Send()`) — exactly as
  consequential as `send_mail`, which is why it has the identical gate.
  Verified live with a REAL created event (no attendees — a personal
  calendar block only, deliberately not tested with real attendees yet,
  since that would send actual invites to actual people; ask before that
  specific scenario if it comes up).
  - **Two real bugs found live, not just theorized:**
    1. `Items.Restrict()` with a date-range string built via `strftime`
       (the documented locale-style format for Outlook's restrict DSL)
       silently failed to match a real, confirmed-to-exist event — an
       unfiltered scan of the same calendar folder proved the event was
       genuinely there with the right time; `Restrict()` just wasn't
       matching it. `Restrict()` combined with `IncludeRecurrences=True`
       is a known-flaky pairing in Outlook's object model. Fixed by
       dropping `Restrict()` entirely and filtering in plain Python
       instead — comparing `item.Start` (pywin32 returns this
       timezone-aware, confirmed live) directly against Python
       `datetime.now(timezone.utc)` — simpler and reliably correct. Don't
       reintroduce `Restrict()` for date filtering here without
       re-verifying live first.
    2. **A real company-wide meeting on this calendar had 300+ required
       attendees** (a town hall spanning half a dozen Albatha group
       companies) — dumping all of them into the tool result would waste
       real context for zero benefit. `MAX_ATTENDEES_SHOWN = 15` caps the
       list with a plain `"+N more"` entry, same pattern as this
       codebase's other `MAX_*` constants (e.g. `file_tool.
       MAX_PDF_IMAGES`). This wasn't something the OLD mock/Graph path
       ever exposed, since `calendar_tool._mock_events()`'s fake data has
       trivially small attendee lists — a real, previously-invisible scale
       issue that only showed up once this was pointed at a real mailbox.
  - `_clean_recipient_addresses()` mirrors `_clean_sender_address()`'s
    EX-vs-SMTP resolution logic for meeting attendees (only required
    attendees, matching `calendar_tool.get_upcoming_events`'s existing
    shape — optional attendees aren't surfaced there either).

- Email + calendar reads (Microsoft Graph): **mock data**. Real Azure app
  registration needs IT admin consent, still pending (Mail.Read,
  Calendars.Read).
- Email **sending** and calendar **event creation**: real code exists
  (`email_tool.send_email`, `calendar_tool.create_event`, added
  2026-09-23), gated the same way flight booking is — CEO explicitly asked
  to build the approval flow now and get the actual Graph write
  permissions later. Needs its OWN, separate admin consent: Mail.Send and
  Calendars.ReadWrite are different permissions from the read-only
  Mail.Read/Calendars.Read above — granting the reads does NOT unlock
  these. Flow: the model can't call either function directly (deliberately
  not registered as LLM tools, see orchestrator.TOOLS) — if asked to send
  an email or book a meeting, its system prompt has it emit a fenced
  ` ```agmc-send-email ` or ` ```agmc-calendar-event ` block with the
  COMPLETE content (full body, not a placeholder — that's exactly what
  gets sent). The frontend renders that as a real approval card; clicking
  Confirm posts to `POST /send-email` or `POST /calendar-event` in
  main.py, the only places `send_email`/`create_event` are ever called.
  Unlike flight booking, there's no known-itinerary-style server-side
  cross-check possible here — an email's content is new, model-composed
  text, not a reference to something already fetched, so there's nothing
  to validate it against. The safeguard is structural in a different way:
  chat text alone can never trigger either endpoint (no tool exists for
  it), and the to/subject/body or subject/start/end/attendees sent are
  exactly what the approval card already showed the CEO — nothing is
  regenerated at send time. While the write Graph permissions aren't
  granted (or while MS_CLIENT_ID/SECRET/TENANT_ID aren't set at all), both
  functions **simulate** the action and say so plainly (`mock: true`,
  never `sent: True`/`created: True`) rather than silently pretending
  something real happened — don't change that to a bare "success" message,
  the CEO needs `sent`/`created` to be trustworthy flags.
- Social: Meta official API access for Instagram was **abandoned** (boss's
  call — wanted a no-cost, no-API-account approach). Working method for
  Instagram/Facebook is site-restricted web search (`site:instagram.com`,
  `site:facebook.com`) — mandated by `orchestrator.SYSTEM_PROMPT` for ANY
  social-media question, not just ones that name the platform (see the
  `get_x_mentions` gotcha below for why "only when asked specifically"
  quietly stopped working). X has its own registered tool,
  `get_x_mentions` (`tools/social_tool.py`) — real keyword search if
  `X_BEARER_TOKEN`/`X_SEARCH_TERM` are set, mock data otherwise. (Stale
  reference removed 2026-09-24: this used to point at
  `agents/social_agent.py`'s system prompt — that file doesn't exist in
  the current flat-orchestrator build, see the Architecture section's
  2026-09-23 correction.)
- Flight fares: **live via Ignav Flight Prices API** (`tools/flight_tool.py`,
  `search_flights` tool), falls back to mock data if `IGNAV_API_KEY` isn't
  set. (Originally built against Amadeus for Developers' self-service API —
  Amadeus decommissioned that entire portal on 2026-07-17, existing keys
  disabled, Enterprise-only now. Don't re-add Amadeus self-service; if a
  future alternative is needed, re-evaluate then.)
- Flight **booking**: real, but deliberately scoped to a link handoff, not a
  purchase — this system never touches payment details or completes a sale.
  Flow: `search_flights` results carry an `ignav_id`; if the CEO asks to book
  one, the orchestrator's system prompt has it emit a fenced ` ```agmc-booking `
  block (ignav_id + display fields) instead of calling any tool. The frontend
  parses that block into a real approval card; clicking Confirm calls the
  new `POST /booking-link` endpoint (`main.py`), which calls
  `flight_tool.get_booking_link` and returns a real link to the airline/OTA's
  own site. The CEO completes the actual purchase there himself, with his own
  payment method. **`get_booking_link` is intentionally NOT registered in
  orchestrator.TOOLS/TOOL_DISPATCH** — it's only reachable via that one
  endpoint, itself only reachable by a real UI button click, so chat text
  alone (including injected/hallucinated content) can never trigger it. Don't
  "simplify" this by giving the LLM a direct booking tool — that would
  collapse the structural approval gate into a conversational one, which is
  the thing this design was built to avoid. Known caveat: the link Ignav
  returns is sometimes just the airline's homepage (with tracking params),
  not a deep link to that exact fare — provider-dependent, not a bug here.
- Hotels: **search-links only, no pricing** (`tools/hotel_tool.py`,
  `search_hotels` tool, added 2026-09-23). Checked first: Ignav (the flight
  data source) is flight-only, no hotel product — confirmed via its docs.
  Finding a real hotel-pricing API with a free/self-service signup is its
  own research task (the same bar Ignav had to clear after Amadeus's
  self-service portal died) and wasn't pursued — CEO explicitly chose
  "search links only" over researching one. So this is NOT the same
  pattern as flights: no priced comparison, no ignav_id, no booking-link
  approval gate — it just builds real, verified URL formats for
  Booking.com, Expedia, and Google Hotels from the location/dates/guests
  in the request, and the model presents them as plain links, same as
  flights' "Or search yourself" links. No external API call happens at
  all, so there's nothing here to structurally gate — don't add an
  approval-card flow "to match flights" unless a real priced data source
  gets added first. If one ever does, the flight link-handoff pattern
  (`get_booking_link` unreachable from the LLM, `_known_itineraries`-style
  session validation) is the template to follow.

## User preferences

- Candid and direct — surface real downsides and tradeoffs plainly, don't
  just cheerlead a change because it was requested.
- Prefers full files over diffs/snippets when sharing code changes.

### UI pass 2: chip row no longer shrinks; Claude-style model picker (2026-09-30)

- **Chip row squeezed as chats grew**: `.palette.has-chat .actions` has
  `overflow-x:auto`, which makes a flex item's min-height 0, so the column
  layout shrank it a little with every message/card. Fixed with `flex: none`
  (plus slightly more padding). Measured: 51px with 25 messages in the thread.
- **Model choice moved out of the header** (the segmented Claude/Local toggle
  is gone) into a pill beside the send button (`#modelPill`) that opens an
  upward menu (`#modelMenu`, `.model-opt`) like the Claude app: name, one-line
  description, checkmark on the active one. Pill label is just "Claude" or
  "Local" (a long model name squeezed the send button); the menu's Local row
  shows "Local · qwen2.5:3b" from `GET /model`. Same backend contract
  (`GET`/`POST /model`). Closes on outside click / Escape; the pill is disabled
  while a reply is generating. `createProgress.isLocal` now reads
  `.model-opt.active`. Placeholder shortened to "Ask anything…" for room.
  Not rebuilt into the installer.
- **Chips that need details now pre-fill instead of sending** (2026-09-30, CEO
  ask): "Schedule a meeting" -> `Schedule a meeting with `, "Book a flight" ->
  `Find a flight from Dubai to ` (CEO chose to keep "from Dubai"), "Book a
  hotel" -> `Find a hotel in ` (`data-fill`). Summarize inbox / Social mentions
  still send directly (`data-q`). The old fixed texts were sample requests, not real data.
- **Closing the panel**: the header X (which only ever collapsed to the bubble,
  never quit) is now a "minimize" dash with tooltip "Minimize (Esc)", and Esc
  collapses the panel (only when no menu is open — Esc closes that first).
  `collapsePanel()` is shared by both. Click-outside/blur-to-collapse was
  deliberately NOT added (it would hide the widget whenever he clicks into
  Outlook to copy something) — ask before adding.
- **Claude switched off in the UI (2026-09-30, CEO ask)**: `LOCAL_ONLY = true`
  in `frontend/agmc-assistant-popup.html` hides the model picker and makes the
  page force the backend to Ollama (`ensureLocalProvider()`, called at the top
  of each `checkBackend()` poll, so it also covers a backend that starts later).
  All backend Claude code and the picker markup/JS are untouched — set
  `LOCAL_ONLY = false` to bring the picker back. The backend's own default
  provider is still `anthropic` until a page connects. Not rebuilt into the installer.

### Scheduling a meeting under the local model — FIXED the same way (2026-09-30)

CEO report: "Schedule a meeting with fatima.masood@agmc.com for tomorrow at 3pm
for 1 hr called test" -> the local model said the calendar was full, proposed
Friday 10:00 instead of the time asked, said "confirmed" with NO approval card,
looped on every "yes", and wrote "Fatima Masood @agmc.com". Same cause as the
inbox/reply failures (a 3B model can't do the bookkeeping), same fix: code does
it, the model is not called at all.

- `backend/scheduling.py` (pure, unit-testable with a fixed "today"): intent
  (`is_schedule_request`: schedule/set up/book... + meeting/call/..., excludes
  flights/hotels/reply/cancel/reschedule/"what's my schedule"), and parsing of
  attendee addresses/names, day (today/tomorrow/weekday/"next fri"/15 october/
  ISO/dd-mm), time ("3pm", "10:00am", "14:30", "noon", "from 2 to 3pm"), length
  ("for 1 hr", "45 minutes", "half an hour"; default 30 min, said in the reply)
  and title ("called/titled/about X", quoted text). A bare "at 3" is ASKED
  ("AM or PM?"), never guessed. Weekday rule: "on friday" said on a Friday means
  next week's; "next friday" = the one in next calendar week.
- `orchestrator._schedule_events` (first handler in `_ollama_events`, before
  `_reply_events`): names are resolved with `resolve_contact` and the resolved
  "Name (address)" is shown; an unresolvable name ASKS for an address; missing
  who/day/time is asked (one pending question, kept in `_pending_schedule`,
  consumed by the next message — only accepted if it fills something that was
  missing, so an unrelated question containing "today" isn't hijacked); a time in
  the past asks for another day; overlaps on the real calendar are a "Heads-up"
  line, never a refusal (all-day banners ignored); "just me" makes a personal
  block; "the meeting ... instead" inherits the last meeting's attendees/title.
  Emits the usual `agmc-calendar-event` block, built with `json.dumps`, times in
  Gulf time with no offset. Status events: resolve_contact, read_calendar.
- **Typed "yes" never goes to the model any more**: after any reply or meeting
  proposal, `_awaiting_confirm` makes a bare yes/ok/confirm answer with "use the
  button on the card" (the model used to claim it had confirmed). Cleared by any
  other message.
- The local-model "still setting up" message no longer says "switch to Claude"
  (Claude is hidden in the UI).
- Verified: 50+ parser cases, an end-to-end conversation test with faked Outlook
  (the CEO's exact transcript, follow-ups, ambiguity, past time, unrelated
  messages, flights still reaching the model), and the real backend on a
  throwaway port with the real Outlook (read-only; nothing was booked).
- **NOT rebuilt into the installer / not pushed to GitHub.**

### Chat history (2026-09-30, CEO ask: "only if possible")

- **Storage** (`backend/history_store.py`): one JSON file per conversation in
  `%APPDATA%\AGMC X\history` (`AGMC_DATA_DIR` overrides). Only the VISIBLE
  transcript is kept (what he typed + what the assistant answered), saved by
  `/chat/stream` after each COMPLETED reply (a failed turn is not saved, so roles
  always alternate). Capped at 200 chats. Plain text on his own profile, like his
  Outlook cache; Delete / "Clear all history" remove the files for real.
- **Proposal cards are stripped when saving** (```agmc-*``` blocks become a
  "card not restored" note) and history renders as plain read-only messages, so an
  old "Send email"/"Book meeting" card can never be clicked days later.
- **Resuming**: opening a chat sets `SESSION_ID` to its id; if the backend has no
  in-memory session for it, `chat_stream` calls `orchestrator.hydrate_session`
  (last 12 messages, plain text, strictly alternating, ends on an assistant turn).
  The old chat's tool results are gone, so the model continues from what was SAID
  (e.g. `reply_ids` from an old inbox read are not restored — re-read the inbox).
- **History routes** (`GET /history`, `GET|DELETE /history/{id}`, `DELETE /history`)
  refuse any request whose `Origin` is not absent / `null` (file://) / localhost
  (`_local_origin_only`) — CORS is wide open, and without this any web page he
  visits could read his conversations from his browser. Ids are validated
  (`^[A-Za-z0-9_-]{1,80}$`) because they become file names. **The rest of the API
  is still wide-open CORS** — restricting it is still recommended.
- **UI**: a History chip (after New chat; full-width at the bottom of the welcome
  grid) opens a full-panel list (title, "Today, 3:41 PM · 2 messages", two-step
  Delete, two-step "Clear all history"); click opens the chat; Esc closes History
  before it collapses the panel. Titles are set with `textContent`.
- **Behaviour change**: every launch of the page now gets its own session id
  (was the fixed `ceo-session-1`, which silently kept hidden backend context after a
  page reload that the UI didn't show).
- Verified: backend tests on a scratch data folder (save, failed turn not saved,
  proposal stripped, resume after simulated restart, bad ids, foreign-origin 403,
  delete, cap, clear), and the UI in the browser pane against a scratch backend.
  **Not rebuilt into the installer / not pushed.**

### Teams messages — NOT built (needs IT), see chat 2026-09-30
Reading/replying to Teams chats needs Microsoft Graph delegated `Chat.Read` +
`ChatMessage.Send` (normally user-consentable, but AGMC's tenant blocks user
consent entirely, as with Mail.Read). Outlook COM cannot see Teams chats and the
Teams desktop app has no automation API. Plan when approved: same pattern as
email (read + summarize fast path, reply via an approval card, chat ids validated
against a server-side "known chats" map).

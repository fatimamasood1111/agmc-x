// AGMC Assist desktop widget — Electron main process.
//
// Replaces the earlier pywebview-based desktop_app.py: that approach hit
// real, unresolved reliability problems specific to hosting WebView2
// inside a WinForms window via pythonnet — window transparency needed a
// manual Win32 hack (Form.Region clipping) since pywebview's own
// transparent= flag didn't work, and even with that working, exact pixel
// sizing for a small window was inconsistent run to run (confirmed with
// DPI-awareness fixes, explicit min_size, raw SetWindowPos, and a
// self-correcting measure-and-retry loop — none of it converged
// reliably). Electron's BrowserWindow has mature, genuinely reliable
// support for transparent + frameless + precisely-sized windows — this
// is one of Electron's most common, well-trodden use cases (chat bubbles,
// overlay widgets), unlike pywebview where it's a rarely-exercised corner
// of a much smaller, thinner wrapper library.
//
// Same behavior as before: a small round bubble in the bottom-right
// corner of the screen that expands into the full chat panel on click and
// shrinks back on close, starting the backend itself if it isn't already
// running (never touching one that's already up).

const { app, BrowserWindow, ipcMain, screen, Tray, Menu } = require("electron");
const path = require("path");
const http = require("http");
const https = require("https");
const fs = require("fs");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const BACKEND_DIR = path.join(ROOT, "backend");
const VENV_PYTHON = path.join(BACKEND_DIR, "venv", "Scripts", "python.exe");
const HEALTH_URL = "http://localhost:8000/health";

// --- Local-model (Ollama) auto-setup, added 2026-09-29 ---------------------
// Direct follow-up to the CEO's boss having to manually run
// setup_local_model.ps1 on his own machine before the local-model toggle
// would work at all. This automates the exact same two required steps
// (installing Ollama itself, pulling the model) from inside the app on
// every launch — best-effort and entirely non-blocking, same philosophy as
// SearXNG's own optional setup in that script: never delays the widget from
// opening, and any failure just means local mode isn't ready yet, not a
// crash.
//
// SearXNG itself (real web search backing tools/web_search_tool.py) is
// handled differently from Ollama below — added 2026-09-29. Setting it up
// FROM SOURCE on someone else's machine is genuinely fragile (git clone, a
// Python venv, source patches for a Windows-incompatibility bug in
// valkeydb.py — see CLAUDE.md's SearXNG section for the full history), so
// rather than automate that whole chain, it's frozen ONCE here into a
// standalone executable (same PyInstaller approach as the backend itself —
// see .tools/searxng/run_frozen.py) and bundled as a prebuilt binary via
// electron-builder's extraResources. The target machine never needs git,
// Python, or any of the source patches — it just runs an .exe, exactly like
// the backend already does.
const OLLAMA_DIR = path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama");
const OLLAMA_EXE = path.join(OLLAMA_DIR, "ollama.exe");
const OLLAMA_APP_EXE = path.join(OLLAMA_DIR, "ollama app.exe");
// Keep this in sync with backend/config.py's OLLAMA_MODEL default — this
// side has no way to read that Python file's value at runtime.
const OLLAMA_MODEL = "qwen2.5:3b";
const OLLAMA_TAGS_URL = "http://localhost:11434/api/tags";
// Confirmed live 2026-09-29: this is Ollama's real Windows installer (Inno
// Setup based, ~1.57GB — bundles CUDA/ROCm backends, hence the size), and
// /VERYSILENT /SUPPRESSMSGBOXES /NORESTART genuinely runs it with zero UI.
// An earlier test of these same flags looked like a real failure (exit code
// 1, "setup failed to initialize") — that turned out to be a false alarm
// caused by testing against a still-downloading, truncated file, not a
// problem with the flags themselves; confirmed clean against the complete
// download, with a real successful install log to show for it.
const OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe";
// Not shown to the user anywhere — this is strictly a diagnostic trail in
// case local-model setup silently fails on someone else's machine and needs
// investigating after the fact, same reasoning as this project's other
// "redirect stdout/stderr so a hidden window's errors are visible" gotchas.
const LOCAL_MODEL_LOG = path.join(app.getPath("userData"), "local-model-setup.log");

// Packaged (electron-builder) vs. dev (`npm start`/`electron .`) resolve
// differently — added 2026-09-25 to let the CEO's boss run this standalone
// on his own PC with no Python/Node install at all. In dev, __dirname is
// desktop/ and everything sits at its normal repo-relative path (ROOT
// above). Packaged, main.js runs from inside app.asar, so anything NOT
// bundled as Electron source (the PyInstaller-frozen backend, frontend/,
// icon.ico) has to be pulled in via electron-builder's extraResources
// (see package.json's build config) and read back from
// process.resourcesPath instead — don't use ROOT-relative paths for these
// three below when app.isPackaged is true.
const FRONTEND_HTML = app.isPackaged
  ? path.join(process.resourcesPath, "frontend", "agmc-assistant-popup.html")
  : path.join(ROOT, "frontend", "agmc-assistant-popup.html");
const ICON_PATH = app.isPackaged
  ? path.join(process.resourcesPath, "icon.ico")
  : path.join(ROOT, "icon.ico");
const BACKEND_EXE = app.isPackaged
  ? path.join(process.resourcesPath, "backend-dist", "agmc-backend.exe")
  : null;
// Dev: the same venv/source setup CLAUDE.md's SearXNG section documents.
// Packaged: the frozen searxng.exe from .tools/searxng/dist/searxng (see
// electron-builder's extraResources in package.json).
const SEARXNG_DIR = path.join(ROOT, ".tools", "searxng");
const SEARXNG_VENV_PYTHON = path.join(SEARXNG_DIR, "venv", "Scripts", "python.exe");
const SEARXNG_EXE = app.isPackaged
  ? path.join(process.resourcesPath, "searxng-dist", "searxng.exe")
  : null;
const SEARXNG_URL = "http://127.0.0.1:8888/";

const MARGIN = 20; // gap from the screen's usable-area edge (excludes the taskbar, via screen.workArea)
const BUBBLE_SIZE = 72; // tight around the 56px mascot circle + a little breathing room — reliable here, unlike pywebview, because Electron actually honors requested window sizes exactly
const PANEL_W = 400;
const PANEL_H = 640;
const PANEL_RADIUS = 16; // matches .palette's own CSS border-radius

let win = null;
let backendProc = null;
let searxngProc = null;
let tray = null;

// Found 2026-09-24: clicking the Desktop icon while an instance is already
// running launched a SECOND full app (its own window, tray icon, and
// taskbar entry) rather than reopening the existing one — confusing, even
// though the chat itself still worked fine underneath, since both windows
// talk to the SAME backend session (SESSION_ID is a hardcoded constant in
// agmc-assistant-popup.html, not per-window), which is why the "second"
// window could still see the ongoing conversation. requestSingleInstanceLock
// is Electron's standard fix: the first instance to grab the lock keeps
// running, and any later launch attempt fires "second-instance" on THAT
// original process instead of creating its own app — so it can just
// refocus the real window and the newcomer process exits immediately.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => showWindow());
}

// Found 2026-09-24, right after adding the single-instance lock above: if
// the window is ever destroyed while the app process stays alive (unclear
// exact trigger — possibly Alt+F4 or some other native close path this
// frameless window doesn't otherwise expose), every "if (win) {...}" guard
// silently does nothing, and since the process still holds the single-
// instance lock, EVERY future launch attempt from the Desktop icon just
// hands off to this now-window-less zombie and produces no visible
// result at all — the exact "icon click does nothing" bug the CEO hit.
// showWindow() recreates the window if it's gone instead of silently
// no-op'ing; use this everywhere a window needs to be (re)shown, not a
// bare "if (win) win.show()".
function showWindow() {
  if (!win) {
    createWindow();
    return;
  }
  win.show();
  win.focus();
}

function backendIsUp() {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, { timeout: 1500 }, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

function startBackend() {
  // Packaged: run the PyInstaller-frozen backend-dist/agmc-backend.exe
  // directly — no Python install needed on the target machine at all.
  // Dev: same command CLAUDE.md's manual instructions use, just as a
  // background subprocess instead of a terminal the CEO has to remember
  // to open himself. windowsHide keeps a console window from popping up
  // next to the widget, either way.
  if (app.isPackaged) {
    backendProc = spawn(BACKEND_EXE, [], {
      cwd: path.dirname(BACKEND_EXE),
      windowsHide: true,
    });
  } else {
    backendProc = spawn(VENV_PYTHON, ["-m", "uvicorn", "main:app", "--port", "8000"], {
      cwd: BACKEND_DIR,
      windowsHide: true,
    });
  }
}

async function ensureBackend() {
  if (await backendIsUp()) return;
  startBackend();
  for (let i = 0; i < 30; i++) {
    // up to ~15s for uvicorn to come up
    if (await backendIsUp()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  // Still not up — open the widget anyway. The UI's own "Offline demo"
  // indicator already handles this gracefully, same as double-clicking
  // the HTML file with no server running.
}

function logLocal(msg) {
  try {
    fs.appendFileSync(LOCAL_MODEL_LOG, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {
    // Best-effort logging only — never let a logging failure break setup.
  }
}

function downloadFile(url, destPath, redirectsLeft = 5) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(destPath);
    https
      .get(url, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          file.close();
          fs.unlink(destPath, () => {});
          if (redirectsLeft <= 0) return reject(new Error("Too many redirects"));
          return resolve(downloadFile(res.headers.location, destPath, redirectsLeft - 1));
        }
        if (res.statusCode !== 200) {
          file.close();
          fs.unlink(destPath, () => {});
          return reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        }
        res.pipe(file);
        file.on("finish", () => file.close(() => resolve()));
      })
      .on("error", (err) => {
        file.close();
        fs.unlink(destPath, () => {});
        reject(err);
      });
  });
}

function installOllamaSilently(installerPath) {
  // Confirmed live 2026-09-29 against the real installer: these three
  // flags (Inno Setup's standard silent-install switches) produce a
  // genuinely unattended install — no window, no click needed. Don't
  // drop /SUPPRESSMSGBOXES or /NORESTART; either can reintroduce a
  // blocking dialog on someone else's machine even with /VERYSILENT set.
  return new Promise((resolve, reject) => {
    const proc = spawn(installerPath, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], {
      windowsHide: true,
    });
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Ollama installer exited with code ${code}`));
    });
    proc.on("error", reject);
  });
}

function ollamaServerIsUp() {
  return new Promise((resolve) => {
    const req = http.get(OLLAMA_TAGS_URL, { timeout: 1500 }, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function ensureOllamaServerRunning() {
  if (await ollamaServerIsUp()) return true;
  if (!fs.existsSync(OLLAMA_APP_EXE)) return false;
  // Confirmed live 2026-09-29: launching ollama app.exe directly, with a
  // plain inherited environment, leaves its OWN tray process running but
  // silently never binds the API port — it never actually starts the
  // server child process. Ollama's own installer works around this by
  // launching it via `cmd /C set PATH=<install dir>;%PATH% & "ollama
  // app.exe"` (seen verbatim in the real Inno Setup install log) — its
  // server-spawning logic depends on finding ollama.exe via PATH, not its
  // own directory. Replicating that here (prepending OLLAMA_DIR to PATH)
  // is what actually gets the server to come up; dropping this env
  // override reintroduces the exact "process alive, port never answers"
  // bug this comment is describing.
  // detached + unref: this is meant to keep running as its own
  // tray-resident process (exactly like a normal Ollama install behaves),
  // not as a child tied to this Electron process's lifetime.
  const child = spawn(OLLAMA_APP_EXE, [], {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
    env: { ...process.env, PATH: `${OLLAMA_DIR};${process.env.PATH || ""}` },
  });
  child.unref();
  for (let i = 0; i < 20; i++) {
    // up to ~20s for the server to come up
    if (await ollamaServerIsUp()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

function modelIsPulled() {
  return new Promise((resolve) => {
    http
      .get(OLLAMA_TAGS_URL, { timeout: 3000 }, (res) => {
        let body = "";
        res.on("data", (chunk) => (body += chunk));
        res.on("end", () => {
          try {
            const parsed = JSON.parse(body);
            const names = (parsed.models || []).map((m) => m.name);
            resolve(names.includes(OLLAMA_MODEL));
          } catch {
            resolve(false);
          }
        });
      })
      .on("error", () => resolve(false));
  });
}

function pullModel() {
  return new Promise((resolve) => {
    const proc = spawn(OLLAMA_EXE, ["pull", OLLAMA_MODEL], { windowsHide: true });
    proc.on("exit", (code) => resolve(code === 0));
    proc.on("error", () => resolve(false));
  });
}

// Fire-and-forget from app.whenReady() — deliberately never awaited there.
// Downloading Ollama (~1.57GB) and pulling a model (~2GB) can take several
// minutes on a slow connection; this must never delay the widget opening
// in normal (Claude) mode, and any step failing just means the local-model
// toggle isn't ready yet on this launch, not a crash. Logs to
// LOCAL_MODEL_LOG instead of the console, since windowsHide means there's
// no visible window to show progress in anyway (and no UI hook for it —
// see the follow-up note about a real "setting up local AI" progress
// affordance if this is ever asked for).
async function ensureLocalModel() {
  try {
    if (!fs.existsSync(OLLAMA_EXE)) {
      logLocal("Ollama not installed — downloading the installer...");
      const installerPath = path.join(app.getPath("temp"), "AGMC-OllamaSetup.exe");
      await downloadFile(OLLAMA_INSTALLER_URL, installerPath);
      logLocal("Installer downloaded — running silent install...");
      await installOllamaSilently(installerPath);
      logLocal("Ollama installed.");
      fs.unlink(installerPath, () => {});
    } else {
      logLocal("Ollama already installed.");
    }

    const serverUp = await ensureOllamaServerRunning();
    if (!serverUp) {
      logLocal("Could not get the Ollama server running — giving up for this launch.");
      return;
    }

    if (await modelIsPulled()) {
      logLocal(`Model ${OLLAMA_MODEL} already present.`);
      return;
    }

    logLocal(`Pulling model ${OLLAMA_MODEL} (first time only, can take a few minutes)...`);
    const pulled = await pullModel();
    logLocal(pulled ? `Model ${OLLAMA_MODEL} pulled successfully.` : "Model pull failed.");
  } catch (err) {
    logLocal(`ensureLocalModel failed: ${err && err.stack ? err.stack : err}`);
  }
}

function searxngIsUp() {
  return new Promise((resolve) => {
    const req = http.get(SEARXNG_URL, { timeout: 1500 }, (res) => {
      // Any real HTTP response (not just 200) means the server itself is
      // up and answering — good enough to know it's worth using.
      resolve(typeof res.statusCode === "number");
      res.resume();
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

function startSearxng() {
  if (app.isPackaged) {
    searxngProc = spawn(SEARXNG_EXE, [], {
      cwd: path.dirname(SEARXNG_EXE),
      windowsHide: true,
    });
  } else {
    if (!fs.existsSync(SEARXNG_VENV_PYTHON)) return false;
    searxngProc = spawn(SEARXNG_VENV_PYTHON, ["-m", "searx.webapp"], {
      cwd: SEARXNG_DIR,
      windowsHide: true,
    });
  }
  return true;
}

// Fire-and-forget from app.whenReady(), same as ensureLocalModel — never
// blocks the widget opening, and any failure just means search_web falls
// back to its existing honest mock-results behavior, not a crash. Unlike
// Ollama, there's no install step here at all: the frozen searxng.exe (or,
// in dev, the already-set-up venv) is either bundled/present or it isn't —
// see the big comment near SEARXNG_EXE's definition for why setup itself
// stays a one-time, done-by-hand step rather than something this function
// automates on someone else's machine.
async function ensureSearxng() {
  try {
    if (await searxngIsUp()) {
      logLocal("SearXNG already running.");
      return;
    }
    if (app.isPackaged ? !fs.existsSync(SEARXNG_EXE) : !fs.existsSync(SEARXNG_VENV_PYTHON)) {
      logLocal("SearXNG not bundled/set up on this machine — search_web will use mock results.");
      return;
    }
    logLocal("Starting SearXNG...");
    if (!startSearxng()) return;
    for (let i = 0; i < 20; i++) {
      // up to ~20s for the Flask app + all its engines to finish loading
      if (await searxngIsUp()) {
        logLocal("SearXNG is up.");
        return;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    logLocal("SearXNG didn't come up in time — search_web will use mock results for now.");
  } catch (err) {
    logLocal(`ensureSearxng failed: ${err && err.stack ? err.stack : err}`);
  }
}

function bubbleBounds() {
  const { workArea } = screen.getPrimaryDisplay();
  return {
    width: BUBBLE_SIZE,
    height: BUBBLE_SIZE,
    x: workArea.x + workArea.width - MARGIN - BUBBLE_SIZE,
    y: workArea.y + workArea.height - MARGIN - BUBBLE_SIZE,
  };
}

function panelBounds() {
  const { workArea } = screen.getPrimaryDisplay();
  return {
    width: PANEL_W,
    height: PANEL_H,
    x: workArea.x + workArea.width - MARGIN - PANEL_W,
    y: workArea.y + workArea.height - MARGIN - PANEL_H,
  };
}

function createWindow() {
  const bounds = bubbleBounds();
  win = new BrowserWindow({
    ...bounds,
    show: false, // held back until the explicit setBounds() below runs — see the ready-to-show handler
    frame: false,
    transparent: true,
    hasShadow: false, // avoid a native rectangular window-shadow behind the round bubble — the CSS's own shadow/pulse renders correctly now that real transparency works
    resizable: false, // fixed-shape widget; still resizable PROGRAMMATICALLY via IPC below
    alwaysOnTop: true,
    skipTaskbar: false,
    icon: ICON_PATH,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadFile(FRONTEND_HTML, { hash: "app=1" });

  // The bubble rendered as a squashed ellipse (not a circle) on first
  // cold launch only, self-correcting the moment the CEO opened and
  // closed the panel once — found 2026-09-24. Three attempts to fix this
  // from the WINDOW-BOUNDS side (setBounds in ready-to-show; adding
  // show:false and calling show() after setBounds; replicating the exact
  // bubble->panel->bubble transition before ever showing the window) all
  // failed, confirmed live each time. That ruled out window bounds/timing
  // as the real mechanism. The actual fix turned out to be on the CONTENT
  // side, not here — see agmc-assistant-popup.html's isDesktopApp block:
  // a real click's closeBtn handler happens to toggle
  // mascotBtn.style.display 'none'->'' as a side effect of an unrelated
  // fix, forcing the browser to redo that element's layout; replicating
  // that toggle once at startup is what actually fixed it. Kept show:false
  // here anyway since it's still correct in principle (no reason to show
  // the window before its content has settled), just isn't what fixed
  // this specific bug — don't assume it's dead weight and remove it.
  win.once("ready-to-show", () => {
    win.setBounds(bubbleBounds());
    win.show();
  });

  win.on("closed", () => {
    win = null;
  });
}

function createTray() {
  // Frameless windows have no native close button, so this is the real
  // "fully quit" affordance — right-click the tray icon. Left-click
  // toggles the bubble open, matching how most tray-resident chat widgets
  // behave.
  tray = new Tray(ICON_PATH);
  tray.setToolTip("AGMC X");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "Open",
        click: () => showWindow(),
      },
      { label: "Quit", click: () => app.quit() },
    ])
  );
  tray.on("click", () => showWindow());
}

ipcMain.handle("widget:expand", () => {
  if (!win) return;
  win.setBounds(panelBounds());
});

ipcMain.handle("widget:collapse", () => {
  if (!win) return;
  win.setBounds(bubbleBounds());
});

app.whenReady().then(async () => {
  await ensureBackend();
  createWindow();
  createTray();
  // Neither awaited — see each function's own comment for why this must
  // never block the widget opening in normal (Claude) mode.
  ensureLocalModel();
  ensureSearxng();
});

app.on("window-all-closed", () => {
  // Don't quit on window close — this is a tray-resident widget. Real
  // quit is the tray menu's "Quit" item, which calls app.quit()
  // explicitly and triggers before-quit below.
});

app.on("before-quit", () => {
  if (backendProc) {
    // Only stop the backend if THIS launcher started it — never kill a
    // dev server the CEO (or a developer) already had running manually.
    backendProc.kill();
  }
  if (searxngProc) {
    searxngProc.kill();
  }
});

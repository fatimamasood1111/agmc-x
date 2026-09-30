# Sets up everything needed to demo AGMC X's local-model (Ollama) mode on
# a machine that's never had any of this installed before — added
# 2026-09-29 for demoing to the CEO's boss on his own machine. Run this
# ONCE per machine, before the demo:
#
#   powershell -ExecutionPolicy Bypass -File setup_local_model.ps1
#
# Safe to re-run — every step checks whether it's already done first.
# Steps 1-2 (Ollama + the model) are REQUIRED for the local-model toggle
# to work at all. Step 3 (SearXNG, real web search under the local model)
# is best-effort and OPTIONAL — if it can't complete on this machine (no
# git/Python available, or anything else goes wrong), the app still works
# fine without it: tools/web_search_tool.py just falls back to honest
# mock search results instead of real ones. Don't let step 3 block a demo.

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot

Write-Host "=== AGMC X local-model setup ===" -ForegroundColor Cyan

# --- 1. Ollama itself ---------------------------------------------------
# Installs per-user (no admin rights needed) — same install this project's
# own dev machine used, confirmed live at %LOCALAPPDATA%\Programs\Ollama.
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (Test-Path $ollamaExe) {
    Write-Host "[1/3] Ollama already installed." -ForegroundColor Green
} else {
    Write-Host "[1/3] Ollama not found — downloading the official installer from ollama.com..."
    $installer = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Write-Host "Launching the installer — please complete it (it's quick, no admin prompt expected)."
    Start-Process -FilePath $installer -Wait
    if (-not (Test-Path $ollamaExe)) {
        Write-Host "Ollama still isn't at the expected path after install — check it actually completed, then re-run this script." -ForegroundColor Red
        exit 1
    }
    Write-Host "Ollama installed." -ForegroundColor Green
}

# --- 2. Pull the model ---------------------------------------------------
# qwen2.5:3b, ~1.9GB — the model this app defaults to (config.OLLAMA_MODEL).
# If that default is ever changed, update this line to match.
Write-Host "[2/3] Pulling qwen2.5:3b (about 1.9GB — can take a few minutes on a normal connection)..."
& $ollamaExe pull qwen2.5:3b
Write-Host "Model ready." -ForegroundColor Green

# --- 3. SearXNG (optional — real web search under the local model) ------
$searxngDir = Join-Path $repoRoot ".tools\searxng"
$searxngVenvPython = Join-Path $searxngDir "venv\Scripts\python.exe"

if (Test-Path $searxngVenvPython) {
    Write-Host "[3/3] SearXNG already set up." -ForegroundColor Green
} else {
    Write-Host "[3/3] Setting up SearXNG (real, free web search for local-model mode)..."
    try {
        $git = Get-Command git -ErrorAction Stop
        $python = Get-Command python -ErrorAction Stop

        New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot ".tools") | Out-Null
        if (-not (Test-Path $searxngDir)) {
            # --no-checkout + a sparse-checkout exclusion, NOT a plain clone:
            # SearXNG ships a few packaged filenames containing a literal ":"
            # (e.g. utils/templates/etc/httpd/sites-available/searxng.conf:socket)
            # which is invalid on Windows/NTFS and makes a plain `git clone`
            # fail outright with "unable to checkout working tree". Confirmed
            # live 2026-09-28 setting this up the first time.
            git clone --depth 1 --no-checkout https://github.com/searxng/searxng.git $searxngDir
            Push-Location $searxngDir
            git sparse-checkout init --cone
            "/*`n!/utils/templates/etc/httpd/`n!/utils/templates/etc/systemd/" | Out-File -Encoding utf8 ".git\info\sparse-checkout"
            git checkout master
            Pop-Location
        }

        Push-Location $searxngDir
        python -m venv venv
        # requirements.txt FIRST, then the package itself with --no-build-isolation:
        # a plain `pip install -e .` fails with "ModuleNotFoundError: No module
        # named 'msgspec'" — its setup.py imports the searx package at build
        # time, which needs its own dependencies already installed first (a
        # chicken-and-egg problem PEP 517's isolated build env doesn't solve).
        & ".\venv\Scripts\python.exe" -m pip install -r requirements.txt
        & ".\venv\Scripts\python.exe" -m pip install setuptools wheel
        & ".\venv\Scripts\python.exe" -m pip install -e . --no-build-isolation --no-deps

        # Real Windows-incompatibility bug in SearXNG itself: searx/valkeydb.py
        # unconditionally `import pwd` (POSIX-only, used only for one diagnostic
        # log line) — without this patch the server fails to start at all with
        # "ModuleNotFoundError: No module named 'pwd'". Confirmed live 2026-09-28.
        $valkeydbPath = "searx\valkeydb.py"
        $valkeydb = Get-Content $valkeydbPath -Raw
        if ($valkeydb -notmatch "except ImportError:\s*\r?\n\s*pwd = None") {
            $valkeydb = $valkeydb -replace "import pwd\r?\n", "try:`n    import pwd`nexcept ImportError:`n    pwd = None`n"
            $valkeydb = $valkeydb -replace "_pw = pwd\.getpwuid\(os\.getuid\(\)\)\r?\n\s*logger\.exception\(""\[%s \(%s\)\] can't connect valkey DB \.\.\.""\, _pw\.pw_name\, _pw\.pw_uid\)", "if pwd is not None:`n            _pw = pwd.getpwuid(os.getuid())`n            logger.exception(""[%s (%s)] can't connect valkey DB ..."", _pw.pw_name, _pw.pw_uid)`n        else:`n            logger.exception(""can't connect valkey DB ..."")"
            Set-Content $valkeydbPath $valkeydb -Encoding utf8 -NoNewline
        }

        # Enable JSON output (default is html-only) and set a real secret key
        # (the shipped default, "ultrasecretkey", is a documented placeholder —
        # fine for this localhost-only, single-user setup, but don't leave the
        # literal placeholder string either).
        $settingsPath = "searx\settings.yml"
        $settings = Get-Content $settingsPath -Raw
        if ($settings -notmatch "(?m)^\s*-\s*json\s*$") {
            $settings = $settings -replace "(\r?\n\s*formats:\r?\n\s*-\s*html)", "`$1`n    - json"
        }
        $newSecret = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
        $settings = $settings -replace 'secret_key: "ultrasecretkey"', "secret_key: ""$newSecret"""
        Set-Content $settingsPath $settings -Encoding utf8 -NoNewline

        $startScript = @"
@echo off
REM Starts the local SearXNG search instance used by tools/web_search_tool.py
REM for the Ollama/local-model path. Binds to 127.0.0.1:8888 only — not
REM reachable from outside this machine. Leave this window open while
REM using local-model web search; closing it stops the server, and
REM web_search_tool.py just falls back to Brave/mock.
cd /d "%~dp0"
venv\Scripts\python.exe -m searx.webapp
"@
        Set-Content "start_searxng.bat" $startScript -Encoding ascii
        Pop-Location
        Write-Host "SearXNG set up. Start it with .tools\searxng\start_searxng.bat before a demo that needs real web search." -ForegroundColor Green
    } catch {
        Write-Host "SearXNG setup skipped/failed ($_) — not fatal, the app still works fine, web search under the local model will just show honest mock results instead of real ones." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Before demoing: make sure Ollama is running (it usually auto-starts), and if you set up SearXNG, run .tools\searxng\start_searxng.bat first."

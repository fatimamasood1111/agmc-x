"""
PyInstaller entry point for the packaged desktop distribution — added
2026-09-25 to let the CEO's boss run this on his own PC with no Python
install at all. NOT used for normal development (that's still `uvicorn
main:app --reload --port 8000`, see CLAUDE.md) — this exists only so
PyInstaller has a plain Python API call to freeze, instead of uvicorn's
string-based `"main:app"` CLI resolution, which PyInstaller's static
import scanner can't always trace reliably.

Also handles being launched from a different working directory than
backend/ (a frozen .exe's cwd isn't guaranteed to be its own folder) by
resolving paths relative to this file, and loads .env explicitly from
next to the frozen exe rather than relying on load_dotenv()'s cwd-relative
default search.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
os.chdir(BASE_DIR)  # tools/file_tool.py etc. assume cwd-relative behavior matching normal `uvicorn` usage from backend/

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

import uvicorn
from main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

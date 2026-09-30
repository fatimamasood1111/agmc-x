"""
Read-only local file tool.

IMPORTANT — this only works because, right now, the backend process and
the person asking questions are on the SAME machine (your laptop, during
testing). Once this backend runs somewhere else (a server, the cloud),
a file path typed by the CEO refers to HIS laptop, which this code has
no access to — "read local files" then needs to become a real upload
flow (the frontend sends the file's bytes to the backend), not a path
lookup. Don't carry this tool as-is into a production deployment without
re-checking that assumption.

SECURITY NOTE: as written, this can read anything the backend process's
OS user account can read — not just an intended "documents" folder. For
anything beyond your own solo testing, restrict ALLOWED_DIRS below to
specific folders, and/or add an approval step before returning file
contents, the same way booking actions get one.
"""
import base64
import sys
from pathlib import Path

# Uncomment and edit to restrict which folders can be read, e.g.:
# ALLOWED_DIRS = [Path.home() / "Documents" / "AGMC"]
ALLOWED_DIRS: list[Path] | None = None  # None = no restriction (fine for solo testing only)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_CHARS = 20000  # cap what gets sent to the model — a huge file would blow the context window
MAX_EXCEL_ROWS = 200  # per sheet — a full financial workbook could otherwise blow the context window
MAX_LISTED_FILES = 50  # cap what a folder listing returns
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Anthropic's own cap per image; reject early with a clear message instead of a cryptic API error later
MAX_PDF_IMAGES = 10  # cap embedded images sent to the model — a big brochure could otherwise blow up token cost/context


def _shell_folder(registry_name: str, fallback: Path) -> Path:
    """Resolve a special folder (Desktop, Documents, ...) the way Windows
    itself does, instead of assuming it's directly under the home folder.

    Corporate/Enterprise machines commonly use OneDrive "Known Folder Move"
    to redirect Desktop and Documents into a company-branded OneDrive folder
    (e.g. "OneDrive - <Company>\\Desktop") — discovered 2026-09-23 on the
    CEO's actual machine, where Path.home() / "Desktop" doesn't exist at all,
    and Path.home() / "Documents" exists but is an empty decoy (just the
    standard My Music/My Pictures/My Videos junctions, no real files) while
    the OneDrive-redirected one has the CEO's actual documents. A
    read_local_file/list_files call against "desktop" or "documents" would
    fail or silently return the wrong (empty) folder on any such machine —
    this was a latent bug from day one here, not something that regressed.

    Reads HKCU\\...\\User Shell Folders, the same source Windows Explorer
    itself uses, rather than hardcoding "OneDrive - Albatha Holding LLC" —
    hardcoding the company name would break the moment this runs on a
    different tenant/machine. Falls back to the naive path if the registry
    lookup fails (non-Windows, or the value isn't set) or doesn't exist."""
    if sys.platform != "win32":
        return fallback
    try:
        import os
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, registry_name)
        resolved = Path(os.path.expandvars(value))
        return resolved if resolved.is_dir() else fallback
    except OSError:
        return fallback


COMMON_FOLDERS = {
    "downloads": _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}", Path.home() / "Downloads"),
    "documents": _shell_folder("Personal", Path.home() / "Documents"),
    "desktop": _shell_folder("Desktop", Path.home() / "Desktop"),
    "home": Path.home(),
}


def list_files(folder: str = "downloads", keyword: str = "") -> dict:
    """Lists files in a folder, optionally filtered by a filename keyword.
    Lets the assistant resolve a vague reference ('the demo file in my
    downloads') into an actual path, which it then passes to read_local_file.

    SECURITY NOTE: this reveals filenames, not file contents — but
    filenames alone can be sensitive (a folder full of personal or
    financial document names, say). Same ALLOWED_DIRS restriction as
    read_local_file applies here; turn it on together if this needs
    tightening beyond solo testing."""
    key = folder.strip().lower()
    base = COMMON_FOLDERS.get(key, Path(folder).expanduser())

    if not base.exists() or not base.is_dir():
        return {"error": f"Couldn't find folder: {folder}"}

    if ALLOWED_DIRS and not any(str(base.resolve()).startswith(str(d.resolve())) for d in ALLOWED_DIRS):
        return {"error": f"{folder} is outside the allowed folders for this assistant"}

    matches = [
        str(f) for f in sorted(base.iterdir())
        if f.is_file() and (not keyword or keyword.lower() in f.name.lower())
    ]
    return {"folder": str(base), "files": matches[:MAX_LISTED_FILES], "total_matches": len(matches)}


def _read_text(p: Path) -> str:
    raw = p.read_bytes()
    # Try the encodings Windows text editors actually produce, in order of
    # likelihood, before falling back to permissive UTF-8. Without this,
    # a UTF-16 file (a common Notepad default) silently decodes into
    # garbled, null-byte-riddled text that LOOKS non-empty but is
    # unreadable — exactly the bug this fixes.
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _read_pdf(p: Path) -> dict:
    """Returns {"text": ..., "images": [...]} — a normal PDF with real text
    AND embedded photos/graphics (e.g. a newsletter) used to lose the
    images entirely: the old version returned text alone the moment any
    text was found, and OCR only ever ran for a FULLY scanned (zero-text)
    PDF. Added 2026-09-24, direct CEO request after uploading a PDF whose
    embedded photos were invisible to the assistant.

    Uses pypdf's own page.images (confirmed against the installed 6.19.0
    via a real hand-built test PDF before writing this, not assumed) —
    this decodes each embedded image through Pillow into a real PIL Image
    regardless of the PDF's internal compression/color space (DCTDecode/
    JPEG, raw bitmap, etc.), which is why Pillow is a real dependency again
    now — re-added deliberately; don't read the earlier "Pillow removed,
    was only ever one-time icon generation" project history as a reason to
    remove it again, this is a different, ongoing need. Every extracted
    image is re-encoded to a plain sRGB JPEG (via _encode_pdf_image) rather
    than trusting the original bytes/colorspace as-is, so the API always
    gets something it can reliably accept."""
    from pypdf import PdfReader  # imported here so the app still runs if pypdf isn't installed and PDFs are never used

    reader = PdfReader(str(p))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages)

    images = []
    for page in reader.pages:
        for img in page.images:
            if len(images) >= MAX_PDF_IMAGES:
                break
            encoded = _encode_pdf_image(img)
            if encoded:
                images.append(encoded)

    if not text.strip() and not images:
        # No extractable text and no embedded images — likely a scanned
        # page rendered as one big raster image PER PAGE that pypdf's
        # page.images doesn't surface as a normal embedded picture. Fall
        # back to OCR. This is slower (renders each page as an image, then
        # reads it) and needs Tesseract + Poppler installed as system
        # software, not just pip packages — see the README for the
        # install steps.
        text = _ocr_pdf(p)

    return {"text": text, "images": images}


def _encode_pdf_image(img) -> dict | None:
    """img is a pypdf ImageFile (page.images entries). Returns the same
    {"image_base64", "media_type"} shape _read_image uses, re-encoded to
    JPEG through Pillow so callers never have to deal with the PDF's
    original color space/compression — or None if this particular image
    couldn't be decoded (corrupt/unsupported embedded image data shouldn't
    fail the whole PDF read, just skip that one picture)."""
    import io

    if img.image is None:
        return None
    try:
        pil_img = img.image
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=85)
        raw = buf.getvalue()
    except Exception:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        return None
    return {
        "filename": img.name or "embedded_image.jpg",
        "image_base64": base64.b64encode(raw).decode("ascii"),
        "media_type": "image/jpeg",
    }


def _ocr_pdf(p: Path) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    images = convert_from_path(str(p))
    return "\n\n".join(pytesseract.image_to_string(img) for img in images)


def _read_docx(p: Path) -> str:
    import docx  # from python-docx

    doc = docx.Document(str(p))
    return "\n".join(para.text for para in doc.paragraphs)


def _read_excel(p: Path) -> str:
    from openpyxl import load_workbook

    # read_only=True keeps a lazy file handle open on the underlying zip
    # archive as you iterate sheets/rows — unlike normal-mode workbooks,
    # it MUST be closed explicitly or Windows keeps the file locked
    # indefinitely. Found 2026-09-24: main.py's /upload handler always
    # os.unlink()s the temp file right after reading it, and that unlink
    # was throwing WinError 32 ("used by another process") because this
    # function never called wb.close() — the lock was our own still-open
    # workbook, not Excel or anything else. try/finally so the handle is
    # released even if a row fails to parse partway through.
    wb = load_workbook(str(p), data_only=True, read_only=True)
    try:
        parts = []
        for sheet in wb.worksheets:
            parts.append(f"--- Sheet: {sheet.title} ---")
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i >= MAX_EXCEL_ROWS:
                    parts.append(f"... (truncated after {MAX_EXCEL_ROWS} rows)")
                    break
                cells = ["" if c is None else str(c) for c in row]
                parts.append(", ".join(cells))
        return "\n".join(parts)
    finally:
        wb.close()


READERS = {
    **{ext: _read_text for ext in TEXT_EXTENSIONS},
    # .pdf is NOT here — read_local_file handles it explicitly above,
    # since _read_pdf returns {"text", "images"}, not a plain string.
    ".docx": _read_docx,
    ".xlsx": _read_excel,
    ".xlsm": _read_excel,
}


def _read_image(p: Path, media_type: str) -> dict:
    """Returns a shape distinct from the text readers above — no "content"
    string, since there's nothing to extract. orchestrator._run_tools
    special-cases this (image_base64 present) to build a real image content
    block for Claude's vision input instead of json-dumping it as text."""
    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        return {"error": f"{p.name} is {len(raw) / 1_048_576:.1f}MB — too large to send as an image (cap is {MAX_IMAGE_BYTES // 1_048_576}MB). Resize or compress it first."}
    return {
        "filename": p.name,
        "image_base64": base64.b64encode(raw).decode("ascii"),
        "media_type": media_type,
    }


def read_local_file(path: str) -> dict:
    p = Path(path).expanduser()

    if not p.exists():
        return {"error": f"No file found at {path}"}

    if ALLOWED_DIRS and not any(str(p.resolve()).startswith(str(d.resolve())) for d in ALLOWED_DIRS):
        return {"error": f"{path} is outside the allowed folders for this assistant"}

    suffix = p.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return _read_image(p, IMAGE_EXTENSIONS[suffix])

    if suffix == ".pdf":
        # Handled separately from the generic READERS loop below because
        # _read_pdf returns {"text", "images"}, not a plain string like
        # every other reader — a PDF can have BOTH real text and embedded
        # photos, which the generic string-shaped path below can't
        # represent. orchestrator._run_tools/add_uploaded_file check for
        # this "images" key the same way they already check for
        # "image_base64" on a plain image file.
        try:
            result = _read_pdf(p)
        except ImportError as e:
            return {"error": f"Missing library to read PDF files — run: pip install -r requirements.txt ({e})"}
        except Exception as e:
            return {"error": f"Couldn't read {path}: {e}"}
        text = result["text"]
        if not text.strip() and not result["images"]:
            return {"error": f"{p.name} appears to be empty or its content couldn't be extracted."}
        out = {
            "filename": p.name,
            "content": text[:MAX_CHARS],
            "truncated": len(text) > MAX_CHARS,
        }
        if result["images"]:
            out["images"] = result["images"]
        return out

    reader = READERS.get(suffix)
    if reader is None:
        return {
            "error": (
                f"'{p.suffix}' files aren't supported. This tool reads plain text "
                f"formats ({', '.join(sorted(TEXT_EXTENSIONS))}), images (.png/.jpg/.jpeg), "
                f"plus .pdf, .docx, and .xlsx/.xlsm."
            )
        }

    try:
        content = reader(p)
    except ImportError as e:
        return {"error": f"Missing library to read {p.suffix} files — run: pip install -r requirements.txt ({e})"}
    except Exception as e:
        return {"error": f"Couldn't read {path}: {e}"}

    if not content.strip():
        return {"error": f"{p.name} appears to be empty or its content couldn't be extracted."}

    truncated = len(content) > MAX_CHARS
    return {
        "filename": p.name,
        "content": content[:MAX_CHARS],
        "truncated": truncated,
    }
"""PDF -> text. pdftotext if installed (best reading order on two-column
papers), else pypdfium2. OCR with tesseract (via pdftoppm) when a PDF has no
text layer and both are installed."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

WATERMARK = re.compile(r"^\s*(Unauthenticated|Brought to you by.*?) \| Downloaded .*$", re.M)
MIN_TEXT = 200          # characters of real text on the first pages, below which it's a scan


class NoBackend(Exception):
    pass


def backends() -> dict:
    try:
        import pypdfium2  # noqa: F401
        pdfium = True
    except ImportError:
        pdfium = False
    return {"pdftotext": bool(shutil.which("pdftotext")), "pypdfium2": pdfium,
            "tesseract": bool(shutil.which("tesseract")),
            "pdftoppm": bool(shutil.which("pdftoppm"))}


def pdf_text(path: Path, first: int | None = None, last: int | None = None) -> str:
    """Text of pages first..last (1-based, inclusive); pages separated by \\f."""
    if shutil.which("pdftotext"):
        cmd = ["pdftotext", "-enc", "UTF-8"]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        r = subprocess.run(cmd + [str(path), "-"], capture_output=True, timeout=300)
        return r.stdout.decode("utf-8", "replace")
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise NoBackend("no PDF text backend: install poppler (pdftotext) or `pip install "
                        "litnav[pdf]`") from None
    doc = pdfium.PdfDocument(str(path))
    n = len(doc)
    lo, hi = (first or 1) - 1, min(last or n, n)
    pages = []
    for i in range(lo, hi):
        tp = doc[i].get_textpage()
        pages.append(tp.get_text_range())
        tp.close()
    doc.close()
    return "\f".join(pages)


def own_text(text: str) -> str:
    """Strip publisher download watermarks, which are not the document's text."""
    return WATERMARK.sub("", text)


def has_text_layer(path: Path) -> bool:
    return len(own_text(pdf_text(path, 1, 3)).strip()) >= MIN_TEXT


def ocr_available() -> bool:
    b = backends()
    return b["tesseract"] and b["pdftoppm"]


def ocr(path: Path, first: int | None = None, last: int | None = None, dpi: int = 300) -> str:
    if not ocr_available():
        raise NoBackend("OCR needs tesseract and pdftoppm (poppler)")
    with tempfile.TemporaryDirectory() as td:
        cmd = ["pdftoppm", "-r", str(dpi), "-png"]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        subprocess.run(cmd + [str(path), f"{td}/p"], check=True, capture_output=True, timeout=1800)
        pages = []
        for img in sorted(Path(td).glob("p*.png")):
            r = subprocess.run(["tesseract", str(img), "-", "--psm", "3"], capture_output=True,
                               timeout=600)
            pages.append(r.stdout.decode("utf-8", "replace"))
    return "\f".join(pages)


def head(path: Path, pages: int = 5, ocr_fallback: bool = True) -> tuple[str, str]:
    """(text of the first pages, source) for identification."""
    t = own_text(pdf_text(path, 1, pages))
    if len(t.strip()) >= MIN_TEXT or not ocr_fallback or not ocr_available():
        return t, "text"
    return ocr(path, 1, min(pages, 3)), "ocr"


def normalise_text(t: str) -> str:
    """NFKC (ligatures: ﬁ -> fi), rejoin words hyphenated across line breaks,
    drop soft hyphens and control characters other than \\n and \\f."""
    t = unicodedata.normalize("NFKC", t).replace("­", "")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"[\x00-\x08\x0b\x0e-\x1f]", "", t)
    return t

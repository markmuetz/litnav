"""Small shared helpers."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: str | bytes, encoding: str = "utf-8") -> None:
    """Write via a temp file in the same directory and os.replace, so a crash
    or a concurrent reader never sees a half-written file. Text is written
    with LF line endings on every platform."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode(encoding) if isinstance(data, str) else data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()


def short_hash(text: str, n: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]

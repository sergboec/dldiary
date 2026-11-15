"""Walk the input folder — top level only, read-only, never recursing.

Produces bare `Item`s: identity, size, kind. Dates, provenance and previews are
filled in by later stages.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .kinds import kind_for_ext, mime_for_ext
from .manifest import Item

# Noise that is never a diary entry.
_SKIP_NAMES = {".DS_Store", ".localized", "Icon\r", ".dropbox", "desktop.ini"}

# A partially downloaded file is not yet an event.
_SKIP_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial", "~")

# For content_key: hash the head and tail only, so a 6 GB movie costs two reads.
_HASH_WINDOW = 1024 * 1024


def should_skip(path: Path) -> bool:
    name = path.name
    if name in _SKIP_NAMES or name.startswith("."):
        return True
    return name.endswith(_SKIP_SUFFIXES)


def stable_id(name: str, size: int, birthtime: float) -> str:
    """A short id that stays the same across runs for the same file.

    Deliberately not a content hash: renaming a file should produce a new
    diary entry, and re-reading 38 GB to compute ids would be absurd.
    """
    seed = f"{name}\x00{size}\x00{birthtime:.0f}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def content_key(path: Path, size: int) -> str:
    """`"<size>:<sha256 of head+tail>"`, for spotting duplicates cheaply."""
    h = hashlib.sha256()
    h.update(str(size).encode())
    try:
        with path.open("rb") as fh:
            h.update(fh.read(_HASH_WINDOW))
            if size > _HASH_WINDOW * 2:
                fh.seek(-_HASH_WINDOW, os.SEEK_END)
                h.update(fh.read(_HASH_WINDOW))
    except OSError:
        return ""
    return f"{size}:{h.hexdigest()[:32]}"


def birthtime_of(st: os.stat_result) -> float:
    """Creation time where the platform has one, else mtime."""
    return float(getattr(st, "st_birthtime", st.st_mtime))


def scan(input_dir: Path, *, skip_larger_than: int | None = None) -> Iterator[Item]:
    """Yield one bare `Item` per top-level regular file.

    Directories are not recursed into and not indexed — the diary is about
    files that arrived, and a folder is a container, not an event.
    """
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise NotADirectoryError(f"input folder not found: {input_dir}")

    for entry in sorted(os.scandir(input_dir), key=lambda e: e.name.lower()):
        path = Path(entry.path)
        if not entry.is_file(follow_symlinks=False) or should_skip(path):
            continue

        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue

        ext = path.suffix.lstrip(".").lower()
        size = st.st_size
        item = Item(
            id=stable_id(path.name, size, birthtime_of(st)),
            name=path.name,
            ext=ext,
            kind=kind_for_ext(ext),
            mime=mime_for_ext(ext),
            size=size,
            src_path=str(path),
            indexed_at=datetime.now().isoformat(timespec="seconds"),
        )

        # Oversized files are still diary entries; they just skip extraction.
        if skip_larger_than is not None and size > skip_larger_than:
            item.meta["skipped_extraction"] = "larger than --skip-larger-than"
        else:
            item.content_key = content_key(path, size)

        yield item

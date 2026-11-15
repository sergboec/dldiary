"""Deciding when an entry belongs on the timeline.

Several dates compete for one file. A screenshot taken in 2019 and re-saved
today is a 2019 photo; a train ticket downloaded today is a today event. So
every candidate is collected and kept in the manifest, and the anchor is the
first one by precedence — with the winner's name recorded, so a wrong guess is
visible rather than silent.

Precedence
----------
    manual      a human edited it; never overwritten
    exif        EXIF DateTimeOriginal (photos)
    media       container creation_time (video/audio)
    filename    a date spelled out in the filename
    quarantine  when it was downloaded — "when it entered my life"
    birth       filesystem creation time
    mtime       last modification; the floor
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .manifest import Item
from .scan import birthtime_of

PRECEDENCE = ("manual", "exif", "media", "filename", "quarantine", "birth", "mtime")

_MIN_YEAR = 1990


def plausible(dt: datetime | None) -> bool:
    """Reject epoch garbage, zeroed clocks and dates from the future."""
    if dt is None:
        return False
    now = datetime.now()
    return _MIN_YEAR <= dt.year and dt <= now.replace(year=now.year + 1)


# -- filename patterns -----------------------------------------------------
#
# Strict on purpose. Separators are required for the ambiguous forms so that
# an invoice called `175802554.pdf` is never read as a date. Bare digit runs
# are only accepted at exactly 8 characters with a leading 19xx/20xx year.

_FILENAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 2026-09-13 14.28.18   /   2026-09-13_14-28-18   /   2026-09-13T14:28:18
    (re.compile(r"(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})"
                r"[ T_]+(\d{2})[-_.:](\d{2})[-_.:](\d{2})(?!\d)"), "ymdhms"),
    # 2026-09-13  /  2026_09_13  /  2026.09.13
    (re.compile(r"(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)"), "ymd"),
    # 13-09-2026  /  13.09.2026   (day first — European)
    (re.compile(r"(?<!\d)(\d{2})[-_.](\d{2})[-_.](\d{4})(?!\d)"), "dmy"),
    # 20260222  (exactly 8 digits, must start with a plausible year)
    (re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)"), "ymd"),
)


def from_filename(name: str) -> datetime | None:
    """Pull a date out of a filename, or `None` if there isn't a safe one."""
    for pattern, shape in _FILENAME_PATTERNS:
        for m in pattern.finditer(name):
            g = [int(x) for x in m.groups()]
            try:
                if shape == "ymdhms":
                    dt = datetime(g[0], g[1], g[2], g[3], g[4], g[5])
                elif shape == "ymd":
                    dt = datetime(g[0], g[1], g[2])
                else:  # dmy
                    dt = datetime(g[2], g[1], g[0])
            except ValueError:
                continue  # 2026-13-45 and friends
            if plausible(dt):
                return dt
    return None


def parse_exif_datetime(text: str) -> datetime | None:
    """EXIF stores `2019:07:14 09:31:02`; be forgiving about separators."""
    if not text:
        return None
    cleaned = str(text).strip().rstrip("Z")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(cleaned[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
        if plausible(dt):
            return dt
    # Last resort: ISO 8601 with an offset.
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt.replace(tzinfo=None)
    return dt if plausible(dt) else None


def add_candidate(item: Item, source: str, dt: datetime | None) -> None:
    """Record a candidate date, ignoring implausible ones."""
    if plausible(dt):
        item.date_candidates[source] = dt.isoformat(timespec="seconds")  # type: ignore[union-attr]


def seed_candidates(item: Item, path: Path) -> None:
    """Add the candidates that need no file parsing: name and filesystem."""
    add_candidate(item, "filename", from_filename(item.name))
    try:
        st = path.stat()
    except OSError:
        return
    add_candidate(item, "birth", datetime.fromtimestamp(birthtime_of(st)))
    add_candidate(item, "mtime", datetime.fromtimestamp(st.st_mtime))


def resolve(item: Item, *, order: Iterable[str] = PRECEDENCE) -> None:
    """Pick the anchor date from the collected candidates.

    A `manual` candidate always wins; it is how a human correction survives
    re-indexing.
    """
    for source in order:
        value = item.date_candidates.get(source)
        if value:
            item.date = value
            item.date_source = source
            return
    # Nothing plausible at all — keep the item, flag the gap.
    item.date = ""
    item.date_source = "unknown"

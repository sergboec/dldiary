"""Where a file came from, read from macOS extended attributes.

This is the richest diary signal in the whole folder and it costs nothing:

    com.apple.quarantine                  -> download timestamp + fetching app
    com.apple.metadata:kMDItemWhereFroms  -> the source URL(s), as a binary plist

In the real Downloads folder 239 of 257 files carry quarantine data and 115
carry a source URL. "Saved an opera ticket from a Gmail attachment, in
Firefox, on 24 Oct" comes straight out of here with no guessing.

On non-macOS, or when the `xattr` package is absent, every lookup returns
empty and the rest of the pipeline carries on unaffected.
"""

from __future__ import annotations

import plistlib
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # optional: absent off macOS
    import xattr as _xattr
except ImportError:  # pragma: no cover
    _xattr = None

QUARANTINE_ATTR = "com.apple.quarantine"
WHERE_FROMS_ATTR = "com.apple.metadata:kMDItemWhereFroms"

# Download managers that identify themselves in the quarantine record under a
# bundle id or an unfriendly name.
_APP_ALIASES = {
    "com.apple.Safari": "Safari",
    "com.google.Chrome": "Chrome",
    "org.mozilla.firefox": "Firefox",
    "com.operasoftware.Opera": "Opera",
    "com.microsoft.edgemac": "Edge",
    "ru.keepcoder.Telegram": "Telegram",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.mail": "Mail",
    "com.apple.MobileSMS": "Messages",
    "com.getdropbox.dropbox": "Dropbox",
}


def available() -> bool:
    """True when extended attributes can actually be read here."""
    return _xattr is not None


def _read(path: Path, attr: str) -> bytes | None:
    if _xattr is None:
        return None
    try:
        return _xattr.xattr(str(path))[attr]
    except (KeyError, OSError, ValueError):
        return None


def read_quarantine(path: Path) -> tuple[datetime | None, str | None]:
    """`(download time, fetching app)` from the quarantine attribute.

    The value looks like `0281;6931c3ab;Firefox;<uuid>` — flags, a hex unix
    timestamp, the agent, and an event id.
    """
    raw = _read(path, QUARANTINE_ATTR)
    if not raw:
        return None, None

    try:
        parts = raw.decode("utf-8", "replace").split(";")
    except Exception:
        return None, None

    when: datetime | None = None
    if len(parts) > 1 and parts[1]:
        try:
            ts = int(parts[1], 16)
            # Guard against nonsense: 1990-01-01 .. now + 1 day.
            if 631152000 < ts < datetime.now().timestamp() + 86400:
                when = datetime.fromtimestamp(ts)
        except (ValueError, OverflowError, OSError):
            when = None

    app: str | None = None
    if len(parts) > 2 and parts[2]:
        app = _APP_ALIASES.get(parts[2], parts[2])

    return when, app


def read_where_froms(path: Path) -> list[str]:
    """Source URLs from `kMDItemWhereFroms` (a binary plist)."""
    raw = _read(path, WHERE_FROMS_ATTR)
    if not raw:
        return []
    try:
        value = plistlib.loads(raw)
    except Exception:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v.strip()]


def domain_of(url: str) -> str:
    """A readable host for a URL, with `www.` dropped.

    Browser `blob:` URLs wrap a real origin — unwrap it so a file saved from
    claude.ai reads as `claude.ai` rather than as nothing.
    """
    if not url:
        return ""
    if url.startswith("blob:"):
        url = url[len("blob:"):]
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0]


def collect(path: Path) -> dict[str, Any]:
    """Everything known about where this file came from.

    Returns `{}` when nothing is known, so the key stays absent from the
    manifest rather than carrying empty strings around.
    """
    when, app = read_quarantine(path)
    urls = read_where_froms(path)

    out: dict[str, Any] = {}
    if urls:
        out["url"] = urls[0]
        domain = domain_of(urls[0])
        if domain:
            out["domain"] = domain
        if len(urls) > 1:
            out["urls"] = urls
    if app:
        out["app"] = app
    if when:
        out["downloaded_at"] = when.isoformat(timespec="seconds")
    return out

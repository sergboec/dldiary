"""The manifest — `index.json`.

This is the single point of entry for everything downstream. The indexer writes
it; every viewer reads it and nothing else. Keep it plain JSON, plainly typed,
and stable: a viewer written against schema 1 should keep working.

Structure
---------
    {
      "schema": 1, "generated_at": ..., "tool_version": ..., "input": ...,
      "stats":  {...},                 # totals, date range, duplicate count
      "by_day": {"2026-10-24": {...}}, # precomputed, so a heatmap is free
      "items":  [ Item, ... ]
    }
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION, __version__

PREVIEW_ROLES = ("poster", "page", "frame", "cover", "card")

# Field caps.
#
# Extractors read untrusted files, and some of what comes back is unbounded:
# a `kMDItemWhereFroms` value can be a `data:` URI carrying an entire PNG,
# which once made up most of a 1.8 MB manifest. Untrusted input stops being
# unbounded the moment it crosses into the contract, not later.
MAX_URL = 2048
MAX_TITLE = 200
MAX_SUMMARY = 600
MAX_META_VALUE = 500
MAX_META_KEYS = 40
MAX_META_LIST = 50

# Date provenance, best first.
DATE_SOURCES = ("exif", "media", "filename", "quarantine", "birth", "mtime")


@dataclass
class Preview:
    """One generated preview asset belonging to an item."""

    role: str                 # poster | page | frame | cover | card
    seq: int                  # page 2, frame 3, ... ; 0 for the primary
    path: str                 # relative to the output root, e.g. "previews/ab12-p0.webp"
    w: int = 0
    h: int = 0
    bytes: int = 0
    blurred: bool = False     # rendered deliberately unreadable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Preview":
        return cls(
            role=d.get("role", "poster"),
            seq=int(d.get("seq", 0)),
            path=d["path"],
            w=int(d.get("w", 0)),
            h=int(d.get("h", 0)),
            bytes=int(d.get("bytes", 0)),
            blurred=bool(d.get("blurred", False)),
        )


@dataclass
class Item:
    """One indexed source file."""

    id: str
    name: str
    ext: str
    kind: str
    mime: str
    size: int
    src_path: str

    date: str = ""                      # ISO 8601, the anchor on the timeline
    date_source: str = "mtime"
    date_candidates: dict[str, str] = field(default_factory=dict)

    title: str = ""
    summary: str = ""
    source: dict[str, Any] = field(default_factory=dict)   # url / domain / app

    previews: list[Preview] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)      # kind-specific

    content_key: str = ""
    indexed_at: str = ""
    error: str | None = None

    # -- convenience -------------------------------------------------------

    @property
    def day(self) -> str:
        """`YYYY-MM-DD` of the anchor date, or `""` if unknown."""
        return self.date[:10] if self.date else ""

    @property
    def poster(self) -> Preview | None:
        """The preview a viewer should lead with."""
        if not self.previews:
            return None
        for role in PREVIEW_ROLES:
            for p in self.previews:
                if p.role == role:
                    return p
        return self.previews[0]

    # -- boundary enforcement ---------------------------------------------

    def normalize(self) -> None:
        """Clamp everything an extractor wrote to the contract's limits.

        Called once, right after extraction, so the caps are visible in the
        manifest rather than applied invisibly at render time. Anything
        truncated says so, so a viewer can tell the difference between
        "short" and "shortened".
        """
        self.title = _clip(self.title, MAX_TITLE)
        self.summary = _clip(self.summary, MAX_SUMMARY)

        url = self.source.get("url")
        if isinstance(url, str) and len(url) > MAX_URL:
            self.source["url"] = url[:MAX_URL]
            self.source["url_truncated"] = True
        # A data: URI is a payload, not an address — there is nothing to
        # revisit, and it can be megabytes.
        if isinstance(url, str) and url.startswith("data:"):
            self.source["url"] = url.split(",", 1)[0][:120]
            self.source["url_is_data"] = True
        if isinstance(self.source.get("urls"), list):
            self.source["urls"] = [
                _clip(str(u), MAX_URL) for u in self.source["urls"][:5]
            ]

        # sanitize() first: a NaN must not survive in the in-memory item
        # either, because viewers build their payloads from that, not from
        # the file. Clamping alone would have let it straight through.
        self.meta = _clamp_mapping(sanitize(self.meta))
        self.source = sanitize(self.source)
        self.date_candidates = {
            k: str(v)[:40] for k, v in list(self.date_candidates.items())[:12]
        }

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Field order follows the declaration above; `Preview` recurses."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Item":
        return cls(
            id=d["id"],
            name=d["name"],
            ext=d.get("ext", ""),
            kind=d.get("kind", "other"),
            mime=d.get("mime", ""),
            size=int(d.get("size", 0)),
            src_path=d.get("src_path", ""),
            date=d.get("date", ""),
            date_source=d.get("date_source", "mtime"),
            date_candidates=dict(d.get("date_candidates") or {}),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            source=dict(d.get("source") or {}),
            previews=[Preview.from_dict(p) for p in d.get("previews") or []],
            meta=dict(d.get("meta") or {}),
            content_key=d.get("content_key", ""),
            indexed_at=d.get("indexed_at", ""),
            error=d.get("error"),
        )


@dataclass
class Manifest:
    """The whole index. Mutable during a run, then written once."""

    input: str = ""
    items: list[Item] = field(default_factory=list)
    generated_at: str = ""
    tool_version: str = __version__
    schema: int = SCHEMA_VERSION

    # -- lookup ------------------------------------------------------------

    def by_id(self) -> dict[str, Item]:
        return {it.id: it for it in self.items}

    def sorted_items(self, newest_first: bool = True) -> list[Item]:
        return sorted(
            self.items, key=lambda i: (i.date or "", i.name), reverse=newest_first
        )

    # -- derived aggregates ------------------------------------------------

    def duplicate_groups(self) -> list[list[Item]]:
        """Items sharing a content_key, as groups of two or more."""
        groups: dict[str, list[Item]] = {}
        for it in self.items:
            if it.content_key:
                groups.setdefault(it.content_key, []).append(it)
        return [g for g in groups.values() if len(g) > 1]

    def build_by_day(self) -> dict[str, dict[str, Any]]:
        """Per-day rollup — the HTML heatmap reads this directly."""
        days: dict[str, dict[str, Any]] = {}
        for it in self.sorted_items(newest_first=False):
            if not it.day:
                continue
            d = days.setdefault(
                it.day, {"count": 0, "bytes": 0, "kinds": {}, "ids": []}
            )
            d["count"] += 1
            d["bytes"] += it.size
            d["kinds"][it.kind] = d["kinds"].get(it.kind, 0) + 1
            d["ids"].append(it.id)
        return days

    def build_stats(self) -> dict[str, Any]:
        days = sorted(d for d in (i.day for i in self.items) if d)
        return {
            "items": len(self.items),
            "previews": sum(len(i.previews) for i in self.items),
            "bytes": sum(i.size for i in self.items),
            "date_range": [days[0], days[-1]] if days else [],
            "duplicates": sum(len(g) - 1 for g in self.duplicate_groups()),
            "errors": sum(1 for i in self.items if i.error),
            "kinds": _count_by(i.kind for i in self.items),
            "apps": _count_by(
                i.source.get("app") for i in self.items if i.source.get("app")
            ),
            "domains": _count_by(
                i.source.get("domain") for i in self.items if i.source.get("domain")
            ),
        }

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at or datetime.now().isoformat(timespec="seconds"),
            "tool_version": self.tool_version,
            "input": self.input,
            "stats": self.build_stats(),
            "by_day": self.build_by_day(),
            "items": [i.to_dict() for i in self.sorted_items()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        schema = int(d.get("schema", 0))
        if schema > SCHEMA_VERSION:
            raise ValueError(
                f"index.json is schema {schema}, but this tool understands "
                f"up to {SCHEMA_VERSION}. Upgrade the tool, or re-index."
            )
        return cls(
            input=d.get("input", ""),
            items=[Item.from_dict(i) for i in d.get("items") or []],
            generated_at=d.get("generated_at", ""),
            tool_version=d.get("tool_version", ""),
            schema=schema or SCHEMA_VERSION,
        )


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _clamp_mapping(meta: dict[str, Any]) -> dict[str, Any]:
    """Bound a kind-specific metadata dict in width, depth and value size."""
    out: dict[str, Any] = {}
    for key, value in list(meta.items())[:MAX_META_KEYS]:
        if isinstance(value, str):
            out[key] = _clip(value, MAX_META_VALUE)
        elif isinstance(value, (list, tuple)):
            out[key] = [
                _clip(str(v), MAX_META_VALUE) if isinstance(v, str) else v
                for v in list(value)[:MAX_META_LIST]
            ]
        elif isinstance(value, dict):
            out[key] = {
                str(k): _clip(str(v), MAX_META_VALUE)
                for k, v in list(value.items())[:MAX_META_LIST]
            }
        else:
            out[key] = value
    return out


def _count_by(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        if v:
            out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# -- disk I/O --------------------------------------------------------------


def sanitize(value: Any) -> Any:
    """Make a value safe for strict JSON.

    Python writes `NaN` and `Infinity` into JSON without complaint, and every
    other reader in the world rejects them. Two photos with an unreadable
    EXIF GPS rational once produced `"gps":[NaN,NaN]`, which made
    `JSON.parse` throw and blanked a 257-entry viewer. Extractors handle
    untrusted files, so the manifest cannot assume they always behave: the
    boundary is enforced here, where everything funnels through.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value


def load(path: Path) -> Manifest:
    """Read a manifest. A missing or unreadable file yields an empty one."""
    if not path.exists():
        return Manifest()
    try:
        with path.open("r", encoding="utf-8") as fh:
            # parse_constant fires on NaN/Infinity, healing a manifest an
            # older build may have written.
            data = json.load(fh, parse_constant=lambda _: None)
        return Manifest.from_dict(sanitize(data))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"{path} is not a readable manifest: {exc}") from exc


def save(manifest: Manifest, path: Path) -> None:
    """Write atomically, and only ever write strict JSON.

    `allow_nan=False` turns a bad float into a loud failure here rather than
    a silently broken viewer later; `sanitize` means it should never fire.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = sanitize(manifest.to_dict())
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.write("\n")
    tmp.replace(path)

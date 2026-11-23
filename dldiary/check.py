"""`dldiary check` — validate the manifest and everything it points at.

The manifest is a contract between one producer and several consumers. When
nothing enforces it, a single bad value can take the whole thing down
quietly: two photos with an unreadable EXIF GPS rational once wrote
`"gps":[NaN,NaN]`, which Python emits happily and `JSON.parse` refuses, and
a 257-entry page rendered blank with no error anywhere.

So this runs standalone, reads only what is on disk, and says plainly what
is wrong. Errors mean a viewer will misbehave; warnings mean something is
odd but survivable.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, human_size
from .manifest import (
    DATE_SOURCES, MAX_META_KEYS, MAX_SUMMARY, MAX_TITLE, MAX_URL,
    PREVIEW_ROLES, SCHEMA_VERSION,
)

# Above this, the embedded payload makes the page slow to parse on open.
PAYLOAD_WARN_BYTES = 600 * 1024

# How many of each to print before summarising the rest.
ERROR_LIMIT = 40
WARNING_LIMIT = 20


@dataclass
class Problem:
    level: str        # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        mark = "✗" if self.level == "error" else "!"
        return f"  {mark} {self.where}: {self.message}"


def run(cfg: Config) -> list[Problem]:
    """Check the manifest, the previews and the rendered viewers."""
    problems: list[Problem] = []
    path = cfg.manifest_path

    if not path.exists():
        return [Problem("error", "index.json",
                        f"not found at {path} — run `dldiary index` first")]

    raw = path.read_text(encoding="utf-8")
    data = _check_strict_json(raw, problems)
    if data is None:
        return problems

    problems += _check_envelope(data)
    problems += _check_items(data, cfg)
    problems += _check_blur(data, cfg)
    problems += _check_aggregates(data)
    problems += _check_orphan_previews(data, cfg)
    problems += _check_viewers(data, cfg)
    return problems


# -- the JSON itself --------------------------------------------------------


def _check_strict_json(raw: str, problems: list[Problem]) -> dict | None:
    """Parse the way a browser would, not the way Python likes to."""
    bad: list[str] = []
    try:
        data = json.loads(raw, parse_constant=bad.append)
    except json.JSONDecodeError as exc:
        problems.append(Problem("error", "index.json", f"not valid JSON: {exc}"))
        return None

    if bad:
        problems.append(Problem(
            "error", "index.json",
            f"contains {len(bad)} non-standard token(s) ({', '.join(sorted(set(bad)))}) "
            f"— JSON.parse will reject the file and every viewer will be blank",
        ))
    if not isinstance(data, dict):
        problems.append(Problem("error", "index.json", "top level is not an object"))
        return None
    return data


def _check_envelope(data: dict) -> list[Problem]:
    out: list[Problem] = []
    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        out.append(Problem(
            "warning" if isinstance(schema, int) else "error", "index.json",
            f"schema is {schema!r}, this build writes {SCHEMA_VERSION}",
        ))
    for key in ("items", "stats", "by_day"):
        if key not in data:
            out.append(Problem("error", "index.json", f"missing {key!r}"))
    if not isinstance(data.get("items"), list):
        out.append(Problem("error", "index.json", "items is not a list"))
    return out


# -- items ------------------------------------------------------------------

_REQUIRED = {
    "id": str, "name": str, "kind": str, "size": int,
    "previews": list, "meta": dict, "source": dict,
}


def _check_items(data: dict, cfg: Config) -> list[Problem]:
    out: list[Problem] = []
    items = data.get("items") or []
    seen_ids: dict[str, str] = {}

    for item in items:
        if not isinstance(item, dict):
            out.append(Problem("error", "items", "entry is not an object"))
            continue
        where = item.get("name") or item.get("id") or "?"

        for key, expected in _REQUIRED.items():
            value = item.get(key)
            if value is None:
                out.append(Problem("error", where, f"missing {key!r}"))
            elif not isinstance(value, expected):
                out.append(Problem(
                    "error", where,
                    f"{key!r} is {type(value).__name__}, expected "
                    f"{expected.__name__}",
                ))

        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in seen_ids:
                out.append(Problem(
                    "error", where,
                    f"duplicate id {item_id} (also {seen_ids[item_id]})",
                ))
            seen_ids[item_id] = str(where)

        out += _check_dates(item, where)
        out += _check_caps(item, where)
        out += _check_previews(item, where, cfg)

    return out


def _check_dates(item: dict, where: str) -> list[Problem]:
    out: list[Problem] = []
    date = item.get("date") or ""
    source = item.get("date_source") or ""
    if not date:
        out.append(Problem("warning", where, "no anchor date"))
    elif date and not re.match(r"^\d{4}-\d{2}-\d{2}", date):
        out.append(Problem("error", where, f"date {date!r} is not ISO 8601"))
    if source and source not in DATE_SOURCES and source != "unknown":
        out.append(Problem("error", where, f"unknown date_source {source!r}"))
    return out


def _check_caps(item: dict, where: str) -> list[Problem]:
    out: list[Problem] = []
    url = (item.get("source") or {}).get("url", "")
    if isinstance(url, str) and len(url) > MAX_URL:
        out.append(Problem("error", where,
                           f"source url is {len(url)} chars, cap is {MAX_URL}"))
    if len(item.get("summary") or "") > MAX_SUMMARY + 1:
        out.append(Problem("error", where, "summary over the cap"))
    if len(item.get("title") or "") > MAX_TITLE + 1:
        out.append(Problem("error", where, "title over the cap"))
    meta = item.get("meta") or {}
    if isinstance(meta, dict) and len(meta) > MAX_META_KEYS:
        out.append(Problem("error", where,
                           f"meta has {len(meta)} keys, cap is {MAX_META_KEYS}"))
    for key, value in (meta.items() if isinstance(meta, dict) else []):
        if isinstance(value, float) and not math.isfinite(value):
            out.append(Problem("error", where, f"meta.{key} is {value}"))
    return out


def _check_previews(item: dict, where: str, cfg: Config) -> list[Problem]:
    out: list[Problem] = []
    previews = item.get("previews")
    if not isinstance(previews, list):
        return out

    if not previews:
        out.append(Problem("warning", where, "no previews"))

    for preview in previews:
        if not isinstance(preview, dict) or "path" not in preview:
            out.append(Problem("error", where, "malformed preview entry"))
            continue
        rel = preview["path"]
        if not isinstance(rel, str) or not rel.startswith("previews/"):
            out.append(Problem("error", where, f"preview outside previews/: {rel}"))
            continue
        if preview.get("role") not in PREVIEW_ROLES:
            out.append(Problem("warning", where,
                               f"unknown preview role {preview.get('role')!r}"))

        path = cfg.output / rel
        if not path.exists():
            out.append(Problem("error", where, f"preview file missing: {rel}"))
            continue
        out += _check_image(path, preview, where, rel)
    return out


def _check_image(path: Path, preview: dict, where: str,
                 rel: str) -> list[Problem]:
    try:
        from PIL import Image
    except ImportError:
        return []
    try:
        with Image.open(path) as img:
            size = (img.width, img.height)
    except Exception as exc:
        return [Problem("error", where, f"preview unreadable ({rel}): {exc}")]

    recorded = (preview.get("w"), preview.get("h"))
    if recorded != size and all(recorded):
        return [Problem("error", where,
                        f"preview {rel} is {size[0]}×{size[1]}, manifest says "
                        f"{recorded[0]}×{recorded[1]}")]
    return []


# -- aggregates and leftovers -----------------------------------------------


def _check_blur(data: dict, cfg: Config) -> list[Problem]:
    """Every preview of a blurred kind must actually be marked blurred.

    Catches an extractor that wrote a preview without going through
    `Context.save_preview` — the failure mode would otherwise be a sharp
    ticket sitting in a diary you believed was private.
    """
    if not cfg.blur_kinds:
        return []

    out: list[Problem] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict) or item.get("kind") not in cfg.blur_kinds:
            continue
        where = item.get("name") or item.get("id") or "?"

        sharp = [p.get("path") for p in item.get("previews") or []
                 if isinstance(p, dict) and not p.get("blurred")]
        if sharp:
            out.append(Problem(
                "error", where,
                f"{len(sharp)} preview(s) not blurred although --blur-pdf "
                f"covers {item['kind']}: {sharp[0]}",
            ))
        if item.get("summary"):
            out.append(Problem("error", where,
                               "text stored for a blurred kind"))
    return out


def _check_aggregates(data: dict) -> list[Problem]:
    out: list[Problem] = []
    items = data.get("items") or []
    by_day = data.get("by_day") or {}
    stats = data.get("stats") or {}

    if stats.get("items") not in (None, len(items)):
        out.append(Problem("error", "stats",
                           f"says {stats['items']} items, file has {len(items)}"))

    counted = sum(d.get("count", 0) for d in by_day.values()
                  if isinstance(d, dict))
    dated = sum(1 for i in items if isinstance(i, dict) and i.get("date"))
    if counted != dated:
        out.append(Problem("error", "by_day",
                           f"counts {counted} entries, {dated} items have dates"))

    known = {i.get("id") for i in items if isinstance(i, dict)}
    for day, bucket in by_day.items():
        for item_id in (bucket or {}).get("ids", []):
            if item_id not in known:
                out.append(Problem("error", f"by_day[{day}]",
                                   f"references unknown id {item_id}"))
    return out


def _check_orphan_previews(data: dict, cfg: Config) -> list[Problem]:
    """Preview files no item points at — wasted disk from an older run."""
    if not cfg.previews_dir.is_dir():
        return []
    referenced = {
        Path(p["path"]).name
        for i in data.get("items") or []
        for p in (i.get("previews") or [])
        if isinstance(p, dict) and isinstance(p.get("path"), str)
    }
    orphans = [p for p in cfg.previews_dir.glob("*.webp")
               if p.name not in referenced]
    if not orphans:
        return []
    wasted = sum(p.stat().st_size for p in orphans)
    return [Problem("warning", "previews/",
                    f"{len(orphans)} orphaned preview(s), {human_size(wasted)} "
                    f"— the next index run clears them")]


def _check_viewers(data: dict, cfg: Config) -> list[Problem]:
    """Did each viewer actually represent every entry?"""
    out: list[Problem] = []
    items = data.get("items") or []

    notes_dir = cfg.obsidian_dir / "items"
    if notes_dir.is_dir():
        notes = list(notes_dir.glob("*.md"))
        days = len({i.get("date", "")[:10] for i in items if i.get("date")})
        if len(notes) not in (len(items), days):
            out.append(Problem(
                "error", "obsidian/items",
                f"{len(notes)} notes for {len(items)} entries "
                f"({days} days) — entries have been lost",
            ))

    page = cfg.html_dir / "diary.html"
    if page.exists():
        size = page.stat().st_size
        if size > PAYLOAD_WARN_BYTES:
            out.append(Problem(
                "warning", "html/diary.html",
                f"{human_size(size)} — large enough to be slow to open",
            ))
    return out


# -- reporting --------------------------------------------------------------


def _print_capped(problems: list[Problem], limit: int, label: str) -> None:
    """Print at most `limit` problems, then say how many were left out."""
    for problem in problems[:limit]:
        print(str(problem))
    if len(problems) > limit:
        print(f"  … and {len(problems) - limit} more {label}s")


def report(problems: list[Problem], verbose: bool = False) -> int:
    """Print the findings. Returns a process exit code."""
    errors = [p for p in problems if p.level == "error"]
    warnings = [p for p in problems if p.level == "warning"]

    _print_capped(errors, ERROR_LIMIT, "error")
    # Beside a real error the warnings are noise, so they are held back
    # unless there is no error to drown them — or -v asked for everything.
    if verbose or not errors:
        _print_capped(warnings, WARNING_LIMIT, "warning")

    if errors:
        print(f"\n  {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    if warnings:
        print(f"\n  ok, with {len(warnings)} warning(s)")
        return 0
    print("\n  ok — manifest, previews and viewers all consistent")
    return 0

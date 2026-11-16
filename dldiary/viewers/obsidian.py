"""The Obsidian viewer: a vault folder projected from the manifest.

Obsidian's Bases can see every file in a vault, but **note properties exist
only on Markdown**, and the cards view takes its cover image from a property
holding a wikilink. So a gallery needs one note per item — the notes are a
generated projection of `index.json`, not the data model, and are rewritten
on every render.

Notes are pure output: the whole folder is rewritten on every render, so
nothing hand-edited inside it survives.

Layout:

    obsidian/
      Downloads Diary.base      table / cards / by-month views
      previews/                 hardlinked from ../previews/
      items/2026-10-24 ticketdirect-1.md
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from ..config import Config, human_size
from ..manifest import Item, Manifest
from . import assets

ITEMS_DIR = "items"
PREVIEWS_DIR = "previews"
BASE_NAME = "Downloads Diary.base"

# Characters Obsidian will not accept in a note filename.
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')


def render(manifest: Manifest, cfg: Config) -> Path:
    """Write the vault folder and return its path."""
    vault = cfg.obsidian_dir
    items_dir = vault / ITEMS_DIR
    items_dir.mkdir(parents=True, exist_ok=True)

    # Previews must live inside the vault for Obsidian to embed them.
    referenced = [p.path for item in manifest.items for p in item.previews]
    mapping = assets.mirror_previews(
        referenced, cfg.output, vault / PREVIEWS_DIR, prefix=f"{PREVIEWS_DIR}/"
    )
    assets.prune(vault / PREVIEWS_DIR, {Path(p).name for p in referenced})

    if cfg.obsidian_grain == "day":
        written = _write_day_notes(manifest, items_dir, mapping)
    else:
        written = _write_item_notes(manifest, items_dir, mapping)

    _prune_notes(items_dir, written)
    (vault / BASE_NAME).write_text(_base_file(cfg.obsidian_grain),
                                   encoding="utf-8")
    return vault


# -- notes ------------------------------------------------------------------


def _write_item_notes(manifest: Manifest, items_dir: Path,
                      mapping: dict[str, str]) -> set[str]:
    items = manifest.sorted_items(newest_first=True)
    names = _unique_names(items)

    written: set[str] = set()
    for item in items:
        name = names[item.id]
        (items_dir / name).write_text(
            _item_note(item, mapping), encoding="utf-8"
        )
        written.add(name)

    # A viewer that turns 257 entries into 254 notes has lost data. That
    # used to happen silently when two files shared a day and a stem
    # (`beatles.png` and `beatles.omr`); it must never be silent again.
    if len(written) != len(items):
        raise RuntimeError(
            f"note name collision: {len(items)} entries produced "
            f"{len(written)} notes"
        )
    return written


def _unique_names(items: list[Item]) -> dict[str, str]:
    """One note filename per item, guaranteed distinct.

    The extension is part of the name because `beatles.png` and
    `beatles.omr` are two different events on the same day. If a clash
    somehow survives that, the item id settles it.
    """
    taken: set[str] = set()
    out: dict[str, str] = {}
    for item in sorted(items, key=lambda i: (i.day, i.name, i.id)):
        base = _note_stem(item)
        name = f"{base}.md"
        if name.lower() in taken:
            name = f"{base} [{item.id[:6]}].md"
        taken.add(name.lower())
        out[item.id] = name
    return out


def _write_day_notes(manifest: Manifest, items_dir: Path,
                     mapping: dict[str, str]) -> set[str]:
    by_day: dict[str, list[Item]] = defaultdict(list)
    for item in manifest.sorted_items(newest_first=True):
        by_day[item.day or "undated"].append(item)

    written: set[str] = set()
    for day, items in by_day.items():
        name = f"{day}.md"
        (items_dir / name).write_text(
            _day_note(day, items, mapping), encoding="utf-8"
        )
        written.add(name)

    if len(written) != len(by_day):
        raise RuntimeError(
            f"note name collision: {len(by_day)} days produced "
            f"{len(written)} notes"
        )
    return written


def _item_note(item: Item, mapping: dict[str, str]) -> str:
    poster = item.poster
    cover = mapping.get(poster.path, "") if poster else ""

    front: list[tuple[str, object]] = []
    if cover:
        front.append(("preview", f'"[[{cover}]]"'))
    if item.date:
        front.append(("date", item.date))
        front.append(("day", item.day))
    front += [
        ("date_source", item.date_source),
        ("kind", item.kind),
        ("ext", item.ext),
        ("size", item.size),
        ("size_human", _yaml_str(human_size(item.size))),
    ]
    if item.title and item.title != item.name:
        front.append(("doc_title", _yaml_str(item.title)))
    front.append(("filename", _yaml_str(item.name)))
    for key, prop in (("app", "source_app"), ("domain", "source_domain"),
                      ("url", "source_url")):
        value = item.source.get(key)
        if value:
            front.append((prop, _yaml_str(str(value))))
    for key in ("pages", "duration_human", "entries", "slides", "width",
                "height", "author", "artist", "album"):
        if item.meta.get(key) not in (None, ""):
            front.append((key, _yaml_scalar(item.meta[key])))
    if any(p.blurred for p in item.previews):
        front.append(("blurred", "true"))
    front.append(("tags", "[downloads-diary]"))

    lines = ["---"]
    lines += [f"{k}: {v}" for k, v in front]
    lines += ["---", ""]

    lines.append(f"# {item.title or item.name}")
    lines.append("")

    for preview in item.previews:
        target = mapping.get(preview.path)
        if target:
            lines.append(f"![[{target}]]")
    lines.append("")

    if item.summary:
        lines.append("> [!quote] Extracted text")
        for text_line in item.summary.split("\n"):
            lines.append(f"> {text_line}" if text_line else ">")
        lines.append("")

    lines += _details_table(item)
    lines.append("")
    lines.append(f"[Open original](file://{_uri(item.src_path)})")
    lines.append("")
    return "\n".join(lines)


def _day_note(day: str, items: list[Item], mapping: dict[str, str]) -> str:
    total = sum(i.size for i in items)
    kinds = sorted({i.kind for i in items})

    lines = [
        "---",
        f"date: {day}" if day != "undated" else "date:",
        f"count: {len(items)}",
        f"size: {total}",
        f"size_human: {_yaml_str(human_size(total))}",
        f"kinds: [{', '.join(kinds)}]",
        "tags: [downloads-diary, day]",
        "---",
        "",
        f"# {_pretty_day(day)}",
        "",
    ]
    for item in items:
        poster = item.poster
        target = mapping.get(poster.path, "") if poster else ""
        when = item.date[11:16] if len(item.date) >= 16 else ""
        lines.append(f"## {item.title or item.name}")
        if target:
            lines.append(f"![[{target}]]")
        facts = [f for f in (when, human_size(item.size),
                             item.source.get("domain", ""),
                             item.source.get("app", "")) if f]
        lines.append("*" + " · ".join(facts) + "*")
        if item.summary:
            lines.append("")
            lines.append("> " + item.summary.replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines)


def _details_table(item: Item) -> list[str]:
    rows: list[tuple[str, str]] = [
        ("File", f"`{item.name}`"),
        ("When", f"{item.date or 'unknown'} ({item.date_source})"),
        ("Size", human_size(item.size)),
        ("Kind", f"{item.kind}{' · ' + item.ext if item.ext else ''}"),
    ]
    if item.source.get("app"):
        rows.append(("Saved by", item.source["app"]))
    if item.source.get("url"):
        domain = item.source.get("domain", "source")
        rows.append(("From", f"[{domain}]({item.source['url']})"))
    for key, value in item.meta.items():
        if isinstance(value, (list, dict)) or value in (None, ""):
            continue
        if f"{key}_human" in item.meta:
            continue
        label = key.replace("_human", "").replace("_", " ").capitalize()
        rows.append((label, str(value)))
    if item.error:
        rows.append(("Error", f"`{item.error}`"))

    out = ["| | |", "|---|---|"]
    out += [f"| {label} | {value} |" for label, value in rows]
    return out


def _prune_notes(items_dir: Path, keep: set[str]) -> None:
    """Remove notes for entries that no longer exist in the manifest."""
    for path in items_dir.glob("*.md"):
        if path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass


# -- the .base file ---------------------------------------------------------


def _base_file(grain: str) -> str:
    """A Bases definition over the generated notes.

    Hand-written YAML. Every key used here was checked against Obsidian's own
    view parser rather than guessed: `sort` takes
    `[{property, direction: ASC|DESC}]`, and the cards view reads `image`
    (a property reference), `imageFit` ("contain", else cover),
    `imageAspectRatio` (number) and `cardSize` (number).
    """
    return _BASE_DAY if grain == "day" else _BASE_ITEM


_BASE_ITEM = """filters:
  and:
    - file.inFolder("items")
    - file.hasTag("downloads-diary")

formulas:
  when: 'date.format("DD MMM YYYY, HH:mm")'

properties:
  note.preview:
    displayName: Preview
  note.date:
    displayName: When
  note.doc_title:
    displayName: Title
  note.filename:
    displayName: File
  note.kind:
    displayName: Kind
  note.size_human:
    displayName: Size
  note.source_domain:
    displayName: Source
  note.source_app:
    displayName: App
  note.date_source:
    displayName: Date from
  formula.when:
    displayName: When

views:
  - type: cards
    name: Gallery
    image: note.preview
    imageAspectRatio: 1
    cardSize: 220
    order:
      - filename
      - formula.when
      - kind
      - size_human
      - source_domain
    sort:
      - property: date
        direction: DESC

  - type: table
    name: Everything
    order:
      - filename
      - doc_title
      - formula.when
      - kind
      - size_human
      - source_domain
      - source_app
      - date_source
    sort:
      - property: date
        direction: DESC

  - type: cards
    name: By source
    image: note.preview
    imageAspectRatio: 1
    groupBy:
      property: note.source_domain
      direction: ASC
    order:
      - filename
      - formula.when
      - kind
    sort:
      - property: date
        direction: DESC

  - type: table
    name: Check the date
    filters:
      or:
        - 'date_source == "mtime"'
        - 'date_source == "birth"'
        - 'date_source == "unknown"'
    order:
      - filename
      - formula.when
      - date_source
      - kind
      - source_domain
    sort:
      - property: date
        direction: DESC
"""

_BASE_DAY = """filters:
  and:
    - file.inFolder("items")
    - file.hasTag("day")

properties:
  note.date:
    displayName: Day
  note.count:
    displayName: Files
  note.size_human:
    displayName: Size
  note.kinds:
    displayName: Kinds

views:
  - type: table
    name: Days
    order:
      - date
      - count
      - size_human
      - kinds
    sort:
      - property: date
        direction: DESC

  - type: list
    name: Timeline
    order:
      - date
      - count
    sort:
      - property: date
        direction: DESC
"""


# -- helpers ----------------------------------------------------------------


def _note_stem(item: Item) -> str:
    """`2026-10-24 ticketdirect-1.pdf` — sortable, readable, distinct.

    The extension stays: two files with one stem on one day are two events.
    """
    stem = _UNSAFE.sub("-", Path(item.name).stem).strip(" .-")[:70]
    prefix = item.day or "undated"
    suffix = f".{item.ext}" if item.ext else ""
    return f"{prefix} {stem or item.id}{suffix}".replace("  ", " ")


def _pretty_day(day: str) -> str:
    if day == "undated":
        return "No date"
    try:
        return datetime.fromisoformat(day).strftime("%A, %-d %B %Y")
    except ValueError:
        return day


def _yaml_str(value: str) -> str:
    """Quote a YAML scalar safely."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_str(str(value))


def _uri(path: str) -> str:
    return quote(path)

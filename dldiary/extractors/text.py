"""Text, markup, data and source code — stdlib only.

These have no visual form, so the preview is a rendered card showing the
opening lines. For a saved HTML page the `<title>` is worth more than the
filename; for JSON and CSV the shape of the data is the interesting part.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from pathlib import Path

from .. import thumbs
from ..manifest import Item
from .base import Context, Extractor, clean_text, first_lines

READ_LIMIT = 256 * 1024          # enough for a summary; never load a huge log
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.I | re.S)


class TextExtractor(Extractor):
    name = "text"
    kinds = frozenset({"text", "code"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        raw, encoding = _read(path, READ_LIMIT)
        item.meta["encoding"] = encoding
        item.meta["lines"] = raw.count("\n") + (1 if raw else 0)
        if item.size > READ_LIMIT:
            item.meta["truncated"] = True

        body = raw
        if item.ext in ("html", "htm"):
            body = self._html(item, raw)
        elif item.ext in ("json", "jsonl"):
            body = self._json(item, raw)
        elif item.ext in ("csv", "tsv"):
            body = self._csv(item, raw)
        elif item.ext in ("md", "markdown"):
            self._markdown(item, raw)

        item.summary = clean_text(body)
        lines = first_lines(body or raw, count=18)
        img = thumbs.card_for_item(item, lines=lines, size=ctx.thumb_size,
                                   dense=True)
        thumbs.save(img, item, ctx.previews_dir, role="card", seq=0,
                    max_px=ctx.thumb_size)

    # -- per-format enrichment ---------------------------------------------

    def _html(self, item: Item, raw: str) -> str:
        m = _TITLE_RE.search(raw)
        if m:
            title = html.unescape(m.group(1)).strip()
            if title:
                item.title = title[:120]
        text = _TAG_RE.sub(" ", raw)
        return html.unescape(text)

    def _json(self, item: Item, raw: str) -> str:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            item.meta["valid_json"] = False
            return raw
        item.meta["valid_json"] = True
        if isinstance(data, dict):
            item.meta["json_keys"] = list(data)[:20]
            item.meta["json_shape"] = f"object, {len(data)} keys"
        elif isinstance(data, list):
            item.meta["json_shape"] = f"array, {len(data)} items"
        try:
            return json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return raw

    def _csv(self, item: Item, raw: str) -> str:
        delimiter = "\t" if item.ext == "tsv" else ","
        try:
            rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
        except csv.Error:
            return raw
        if not rows:
            return raw
        item.meta["columns"] = len(rows[0])
        item.meta["rows"] = len(rows) - 1
        header = [c.strip() for c in rows[0] if c.strip()]
        if header:
            item.meta["header"] = header[:20]
        width = max(1, 60 // max(1, min(len(rows[0]), 6)))
        return "\n".join(
            " | ".join(cell.strip()[:width] for cell in row[:6])
            for row in rows[:20]
        )

    def _markdown(self, item: Item, raw: str) -> None:
        for line in raw.split("\n"):
            if line.startswith("# "):
                item.title = line[2:].strip()[:120]
                return


def _read(path: Path, limit: int) -> tuple[str, str]:
    """Read text, trying UTF-8 first and falling back without ever raising."""
    data = path.read_bytes()[:limit]
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8 (lossy)"

"""Office documents — stdlib `zipfile` plus a little XML.

A .docx / .pptx / .xlsx is a ZIP of XML, so no third-party library is needed
for either half of the job:

* `docProps/thumbnail.jpeg` — PowerPoint and some Word documents embed a real
  rendered thumbnail. When it is there, it is the best possible preview.
* `docProps/core.xml` — the document's title and author.

Text comes from the document body XML. Legacy binary formats (.doc, .xls)
are not ZIPs and fall through to a card.
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree

from pathlib import Path

from .. import thumbs
from ..manifest import Item
from .base import Context, Extractor, clean_text, first_lines

_THUMBNAIL_NAMES = (
    "docProps/thumbnail.jpeg", "docProps/thumbnail.jpg",
    "docProps/thumbnail.png", "docProps/thumbnail.emf",
)

_CORE_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_TEXT_PARTS = {
    "docx": ("word/document.xml",),
    "pptx": None,      # every ppt/slides/slideN.xml, in order
    "xlsx": ("xl/sharedStrings.xml",),
}

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_XML_TAG = re.compile(r"<[^>]+>")
_PARA_BREAK = re.compile(
    r"</(w:p|a:p|w:tr)>|<w:br\b[^>]*/>", re.I
)


class OfficeExtractor(Extractor):
    name = "office"
    exts = frozenset({"docx", "pptx", "xlsx", "odt", "ods", "odp"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if not zipfile.is_zipfile(path):
            # Legacy .doc/.xls, or a corrupt file. A card still works.
            item.meta["format"] = "legacy binary"
            self._card(item, ctx, [])
            return

        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            self._core_properties(item, zf, names)
            text = self._text(item, zf, names)
            embedded = self._thumbnail(item, zf, names, ctx)

        item.summary = clean_text(text)
        if not embedded:
            self._card(item, ctx, first_lines(text, count=16))

    # -- parts --------------------------------------------------------------

    def _thumbnail(self, item: Item, zf: zipfile.ZipFile, names: set[str],
                   ctx: Context) -> bool:
        """Use the document's own rendered thumbnail when it has one."""
        for candidate in _THUMBNAIL_NAMES:
            if candidate not in names:
                continue
            try:
                data = zf.read(candidate)
            except (KeyError, zipfile.BadZipFile, OSError):
                continue
            preview = ctx.save_preview_bytes(
                data, item, role="poster", seq=0
            )
            if preview:
                item.meta["embedded_thumbnail"] = True
                return True
        return False

    def _core_properties(self, item: Item, zf: zipfile.ZipFile,
                         names: set[str]) -> None:
        if "docProps/core.xml" not in names:
            return
        try:
            root = ElementTree.fromstring(zf.read("docProps/core.xml"))
        except (ElementTree.ParseError, KeyError, OSError):
            return

        def text_of(tag: str) -> str:
            node = root.find(tag, _CORE_NS)
            return (node.text or "").strip() if node is not None else ""

        title = text_of("dc:title")
        if title:
            item.title = title[:120]
        for tag, key in (("dc:creator", "author"),
                         ("cp:lastModifiedBy", "last_modified_by"),
                         ("dc:subject", "subject")):
            value = text_of(tag)
            if value:
                item.meta[key] = value

    def _text(self, item: Item, zf: zipfile.ZipFile, names: set[str]) -> str:
        parts = _TEXT_PARTS.get(item.ext)
        if parts is None:   # pptx: walk the slides in order
            slides = sorted(
                (n for n in names if _SLIDE_RE.match(n)),
                key=lambda n: int(_SLIDE_RE.match(n).group(1)),  # type: ignore[union-attr]
            )
            item.meta["slides"] = len(slides)
            parts = tuple(slides[:30])

        chunks: list[str] = []
        for part in parts:
            if part not in names:
                continue
            try:
                chunks.append(_xml_to_text(zf.read(part)))
            except (KeyError, zipfile.BadZipFile, OSError):
                continue
        return "\n".join(c for c in chunks if c)

    def _card(self, item: Item, ctx: Context, lines: list[str]) -> None:
        img = thumbs.card_for_item(item, lines=lines or None,
                                   size=ctx.thumb_size, dense=True)
        ctx.save_preview(img, item, role="card", seq=0)


def _xml_to_text(data: bytes) -> str:
    """Strip XML to readable text, keeping paragraph breaks."""
    xml = data.decode("utf-8", "replace")
    xml = _PARA_BREAK.sub("\n", xml)
    text = _XML_TAG.sub("", xml)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(entity, char)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())

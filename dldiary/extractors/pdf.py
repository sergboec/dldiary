"""PDFs — PyMuPDF.

The single highest-value extractor here: 86 of 266 files in the real folder
are PDFs, and they are almost all tickets, invoices and bookings. Their text
is what turns a filename like `31-10-2026--Ticket-01--28302466111-1.pdf` into
"Jewgeni Onegin, Schillertheater Berlin, 31.10.2026, 19:30".

Produces a poster of page 1, a few more page renders for context, the opening
text as a summary, and the document metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import dates, thumbs
from ..manifest import Item
from .base import (
    Context, Extractor, MissingDependency, clean_text, looks_like_prose,
)

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]

MAX_PAGE_PREVIEWS = 3          # beyond the poster
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?")


class PdfExtractor(Extractor):
    name = "pdf"
    kinds = frozenset({"pdf"})
    exts = frozenset({"epub", "xps", "cbz", "fb2"})   # PyMuPDF opens these too

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if pymupdf is None:
            raise MissingDependency("pymupdf", "pymupdf==1.28.2")
        if Image is None:
            raise MissingDependency("pillow", "pillow==12.3.0")

        # When the previews are being blurred, nothing read off the page may
        # be kept. The summary and the title both come from the same text
        # the blur is there to hide — "Jewgeni Onegin" and "Sergei Boitsov"
        # are first lines lifted straight off these tickets — so storing
        # them would make the blur decorative.
        private = ctx.suppress_text(item)
        below = None if private else ctx.cfg.blur_text_below

        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                # Encrypted: no text, no render. Still a diary entry.
                item.meta["encrypted"] = True
                if not private:
                    item.summary = "Password-protected document."
                return

            item.meta["pages"] = doc.page_count
            self._read_metadata(item, doc, private=private)

            text_parts: list[str] = []
            for index in range(min(doc.page_count, MAX_PAGE_PREVIEWS + 1)):
                page = doc.load_page(index)
                if index == 0:
                    item.meta["page_width"] = round(page.rect.width)
                    item.meta["page_height"] = round(page.rect.height)
                self._render(item, page, ctx,
                             role="poster" if index == 0 else "page", seq=index)
                if private:
                    continue
                try:
                    # Text that is blurred in the image must not be stored
                    # in the manifest either, or the blur is decorative.
                    if below:
                        text_parts.append(_large_text(page, below))
                    else:
                        text_parts.append(page.get_text())
                except Exception:
                    pass

            if private:
                item.meta["text_suppressed"] = True
                return
            if below:
                item.meta["text_below_pt_dropped"] = below

            body = "\n".join(text_parts)
            if not body.strip():
                item.meta["scanned"] = True   # image-only PDF, no text layer
            elif looks_like_prose(body):
                item.summary = clean_text(body)
                if not item.title:
                    item.title = self._title(body)
            else:
                # Barcode glyphs and similar. The page render is still good.
                item.meta["no_readable_text"] = True

    # -- helpers ------------------------------------------------------------

    def _render(self, item: Item, page, ctx: Context, *, role: str,
                seq: int) -> None:
        longest = max(page.rect.width, page.rect.height, 1)
        zoom = min(ctx.thumb_size / longest, 4.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        redacted = False
        below = ctx.cfg.blur_text_below
        if below:
            spans = _spans(page)
            if not spans:
                # No text layer at all — a scan, or text drawn as curves.
                # There is nothing to measure, so the safe reading of "hide
                # the fine print" is to hide the page.
                item.meta["blurred_whole_page"] = "no text layer to measure"
                img = img.filter(ImageFilter.GaussianBlur(
                    ctx.cfg.blur_radius or max(4, ctx.thumb_size // 42)))
                redacted = True
            else:
                rects = [_scale(s["bbox"], zoom) for s in spans
                         if s["size"] < below]
                img = thumbs.blur_regions(img, rects,
                                          _region_radius(below, zoom))
                redacted = bool(rects)

        preview = ctx.save_preview(img, item, role=role, seq=seq)
        if redacted and preview is not None:
            preview.blurred = True

    def _read_metadata(self, item: Item, doc, *, private: bool = False) -> None:
        meta = doc.metadata or {}

        # `author`, `subject`, `keywords` and the document title are
        # authored content and routinely hold a person's name. `creator`,
        # `producer` and `format` name the software, and the dates are
        # structural — those stay, and the dates still feed date resolution.
        keys = ("creator", "producer", "format") if private else (
            "author", "subject", "keywords", "creator", "producer", "format")
        for key in keys:
            value = (meta.get(key) or "").strip()
            if value:
                item.meta[key] = value

        for key, source in (("creationDate", "pdf_created"),
                            ("modDate", "pdf_modified")):
            parsed = self._parse_date(meta.get(key, ""))
            if parsed:
                item.date_candidates.setdefault(source, parsed)

        title = (meta.get("title") or "").strip()
        if title and not private and not _is_junk_title(title):
            item.title = title

    @staticmethod
    def _parse_date(raw: str) -> str:
        """PDF dates look like `D:20261024110143+02'00'`."""
        m = _PDF_DATE.match(raw or "")
        if not m:
            return ""
        parts = [int(p) if p else 0 for p in m.groups()]
        from datetime import datetime

        try:
            dt = datetime(parts[0], parts[1], parts[2],
                          parts[3], parts[4], parts[5])
        except ValueError:
            return ""
        return dt.isoformat(timespec="seconds") if dates.plausible(dt) else ""

    @staticmethod
    def _title(body: str) -> str:
        """First substantial line of the document, as a human title."""
        for raw in body.split("\n"):
            line = raw.strip()
            if not (4 <= len(line) <= 90):
                continue
            letters = sum(1 for c in line if c.isalpha())
            if letters >= 4 and letters / len(line) >= 0.5:
                return line
        return ""


def _large_text(page, below: float) -> str:
    """The page's text, minus every span set below `below` points.

    Line structure is kept, so what survives still reads as lines rather
    than as a bag of words.
    """
    out: list[str] = []
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        return ""
    for block in blocks:
        for line in block.get("lines", []):
            kept = [s.get("text", "") for s in line.get("spans", [])
                    if s.get("size", 0) >= below]
            text = " ".join(t.strip() for t in kept if t.strip())
            # Barcode fonts draw their bars as large glyphs, so a size
            # filter alone keeps lines like `Ë*Æ{#.N/{,.nÌ`.
            if text and not _is_glyph_soup(text):
                out.append(text)
    return "\n".join(out)


def _is_glyph_soup(line: str) -> bool:
    """True for a line that is symbols rather than language."""
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    odd = sum(1 for c in stripped
              if not (c.isalnum() or c.isspace() or c in ".,:;!?()[]-/&@€$£%+'\""))
    return odd / len(stripped) > 0.25


def _spans(page) -> list[dict]:
    """Every text span on the page, with its font size and bounding box."""
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        return []
    return [
        span
        for block in blocks
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]


def _scale(bbox, zoom: float) -> tuple[float, float, float, float]:
    """A PDF-point bounding box in rendered pixels."""
    x0, y0, x1, y1 = bbox
    return (x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom)


def _region_radius(below: float, zoom: float) -> float:
    """Enough blur to destroy text of the threshold size, and no more.

    Tied to how tall that text actually renders: a radius suited to a whole
    page would bleed far outside a six-pixel line of fine print and smear
    the large text next to it.
    """
    return max(1.8, below * zoom * 0.55)


def _is_junk_title(title: str) -> bool:
    """Reject the placeholder titles PDF generators leave behind."""
    low = title.lower().strip()
    if low in {"untitled", "document", "pdf-document", "microsoft word",
               "print", "report", "invoice", "no title", "datawindow",
               "untitled document", "title", "form", "new document"}:
        return True
    # "Microsoft Word - final_v3.docx" and friends.
    return low.startswith(("microsoft word -", "microsoft powerpoint -",
                           "microsoft excel -"))

"""Turning anything into a preview image.

Two jobs:

* `save` — take a Pillow image from an extractor, fit it to the thumbnail box,
  encode it as WebP and register it in the manifest.
* `render_card` — for things with no visual form (a Python file, a zip, a
  disk image), draw one: a titled card showing a few lines of content. A card
  is a real preview, not a placeholder, so the timeline never has holes.

Everything here is Pillow. No external binaries.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import human_size
from .manifest import Item, Preview

# -- look -------------------------------------------------------------------

# Muted, coherent, and distinct at thumbnail size. Each kind gets an accent
# used for the card's header band and its corner tag.
KIND_COLORS: dict[str, tuple[int, int, int]] = {
    "image":    (0x4F, 0x86, 0xC6),
    "video":    (0xB5, 0x5C, 0x8A),
    "audio":    (0x5A, 0x9E, 0x8F),
    "pdf":      (0xC2, 0x5B, 0x4E),
    "document": (0x4A, 0x72, 0xA8),
    "archive":  (0xA8, 0x82, 0x3C),
    "text":     (0x6B, 0x74, 0x84),
    "code":     (0x7A, 0x5C, 0xA8),
    "other":    (0x6E, 0x6E, 0x72),
}

CARD_BG = (0x1C, 0x1E, 0x22)
CARD_FG = (0xE6, 0xE8, 0xEC)
CARD_DIM = (0x93, 0x99, 0xA4)

WEBP_QUALITY = 82

# Fonts with real Unicode coverage. Many filenames here are Cyrillic, and
# Pillow's bundled default font does not cover it.
_SANS_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_MONO_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Courier.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, *, mono: bool = False) -> ImageFont.FreeTypeFont:
    """A TrueType font at `size`, falling back to Pillow's default."""
    key = ("mono" if mono else "sans", size)
    if key in _font_cache:
        return _font_cache[key]

    for candidate in (_MONO_CANDIDATES if mono else _SANS_CANDIDATES):
        if Path(candidate).exists():
            try:
                f = ImageFont.truetype(candidate, size)
                _font_cache[key] = f
                return f
            except OSError:
                continue

    f = ImageFont.load_default(size=size)
    _font_cache[key] = f
    return f


# -- writing previews -------------------------------------------------------


def _flatten(img: Image.Image) -> Image.Image:
    """Composite transparency onto the card background.

    WebP supports alpha, but a transparent PNG over an unknown viewer
    background reads as broken. A known backdrop always looks deliberate.
    """
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, CARD_BG)
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def save(
    img: Image.Image,
    item: Item,
    previews_dir: Path,
    *,
    role: str = "poster",
    seq: int = 0,
    max_px: int = 512,
) -> Preview:
    """Fit, encode and register one preview. Returns the manifest entry."""
    img = _flatten(img)
    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)

    name = f"{item.id}-{role[0]}{seq}.webp"
    previews_dir.mkdir(parents=True, exist_ok=True)
    out = previews_dir / name
    img.save(out, "WEBP", quality=WEBP_QUALITY, method=4)

    preview = Preview(
        role=role, seq=seq, path=f"previews/{name}",
        w=img.width, h=img.height, bytes=out.stat().st_size,
    )
    item.previews.append(preview)
    return preview


def save_bytes(
    data: bytes,
    item: Item,
    previews_dir: Path,
    *,
    role: str = "cover",
    seq: int = 0,
    max_px: int = 512,
) -> Preview | None:
    """Same, for an image already embedded in a file (cover art, docProps)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return save(img, item, previews_dir, role=role, seq=seq,
                        max_px=max_px)
    except Exception:
        return None


def open_oriented(path: Path) -> Image.Image:
    """Open an image and apply its EXIF orientation.

    Without this, every portrait phone photo lands sideways in the timeline.
    """
    img = Image.open(path)
    img.load()
    return ImageOps.exif_transpose(img) or img


# -- cards ------------------------------------------------------------------


def _wrap(text: str, draw: ImageDraw.ImageDraw, f: ImageFont.FreeTypeFont,
          width: int) -> list[str]:
    """Greedy wrap, breaking over-long words so nothing overflows the card."""
    out: list[str] = []
    for word in text.split():
        if not out:
            out.append(word)
            continue
        trial = f"{out[-1]} {word}"
        if draw.textlength(trial, font=f) <= width:
            out[-1] = trial
        else:
            out.append(word)
    # Hard-break any single token still too wide.
    fixed: list[str] = []
    for line in out:
        while draw.textlength(line, font=f) > width and len(line) > 1:
            cut = len(line)
            while cut > 1 and draw.textlength(line[:cut], font=f) > width:
                cut -= 1
            fixed.append(line[:cut])
            line = line[cut:]
        fixed.append(line)
    return fixed


def render_card(
    *,
    kind: str,
    tag: str,
    title: str,
    lines: list[str] | None = None,
    subtitle: str = "",
    size: int = 512,
    mono: bool = True,
    dense: bool = False,
) -> Image.Image:
    """Draw a preview card for something with no visual form.

    An accent band carries the file type, the title is the filename, and the
    body is whatever the extractor could read.

    `dense` decides the typography, and it matters more than it sounds. A
    512 px card displayed in a 170 px grid cell is scaled to a third, so
    11 px body text lands at under 4 px and reads as grey mush. Cards whose
    *content* is the point — source code, a config file — stay dense, where
    that texture is the signal and the lightbox shows it full size. Cards
    that merely stand in for a binary get large type instead, so the
    filename is legible in the grid where you actually look at it.
    """
    accent = KIND_COLORS.get(kind, KIND_COLORS["other"])
    img = Image.new("RGB", (size, size), CARD_BG)
    draw = ImageDraw.Draw(img)

    pad = max(16, size // 20)
    band_h = max(40, size // 9)

    # Accent band with the type tag.
    draw.rectangle([0, 0, size, band_h], fill=accent)
    tag_font = font(max(13, band_h // 3), mono=False)
    draw.text((pad, band_h // 2), tag.upper(), font=tag_font,
              fill=(0xFF, 0xFF, 0xFF), anchor="lm")
    if subtitle:
        draw.text((size - pad, band_h // 2), subtitle, font=tag_font,
                  fill=(0xFF, 0xFF, 0xFF), anchor="rm")

    y = band_h + pad

    title_px = max(15, size // 24) if dense else max(22, size // 13)
    body_px = max(11, size // 38) if dense else max(15, size // 25)
    max_title_lines = 3 if dense else 4

    title_font = font(title_px, mono=False)
    for line in _wrap(title, draw, title_font, size - 2 * pad)[:max_title_lines]:
        draw.text((pad, y), line, font=title_font, fill=CARD_FG)
        y += int(title_px * 1.3)

    y += pad // 2
    draw.line([(pad, y), (size - pad, y)], fill=(0x33, 0x37, 0x3E), width=1)
    y += pad // 2

    if lines:
        body_font = font(body_px, mono=mono)
        step = int(body_px * 1.45)
        for raw in lines:
            if y + step > size - pad // 2:
                break
            text = raw.replace("\t", "    ").rstrip()
            while draw.textlength(text, font=body_font) > size - 2 * pad and text:
                text = text[:-1]
            draw.text((pad, y), text, font=body_font, fill=CARD_DIM)
            y += step

    return img


def card_for_item(item: Item, lines: list[str] | None = None,
                  size: int = 512, dense: bool = False) -> Image.Image:
    """The standard card for an item, used by several extractors."""
    return render_card(
        kind=item.kind,
        tag=item.ext or item.kind,
        title=item.name,
        subtitle=human_size(item.size),
        lines=lines,
        size=size,
        dense=dense,
    )

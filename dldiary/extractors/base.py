"""Extractor protocol, registry and error isolation.

An extractor turns one file into previews, a summary and some metadata. It is
handed an `Item` and mutates it in place.

Two rules hold for every extractor, enforced here rather than in each one:

* **A failure is never fatal.** 266 files, one of them malformed, must still
  produce 266 diary entries. Exceptions are caught, recorded on the item, and
  replaced by a generic card.
* **Every item ends up with at least one preview.** A timeline with holes in
  it is worse than one with plain cards.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..manifest import Item
from .. import thumbs


class MissingDependency(RuntimeError):
    """An optional library this extractor needs is not installed."""

    def __init__(self, package: str, pin: str = ""):
        self.package = package
        super().__init__(
            f"{package} is not installed"
            + (f" (pip install {pin or package})" if pin or package else "")
        )


@dataclass
class Context:
    """Everything an extractor needs besides the file itself."""

    cfg: Config
    warned: set[str] = field(default_factory=set)

    @property
    def previews_dir(self) -> Path:
        return self.cfg.previews_dir

    @property
    def thumb_size(self) -> int:
        return self.cfg.thumb_size

    def warn_once(self, key: str, message: str) -> None:
        """Report a condition that will repeat, exactly once per run."""
        if key not in self.warned:
            self.warned.add(key)
            print(f"  ! {message}", file=sys.stderr)


class Extractor:
    """Base class. Subclasses set `name` and either `kinds` or `exts`."""

    name: str = "base"
    kinds: frozenset[str] = frozenset()
    exts: frozenset[str] = frozenset()

    def matches(self, item: Item) -> bool:
        return item.ext in self.exts or item.kind in self.kinds

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        raise NotImplementedError


# -- shared text helpers ----------------------------------------------------

_WS_RUN = re.compile(r"[ \t ]+")
_BLANK_RUN = re.compile(r"\n{3,}")
SUMMARY_LIMIT = 600


def clean_text(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Collapse the whitespace soup that text extraction produces."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


_TRIM = ".,:;!?()[]{}\"'«»–—-/\\|*#@…·"


def looks_like_prose(text: str, threshold: float = 0.6) -> bool:
    """Is this text, or is it the output of a barcode font?

    Tickets and invoices embed barcodes as glyph runs like `Ë*Æ{#.N/{,.nÌ`,
    which extract as text and would otherwise become the entry's summary and
    title. Real words survive having their punctuation stripped; barcode soup
    does not.
    """
    tokens = [t for t in text[:3000].split() if t]
    if len(tokens) < 3:
        return False
    wordish = sum(1 for t in tokens if t.strip(_TRIM).isalnum())
    return wordish / len(tokens) >= threshold


def first_lines(text: str, count: int = 18, width: int = 64) -> list[str]:
    """The opening lines of a document, trimmed for a card body."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.replace("\t", "    ").rstrip()
        if not line and not lines:
            continue  # skip a leading blank run
        lines.append(line[:width])
        if len(lines) >= count:
            break
    return lines


def title_from_name(name: str) -> str:
    """A readable title from a filename, when nothing better is available."""
    stem = Path(name).stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip(" -–—")
    return stem or name


# -- registry ---------------------------------------------------------------


def build_registry() -> list[Extractor]:
    """All extractors, most specific first. `generic` always matches last."""
    from .image import ImageExtractor
    from .pdf import PdfExtractor
    from .video import VideoExtractor
    from .audio import AudioExtractor
    from .office import OfficeExtractor
    from .archive import ArchiveExtractor
    from .text import TextExtractor
    from .generic import GenericExtractor

    return [
        ImageExtractor(),
        PdfExtractor(),
        VideoExtractor(),
        AudioExtractor(),
        OfficeExtractor(),
        ArchiveExtractor(),
        TextExtractor(),
        GenericExtractor(),
    ]


def run(item: Item, path: Path, ctx: Context,
        registry: list[Extractor]) -> None:
    """Extract one item, surviving anything the file throws at us."""
    if item.meta.get("skipped_extraction"):
        _ensure_preview(item, ctx, note=str(item.meta["skipped_extraction"]))
        if not item.title:
            item.title = title_from_name(item.name)
        return

    for extractor in registry:
        if not extractor.matches(item):
            continue
        try:
            extractor.extract(item, path, ctx)
        except MissingDependency as exc:
            ctx.warn_once(
                exc.package,
                f"{exc} — {item.kind} files get a plain card instead",
            )
            item.meta["degraded"] = exc.package
        except Exception as exc:  # deliberately broad: never abort the run
            item.error = f"{type(exc).__name__}: {exc}"
            if ctx.cfg.verbose:
                print(f"  ! {item.name}: {item.error}", file=sys.stderr)
        break

    _ensure_preview(item, ctx)
    if not item.title:
        item.title = title_from_name(item.name)
    # Everything above handled an untrusted file. This is the boundary.
    item.normalize()


def _ensure_preview(item: Item, ctx: Context, note: str | None = None) -> None:
    """Guarantee the invariant: no item leaves without a preview."""
    if item.previews:
        return
    lines = [t for t in (note, item.error) if t]
    img = thumbs.card_for_item(item, lines=lines or None, size=ctx.thumb_size)
    thumbs.save(img, item, ctx.previews_dir, role="card", seq=0,
                max_px=ctx.thumb_size)

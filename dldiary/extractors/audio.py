"""Audio — mutagen.

Embedded cover art makes the best preview; where there is none, the tags are
rendered onto a card so the timeline still reads as "a song", not "a file".
"""

from __future__ import annotations

from pathlib import Path

from .. import dates, thumbs
from ..config import human_duration
from ..manifest import Item
from .base import Context, Extractor, MissingDependency

try:
    import mutagen
except ImportError:  # pragma: no cover
    mutagen = None  # type: ignore[assignment]

# Tag names differ per container; try each in turn.
_TAG_ALIASES = {
    "title": ("title", "TIT2", "\xa9nam"),
    "artist": ("artist", "TPE1", "\xa9ART"),
    "album": ("album", "TALB", "\xa9alb"),
    "date": ("date", "TDRC", "\xa9day", "year"),
    "genre": ("genre", "TCON", "\xa9gen"),
    "track": ("tracknumber", "TRCK", "trkn"),
}


class AudioExtractor(Extractor):
    name = "audio"
    kinds = frozenset({"audio"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if mutagen is None:
            raise MissingDependency("mutagen", "mutagen==1.48.1")

        audio = mutagen.File(path)
        if audio is None:
            raise RuntimeError("unrecognised audio container")

        self._read_info(item, audio)
        self._read_tags(item, audio)

        if not self._cover(item, audio, ctx):
            self._card(item, ctx)

    # -- parts --------------------------------------------------------------

    def _read_info(self, item: Item, audio) -> None:
        info = getattr(audio, "info", None)
        if info is None:
            return
        length = getattr(info, "length", None)
        if length:
            item.meta["duration"] = round(float(length), 2)
            item.meta["duration_human"] = human_duration(float(length))
        for attr, key in (("bitrate", "bitrate"), ("sample_rate", "sample_rate"),
                          ("channels", "channels")):
            value = getattr(info, attr, None)
            if value:
                item.meta[key] = value
        item.meta["audio_format"] = type(audio).__name__

    def _read_tags(self, item: Item, audio) -> None:
        tags = getattr(audio, "tags", None)
        if not tags:
            return
        found: dict[str, str] = {}
        for key, aliases in _TAG_ALIASES.items():
            for alias in aliases:
                try:
                    value = tags.get(alias)
                except (TypeError, AttributeError):
                    value = None
                if value:
                    found[key] = _first_text(value)
                    break

        for key, value in found.items():
            if value:
                item.meta[key] = value

        if found.get("title"):
            artist = found.get("artist")
            item.title = f"{artist} — {found['title']}" if artist else found["title"]

        parsed = dates.parse_exif_datetime(found.get("date", ""))
        if parsed:
            dates.add_candidate(item, "media", parsed)

        bits = [found.get(k) for k in ("artist", "title", "album")]
        item.summary = " · ".join(b for b in bits if b)

    def _cover(self, item: Item, audio, ctx: Context) -> bool:
        for data in _cover_candidates(audio):
            if ctx.save_preview_bytes(data, item, role="cover", seq=0):
                item.meta["embedded_cover"] = True
                return True
        return False

    def _card(self, item: Item, ctx: Context) -> None:
        lines = []
        for key in ("artist", "album", "genre", "duration_human", "bitrate"):
            value = item.meta.get(key)
            if value:
                label = key.replace("_human", "").replace("_", " ")
                lines.append(f"{label:<10} {value}")
        img = thumbs.card_for_item(item, lines=lines or None,
                                   size=ctx.thumb_size)
        ctx.save_preview(img, item, role="card", seq=0)


def _cover_candidates(audio):
    """Yield raw image bytes from whichever tagging scheme is in use."""
    tags = getattr(audio, "tags", None)

    # ID3 (MP3)
    if tags is not None and hasattr(tags, "getall"):
        try:
            for frame in tags.getall("APIC"):
                if getattr(frame, "data", None):
                    yield frame.data
        except Exception:
            pass

    # MP4 / M4A
    if tags is not None:
        try:
            for art in tags.get("covr", []) or []:
                yield bytes(art)
        except Exception:
            pass

    # FLAC / OGG
    for picture in getattr(audio, "pictures", []) or []:
        if getattr(picture, "data", None):
            yield picture.data


def _first_text(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, tuple):          # MP4 track numbers: (3, 12)
        value = value[0]
    return str(value).strip()

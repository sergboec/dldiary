"""Video — PyAV (libav bindings; no ffmpeg CLI).

A poster frame about a third of the way in, plus a few frames spread across
the running time so the timeline shows what a clip actually contains rather
than its black first frame.

Frames are reached by seeking, not by decoding from the start — a 6 GB movie
costs the same as a 6 MB one.
"""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from pathlib import Path

from .. import dates, thumbs
from ..manifest import Item
from .base import Context, Extractor, MissingDependency

try:
    import av
    from av.error import FFmpegError
except ImportError:  # pragma: no cover
    av = None  # type: ignore[assignment]
    FFmpegError = Exception  # type: ignore[assignment,misc]

try:
    from PIL import ImageStat
except ImportError:  # pragma: no cover
    ImageStat = None  # type: ignore[assignment]

POSTER_AT = 0.30                 # fraction of the duration
FRAME_POSITIONS = (0.10, 0.50, 0.90)

# Seeking only ever lands on a keyframe, and keyframes can be minutes apart —
# in the test clip there are exactly two, and the first one is pure black. So
# decode forward from the keyframe to the requested moment, then keep going
# past anything blank. Both walks are bounded, so a pathological file cannot
# turn one thumbnail into a full decode.
MAX_DECODE = 400                 # frames walked while reaching the target
MAX_EVALUATE = 24                # candidate frames examined once there
MIN_STDDEV = 8.0                 # below this a frame is a fade or a blank

# Container tags that hold a creation date, in the order we trust them.
_DATE_TAGS = ("creation_time", "date", "com.apple.quicktime.creationdate")


class VideoExtractor(Extractor):
    name = "video"
    kinds = frozenset({"video"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if av is None:
            raise MissingDependency("av", "av==17.1.0")

        # Decoder chatter on damaged files is noise, not information.
        av.logging.set_level(av.logging.FATAL)

        # `metadata_errors="replace"` is load-bearing. PyAV decodes the
        # container's tag dictionary during open(), as UTF-8, and raises if
        # it cannot — so a film tagged in cp1251 by VirtualDub failed before
        # a single frame could be read. Mojibake in a title we may not even
        # use is a far smaller loss than no thumbnails at all.
        with av.open(str(path), metadata_errors="replace") as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                item.meta["no_video_stream"] = True
                self._read_container_meta(item, container)
                return

            duration = self._duration(container, stream)
            self._read_stream_meta(item, stream, duration)
            # Tags are a nice-to-have; frames are the point. A Russian AVI
            # with cp1251 bytes in its metadata made PyAV raise here and
            # cost the file its thumbnails entirely.
            try:
                self._read_container_meta(item, container)
            except Exception as exc:
                item.meta["metadata_unreadable"] = type(exc).__name__

            positions = [POSTER_AT, *FRAME_POSITIONS[:ctx.cfg.video_frames]]
            roles = ["poster", *["frame"] * (len(positions) - 1)]

            saved = 0
            seen: set[bytes] = set()
            for seq, (fraction, role) in enumerate(zip(positions, roles)):
                grabbed = self._grab(container, stream, duration, fraction)
                if grabbed is None:
                    continue
                img, signature = grabbed
                # A short clip can yield the same frame for two positions;
                # one copy is enough.
                if signature in seen:
                    continue
                seen.add(signature)
                thumbs.save(img, item, ctx.previews_dir,
                            role=role, seq=seq, max_px=ctx.thumb_size)
                saved += 1

            if saved == 0:
                raise RuntimeError("no decodable video frame")

    # -- frame grabbing -----------------------------------------------------

    def _grab(self, container, stream, duration: float | None,
              fraction: float):
        """Best frame at roughly `fraction` through, as `(image, signature)`.

        Returns the first non-blank frame at or after the target, or the most
        varied one seen while looking, or `None` if nothing decoded.
        """
        target = (duration or 0) * fraction
        if not self._seek(container, stream, target):
            return None

        best = None
        best_score = -1.0
        decoded = 0
        evaluated = 0

        try:
            for frame in container.decode(stream):
                decoded += 1
                if decoded > MAX_DECODE:
                    break

                when = frame.time if frame.time is not None else 0.0
                # Walk forward to the requested moment before judging anything.
                if target and when + 1e-3 < target and decoded < MAX_DECODE:
                    continue

                img = frame.to_image()
                score = _variation(img)
                if score > best_score:
                    best, best_score = img, score
                if score >= MIN_STDDEV:
                    break

                evaluated += 1
                if evaluated >= MAX_EVALUATE:
                    break
        except (FFmpegError, ValueError, RuntimeError, StopIteration):
            pass

        if best is None:
            return None
        return best, _signature(best)

    @staticmethod
    def _seek(container, stream, target: float) -> bool:
        try:
            if target > 0.1 and stream.time_base:
                offset = int(target / float(stream.time_base))
                container.seek(offset, stream=stream, backward=True)
            else:
                container.seek(0, stream=stream, backward=True)
            return True
        except (FFmpegError, ValueError, OverflowError, TypeError):
            try:
                container.seek(0)
                return True
            except Exception:
                return False

    # -- metadata -----------------------------------------------------------

    @staticmethod
    def _duration(container, stream) -> float | None:
        if container.duration:
            return float(container.duration) / av.time_base
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
        return None

    def _read_stream_meta(self, item: Item, stream, duration: float | None) -> None:
        ctx_ = stream.codec_context
        if ctx_ is not None:
            item.meta["width"] = ctx_.width
            item.meta["height"] = ctx_.height
            if ctx_.codec:
                item.meta["codec"] = ctx_.codec.name
        if duration:
            item.meta["duration"] = round(duration, 2)
            item.meta["duration_human"] = _hms(duration)
        rate = stream.average_rate or stream.guessed_rate
        if isinstance(rate, Fraction) and rate.denominator:
            item.meta["fps"] = round(float(rate), 2)
        if stream.frames:
            item.meta["frame_count"] = stream.frames

    def _read_container_meta(self, item: Item, container) -> None:
        tags = _safe_metadata(container)
        for key in ("title", "artist", "comment", "encoder", "major_brand"):
            value = (tags.get(key) or "").strip()
            if value and _readable(value):
                item.meta[key] = value
        title = (tags.get("title") or "").strip()
        if title and _readable(title):
            item.title = title

        for tag in _DATE_TAGS:
            raw = tags.get(tag)
            if not raw:
                continue
            parsed = dates.parse_exif_datetime(str(raw))
            if parsed:
                dates.add_candidate(item, "media", parsed)
                break

        try:
            item.meta["container"] = container.format.name
        except Exception:
            pass


def _readable(text: str) -> bool:
    """Reject strings that lost too much to a failed decode.

    Opening with `metadata_errors="replace"` trades an exception for U+FFFD,
    so `Идеальные дни` arrives as `?????????`. A title made mostly of
    replacement characters is worse than the filename.
    """
    if not text:
        return False
    return text.count("�") / len(text) < 0.2


def _safe_metadata(container) -> dict[str, str]:
    """Container tags, skipping any that are not decodable text.

    Media files carry tags in whatever encoding the muxer felt like; PyAV
    decodes as UTF-8 and raises on the rest. One unreadable tag should cost
    that tag, not the file.
    """
    try:
        raw = container.metadata
    except Exception:
        return {}
    out: dict[str, str] = {}
    try:
        keys = list(raw.keys())
    except Exception:
        return {}
    for key in keys:
        try:
            value = raw[key]
        except Exception:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            out[str(key)] = value
    return out


def _variation(img) -> float:
    """How much is going on in this frame. A fade or a blank scores ~0."""
    if ImageStat is None:
        return MIN_STDDEV
    try:
        small = img.convert("L").resize((64, 64))
        return float(ImageStat.Stat(small).stddev[0])
    except Exception:
        return MIN_STDDEV


def _signature(img) -> bytes:
    """Cheap perceptual fingerprint, for spotting repeated frames."""
    try:
        return img.convert("L").resize((16, 16)).tobytes()
    except Exception:
        return b""


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

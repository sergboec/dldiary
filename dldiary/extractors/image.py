"""Images — Pillow.

Beyond the thumbnail, images carry the best dates in the whole folder: a
photo's EXIF `DateTimeOriginal` is when the moment happened, which is what a
diary wants, rather than when the file was saved.

SVG has no raster form, so it is handed to PyMuPDF, which renders it.
"""

from __future__ import annotations

import math
from pathlib import Path

from .. import dates, thumbs
from ..kinds import VECTOR_IMAGE_EXTS
from ..manifest import Item
from .base import Context, Extractor, MissingDependency

try:
    from PIL import Image, ExifTags
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ExifTags = None  # type: ignore[assignment]

# EXIF tags worth keeping in the manifest, mapped to friendly names.
_EXIF_KEEP = {
    "Make": "camera_make",
    "Model": "camera_model",
    "LensModel": "lens",
    "FNumber": "aperture",
    "ExposureTime": "exposure",
    "ISOSpeedRatings": "iso",
    "FocalLength": "focal_length",
    "Software": "software",
}


class ImageExtractor(Extractor):
    name = "image"
    kinds = frozenset({"image"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if item.ext in VECTOR_IMAGE_EXTS:
            self._extract_vector(item, path, ctx)
            return
        if Image is None:
            raise MissingDependency("pillow", "pillow==12.3.0")

        with thumbs.open_oriented(path) as img:
            item.meta["width"], item.meta["height"] = img.width, img.height
            item.meta["mode"] = img.mode
            if getattr(img, "n_frames", 1) > 1:
                item.meta["frames"] = img.n_frames  # animated GIF/WebP
            self._read_exif(item, path)
            thumbs.save(img, item, ctx.previews_dir, role="poster", seq=0,
                        max_px=ctx.thumb_size)

    # -- EXIF ---------------------------------------------------------------

    def _read_exif(self, item: Item, path: Path) -> None:
        """Pull the capture date, camera and GPS out of EXIF.

        Re-opened rather than reusing the transposed image, because
        `exif_transpose` strips the orientation tag and may drop the block.
        """
        try:
            with Image.open(path) as raw:
                exif = raw.getexif()
        except Exception:
            return
        if not exif:
            return

        tag_name = {v: k for k, v in ExifTags.TAGS.items()}
        ifd = {}
        try:
            ifd = exif.get_ifd(ExifTags.IFD.Exif) or {}
        except Exception:
            pass

        def value_for(name: str):
            tid = tag_name.get(name)
            if tid is None:
                return None
            return ifd.get(tid, exif.get(tid))

        # Capture date: the Exif IFD original beats the generic DateTime.
        for field in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            raw_value = value_for(field)
            if raw_value:
                dt = dates.parse_exif_datetime(str(raw_value))
                if dt:
                    dates.add_candidate(item, "exif", dt)
                    break

        for field, key in _EXIF_KEEP.items():
            raw_value = value_for(field)
            if raw_value not in (None, "", b""):
                item.meta[key] = _simplify(raw_value)

        self._read_gps(item, exif)

    def _read_gps(self, item: Item, exif) -> None:
        try:
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
        except Exception:
            return
        if not gps:
            return
        names = {v: k for k, v in ExifTags.GPSTAGS.items()}

        def coord(value_tag: str, ref_tag: str) -> float | None:
            dms = gps.get(names.get(value_tag, -1))
            ref = gps.get(names.get(ref_tag, -1))
            if not dms or len(dms) != 3:
                return None
            try:
                deg = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
            except (TypeError, ValueError, ZeroDivisionError):
                return None
            # A 0/0 EXIF rational yields NaN rather than raising, and an
            # unset GPS block is all zeroes. Neither is a location.
            if not math.isfinite(deg) or deg == 0.0:
                return None
            if str(ref).upper() in ("S", "W"):
                deg = -deg
            return round(deg, 6)

        lat = coord("GPSLatitude", "GPSLatitudeRef")
        lon = coord("GPSLongitude", "GPSLongitudeRef")
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            item.meta["gps"] = [lat, lon]

    # -- SVG ----------------------------------------------------------------

    def _extract_vector(self, item: Item, path: Path, ctx: Context) -> None:
        try:
            import pymupdf
        except ImportError as exc:
            raise MissingDependency("pymupdf", "pymupdf==1.28.2") from exc

        with pymupdf.open(path) as doc:
            page = doc.load_page(0)
            item.meta["width"] = round(page.rect.width)
            item.meta["height"] = round(page.rect.height)
            zoom = ctx.thumb_size / max(page.rect.width, page.rect.height, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            thumbs.save(img, item, ctx.previews_dir, role="poster", seq=0,
                        max_px=ctx.thumb_size)


def _simplify(value):
    """EXIF values include rationals and bytes; make them JSON-friendly."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00").strip()
    if isinstance(value, tuple) and len(value) == 2:
        try:
            return round(value[0] / value[1], 4)
        except (TypeError, ZeroDivisionError):
            return str(value)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return round(float(value), 4)
        except (TypeError, ZeroDivisionError):
            return str(value)
    if isinstance(value, (int, float, str)):
        return value.strip() if isinstance(value, str) else value
    return str(value)

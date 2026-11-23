"""Images — Pillow.

The thumbnail is the point. The one thing worth reading out of EXIF besides
it is the location: "this photo was taken there" is a diary entry, while the
camera body, the aperture and the ISO are not.

SVG has no raster form, so it is handed to PyMuPDF, which renders it.
"""

from __future__ import annotations

import math
from pathlib import Path

from .. import thumbs
from ..kinds import VECTOR_IMAGE_EXTS
from ..manifest import Item
from .base import Context, Extractor, MissingDependency

try:
    from PIL import Image, ExifTags
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ExifTags = None  # type: ignore[assignment]

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
            self._read_location(item, path)
            ctx.save_preview(img, item, role="poster", seq=0)

    # -- EXIF ---------------------------------------------------------------

    def _read_location(self, item: Item, path: Path) -> None:
        """Pull the GPS coordinates out of EXIF, if the camera recorded any.

        Re-opened rather than reusing the transposed image, because
        `exif_transpose` strips the orientation tag and may drop the block.
        """
        try:
            with Image.open(path) as raw:
                gps = raw.getexif().get_ifd(ExifTags.IFD.GPSInfo) or {}
        except Exception:
            return
        if not gps:
            return

        def coord(value_tag: str, ref_tag: str) -> float | None:
            # Pillow names every GPS tag id in the ExifTags.GPS enum, so a
            # name costs one lookup rather than a table inversion.
            dms = gps.get(getattr(ExifTags.GPS, value_tag, -1))
            ref = gps.get(getattr(ExifTags.GPS, ref_tag, -1))
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
            ctx.save_preview(img, item, role="poster", seq=0)

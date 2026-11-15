"""Everything else — disk images, 3D models, binaries, unknown extensions.

Matches last and always succeeds. Its only job is to uphold the invariant
that every item has a preview, so the timeline has no holes. Where a format
is recognisable it gets a one-line description; otherwise the card shows the
extension and the size, which is still a true diary entry.
"""

from __future__ import annotations

from pathlib import Path

from .. import thumbs
from ..config import human_size
from ..manifest import Item
from .base import Context, Extractor

# Formats common in a downloads folder that have no preview worth building,
# but which deserve a human label rather than a bare extension.
_DESCRIPTIONS = {
    "dmg": "macOS disk image",
    "pkg": "macOS installer package",
    "iso": "disc image",
    "exe": "Windows executable",
    "msi": "Windows installer",
    "app": "macOS application",
    "stl": "3D model (stereolithography)",
    "stp": "3D model (STEP)",
    "step": "3D model (STEP)",
    "obj": "3D model (Wavefront)",
    "gp": "Guitar Pro tablature",
    "gp3": "Guitar Pro 3 tablature",
    "gp4": "Guitar Pro 4 tablature",
    "gp5": "Guitar Pro 5 tablature",
    "aup3": "Audacity project",
    "omr": "optical music recognition project",
    "wasm": "WebAssembly module",
    "ttf": "TrueType font",
    "otf": "OpenType font",
    "woff": "web font",
    "woff2": "web font",
    "crt": "certificate",
    "pem": "certificate or key",
    "key": "private key",
    "torrent": "BitTorrent metadata",
    "": "no extension",
}


class GenericExtractor(Extractor):
    name = "generic"

    def matches(self, item: Item) -> bool:
        return True              # last in the registry; the safety net

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        description = _DESCRIPTIONS.get(item.ext, "")
        if description:
            item.meta["description"] = description
            item.summary = description

        lines = [description] if description else []
        lines.append(human_size(item.size))
        if item.source.get("domain"):
            lines.append(f"from {item.source['domain']}")

        img = thumbs.card_for_item(item, lines=lines, size=ctx.thumb_size)
        ctx.save_preview(img, item, role="card", seq=0)

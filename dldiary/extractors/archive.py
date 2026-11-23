"""Archives — listed, never extracted.

These are untrusted downloads. Extracting one would write attacker-chosen
paths to disk, so the archive is only ever opened for its table of contents.
A listing is enough for a diary: "28 Oct — a zip of guitar tabs".

Only ZIP and TAR are readable with the stdlib; everything else (7z, rar, dmg)
gets a card naming the format.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from .. import thumbs
from ..config import human_size
from ..manifest import Item
from .base import Context, Extractor

MAX_LISTED = 40

# Container noise that says nothing about what the archive holds. Matched on
# the basename as well as the path, since these hide inside folders too.
_NOISE_PREFIXES = ("__MACOSX/",)
_NOISE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}


def _is_noise(name: str) -> bool:
    if name.endswith("/") or name.startswith(_NOISE_PREFIXES):
        return True
    base = name.rsplit("/", 1)[-1]
    return base in _NOISE_NAMES or base.startswith("._")


class ArchiveExtractor(Extractor):
    name = "archive"
    kinds = frozenset({"archive"})

    def extract(self, item: Item, path: Path, ctx: Context) -> None:
        if zipfile.is_zipfile(path):
            names, total = self._zip(path)
        elif tarfile.is_tarfile(path):
            names, total = self._tar(path)
        else:
            self._unsupported(item, ctx)
            return

        interesting = [n for n in names if not _is_noise(n)]

        # `entries` is what a human would call the contents; `members` counts
        # the raw ZIP records, directories and resource forks included.
        item.meta["entries"] = len(interesting)
        item.meta["members"] = len(names)
        item.meta["uncompressed"] = total
        item.meta["uncompressed_human"] = human_size(total)
        if total and item.size:
            item.meta["compression"] = f"{100 * (1 - item.size / total):.0f}%"

        item.meta["top_entries"] = interesting[:MAX_LISTED]
        item.summary = ", ".join(interesting[:12])

        lines = [f"{len(interesting)} files · {human_size(total)}", ""]
        lines += [f"  {n[:58]}" for n in interesting[:14]]
        if len(interesting) > 14:
            lines.append(f"  … and {len(interesting) - 14} more")

        img = thumbs.card_for_item(item, lines=lines, size=ctx.thumb_size)
        ctx.save_preview(img, item, role="card", seq=0)

    # -- readers ------------------------------------------------------------

    def _zip(self, path: Path) -> tuple[list[str], int]:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
        return [i.filename for i in infos], sum(i.file_size for i in infos)

    def _tar(self, path: Path) -> tuple[list[str], int]:
        names: list[str] = []
        total = 0
        with tarfile.open(path) as tf:
            for member in tf:
                names.append(member.name)
                total += member.size
        return names, total

    def _unsupported(self, item: Item, ctx: Context) -> None:
        label = {"7z": "7-Zip", "rar": "RAR"}.get(item.ext, item.ext.upper())
        item.meta["listing"] = "unsupported format"
        img = thumbs.card_for_item(
            item,
            lines=[f"{label} archive", human_size(item.size),
                   "", "Contents not listed:", "no stdlib reader."],
            size=ctx.thumb_size,
        )
        ctx.save_preview(img, item, role="card", seq=0)

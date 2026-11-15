"""Getting preview files to where a viewer needs them.

Previews live once, in `<output>/previews/`. A viewer that needs them under
its own folder gets hardlinks: same bytes, same inode, no extra disk. Where
hardlinking is not possible — a different filesystem, a platform that will
not do it — the file is copied instead.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def link_or_copy(source: Path, destination: Path) -> bool:
    """Place `source` at `destination`. True if a hardlink was used."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        try:
            if destination.samefile(source):
                return True            # already linked from an earlier run
        except OSError:
            pass
        try:
            destination.unlink()
        except OSError:
            return False

    try:
        os.link(source, destination)
        return True
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(source, destination)
        return False


def mirror_previews(previews: list[str], output: Path, target_dir: Path,
                    *, prefix: str = "") -> dict[str, str]:
    """Mirror a set of manifest-relative preview paths into `target_dir`.

    Returns a mapping from the original manifest path to the path a viewer
    should use, relative to its own folder.
    """
    mapping: dict[str, str] = {}
    for rel in previews:
        source = output / rel
        if not source.exists():
            continue
        name = Path(rel).name
        link_or_copy(source, target_dir / name)
        mapping[rel] = f"{prefix}{name}" if prefix else name
    return mapping


def prune(directory: Path, keep: set[str], suffix: str = ".webp") -> int:
    """Delete files in `directory` that a re-render no longer references.

    Scoped to one suffix inside a folder this tool owns, so nothing a user
    put there can be caught by it.
    """
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob(f"*{suffix}"):
        if path.name not in keep:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed

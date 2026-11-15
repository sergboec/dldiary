"""The indexing pass: folder in, previews and manifest out.

One shot. The tool is not a mapper you run weekly — you run it, you get a
diary, and then you clear the downloads folder out. So there is no reuse
cache, no fingerprint comparison and no `--force`: every run reads every
file and builds the manifest from nothing.

Order matters. Provenance is read before extraction so an extractor can see
where a file came from; filesystem and filename dates are seeded before
extraction so a richer date found inside the file simply outranks them; the
anchor is resolved last, once every candidate is in.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import dates, extractors, provenance
from .config import Config, human_size
from .manifest import Item, Manifest
from .scan import scan
from .viewers import assets


@dataclass
class IndexReport:
    scanned: int = 0
    errors: int = 0
    previews: int = 0
    pruned: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        bits = [f"{self.scanned} files", f"{self.previews} previews"]
        if self.errors:
            bits.append(f"{self.errors} errors")
        if self.pruned:
            bits.append(f"{self.pruned} stale previews removed")
        bits.append(f"{self.seconds:.1f}s")
        return " · ".join(bits)


def index(cfg: Config) -> tuple[Manifest, IndexReport]:
    """Build the manifest for `cfg.input` from scratch."""
    started = time.monotonic()
    report = IndexReport()

    if not provenance.available():
        print("  ! xattr unavailable — no download dates or source URLs",
              file=sys.stderr)

    registry = extractors.build_registry()
    ctx = extractors.Context(cfg=cfg)
    manifest = Manifest(input=str(cfg.input.expanduser().resolve()))

    for item in scan(cfg.input, skip_larger_than=cfg.skip_larger_than):
        report.scanned += 1
        path = Path(item.src_path)

        if cfg.dry_run:
            print(f"  + {item.name} ({human_size(item.size)}) would be indexed")
            manifest.items.append(item)
            continue

        _enrich(item, path)
        extractors.run(item, path, ctx, registry)
        dates.resolve(item)

        manifest.items.append(item)
        report.previews += len(item.previews)
        if item.error:
            report.errors += 1
        if cfg.verbose:
            mark = "!" if item.error else "+"
            print(f"  {mark} {item.name} → {len(item.previews)} preview(s), "
                  f"{item.date[:16] or 'no date'} ({item.date_source})")

    if not cfg.dry_run:
        report.pruned = _prune_previews(manifest, cfg)

    report.seconds = time.monotonic() - started
    return manifest, report


def _enrich(item: Item, path: Path) -> None:
    """Provenance and the dates that need no file parsing."""
    item.source = provenance.collect(path)
    dates.seed_candidates(item, path)
    downloaded = item.source.get("downloaded_at")
    if downloaded:
        item.date_candidates["quarantine"] = downloaded


def _prune_previews(manifest: Manifest, cfg: Config) -> int:
    """Delete previews left behind by an earlier run into this folder.

    With no reuse cache, a second run writes a fresh set of previews under
    fresh ids; without this the old set would simply accumulate.
    """
    keep = {Path(p.path).name for item in manifest.items for p in item.previews}
    return assets.prune(cfg.previews_dir, keep)

"""The indexing pass: folder in, previews and manifest out.

Order matters. Provenance is read before extraction so that an extractor can
see where a file came from; filesystem and filename dates are seeded before
extraction so that a richer date found inside the file simply outranks them;
the anchor is resolved last, once every candidate is in.

Re-runs are incremental. An unchanged file — same size, mtime and inode — is
carried over whole, previews and all, because re-decoding 38 GB of video on
every run is not something anyone would do twice.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import dates, extractors, manifest as manifest_io, provenance
from .config import Config, human_size
from .manifest import Item, Manifest
from .scan import scan


@dataclass
class IndexReport:
    scanned: int = 0
    extracted: int = 0
    reused: int = 0
    missing: int = 0
    errors: int = 0
    previews: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        bits = [
            f"{self.scanned} files",
            f"{self.extracted} extracted",
            f"{self.reused} unchanged",
            f"{self.previews} previews",
        ]
        if self.missing:
            bits.append(f"{self.missing} missing")
        if self.errors:
            bits.append(f"{self.errors} errors")
        bits.append(f"{self.seconds:.1f}s")
        return " · ".join(bits)


def index(cfg: Config) -> tuple[Manifest, IndexReport]:
    """Build or refresh the manifest for `cfg.input`."""
    started = time.monotonic()
    report = IndexReport()

    previous = manifest_io.load(cfg.manifest_path) if not cfg.force else Manifest()
    previous_by_id = previous.by_id()

    if not provenance.available():
        print("  ! xattr unavailable — no download dates or source URLs",
              file=sys.stderr)

    registry = extractors.build_registry()
    ctx = extractors.Context(cfg=cfg)

    current = Manifest(input=str(cfg.input.expanduser().resolve()))
    seen: set[str] = set()

    for item in scan(cfg.input, skip_larger_than=cfg.skip_larger_than):
        report.scanned += 1
        seen.add(item.id)
        path = Path(item.src_path)

        old = previous_by_id.get(item.id)
        if old is not None and not cfg.force and _reusable(old, item, cfg):
            carried = _carry_over(old, item)
            current.items.append(carried)
            report.reused += 1
            report.previews += len(carried.previews)
            if cfg.verbose:
                print(f"  = {item.name}")
            continue

        if cfg.dry_run:
            print(f"  + {item.name} ({human_size(item.size)}) would be indexed")
            current.items.append(item)
            continue

        _clear_previews(item, cfg)
        _enrich(item, path)
        extractors.run(item, path, ctx, registry)
        dates.resolve(item)
        _inherit_manual_date(old, item)

        current.items.append(item)
        report.extracted += 1
        report.previews += len(item.previews)
        if item.error:
            report.errors += 1
        if cfg.verbose:
            mark = "!" if item.error else "+"
            print(f"  {mark} {item.name} → {len(item.previews)} preview(s), "
                  f"{item.date[:16] or 'no date'} ({item.date_source})")

    # Entries whose source file is gone are kept: the diary outlives the
    # downloads folder, which is the entire point of building it.
    for item_id, old in previous_by_id.items():
        if item_id in seen:
            continue
        old.missing = True
        current.items.append(old)
        report.missing += 1
        if cfg.verbose:
            print(f"  - {old.name} (source file gone, entry kept)")

    report.seconds = time.monotonic() - started
    return current, report


# -- incremental helpers ----------------------------------------------------


def _reusable(old: Item, fresh: Item, cfg: Config) -> bool:
    """True when the previous extraction can stand, untouched."""
    if not fresh.fingerprint.matches(old.fingerprint):
        return False
    if not old.previews:
        return False
    # A preview file deleted by hand must be regenerated.
    return all((cfg.output / p.path).exists() for p in old.previews)


def _carry_over(old: Item, fresh: Item) -> Item:
    """Keep the old extraction, refresh the cheap facts around it."""
    old.src_path = fresh.src_path
    old.fingerprint = fresh.fingerprint
    old.missing = False
    if fresh.content_key:
        old.content_key = fresh.content_key
    # The caps are a property of the contract, not of one code path, so a
    # manifest written by an older build heals on the next ordinary run
    # instead of needing --force.
    old.normalize()
    return old


def _inherit_manual_date(old: Item | None, fresh: Item) -> None:
    """A hand-corrected date survives re-indexing."""
    if old is not None and old.date_source == "manual" and old.date:
        fresh.date = old.date
        fresh.date_source = "manual"
        fresh.date_candidates["manual"] = old.date


def _clear_previews(item: Item, cfg: Config) -> None:
    """Drop stale preview files before re-extracting this item.

    Only files named for this item's id are touched, so nothing else in the
    previews folder can be affected.
    """
    if not cfg.previews_dir.exists():
        return
    for stale in cfg.previews_dir.glob(f"{item.id}-*.webp"):
        try:
            stale.unlink()
        except OSError:
            pass


def _enrich(item: Item, path: Path) -> None:
    """Provenance and the dates that need no file parsing."""
    item.source = provenance.collect(path)
    dates.seed_candidates(item, path)
    downloaded = item.source.get("downloaded_at")
    if downloaded:
        item.date_candidates["quarantine"] = downloaded

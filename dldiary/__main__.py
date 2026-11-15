"""Command line entry point: `python -m dldiary`."""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__
from .config import Config, human_size, parse_args

# Pillow encodes every preview and draws every card, so it is the one hard
# requirement. The rest degrade to a plain card when absent.
_INSTALL_HINT = (
    "error: Pillow is required but not installed.\n\n"
    "  python3 -m venv .venv\n"
    "  .venv/bin/pip install -r requirements.txt\n"
    "  .venv/bin/python -m dldiary\n"
)


def _preflight() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    if not _preflight():
        return 2
    try:
        if cfg.command == "check":
            return _run_check(cfg)
        if cfg.command in ("all", "index"):
            if not _run_index(cfg):
                return 1
        if cfg.command in ("all", "render"):
            if not _run_render(cfg):
                return 1
        # A clean run should also be a valid one.
        if cfg.command == "all" and not cfg.dry_run:
            _run_check(cfg)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


def _run_check(cfg: Config) -> int:
    from . import check

    print(f"checking {_rel(cfg.manifest_path)}")
    return check.report(check.run(cfg), verbose=cfg.verbose)


# -- stages -----------------------------------------------------------------


def _run_index(cfg: Config) -> bool:
    from . import indexer, manifest as manifest_io

    try:
        resolved = cfg.input.expanduser().resolve(strict=True)
    except (OSError, FileNotFoundError):
        print(f"error: input folder not found: {cfg.input}", file=sys.stderr)
        return False
    if not resolved.is_dir():
        print(f"error: not a folder: {resolved}", file=sys.stderr)
        return False

    print(f"dldiary {__version__}")
    print(f"  input   {resolved}")
    print(f"  output  {cfg.output.expanduser().resolve()}")
    if cfg.dry_run:
        print("  dry run — nothing will be written")

    manifest, report = indexer.index(cfg)

    if cfg.dry_run:
        print(f"\n{report.scanned} files would be indexed")
        return True

    manifest_io.save(manifest, cfg.manifest_path)
    stats = manifest.build_stats()

    print(f"\n  {report.summary()}")
    print(f"  {stats['items']} entries · {human_size(stats['bytes'])} · "
          f"{_range(stats)}")
    if stats["duplicates"]:
        print(f"  {stats['duplicates']} duplicate file(s) detected")
    if stats["errors"]:
        print(f"  {stats['errors']} file(s) could not be fully read "
              f"(see \"error\" in index.json)")
    print(f"  manifest → {_rel(cfg.manifest_path)}")
    return True


def _run_render(cfg: Config) -> bool:
    from . import manifest as manifest_io

    if cfg.dry_run:
        print("dry run — viewers not rendered")
        return True

    try:
        manifest = manifest_io.load(cfg.manifest_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False

    if not manifest.items:
        print(f"error: no entries in {_rel(cfg.manifest_path)} — run "
              f"`dldiary index` first", file=sys.stderr)
        return False

    if cfg.wants_html:
        from .viewers import html

        out = html.render(manifest, cfg)
        print(f"  html    → {_rel(out)}")

    if cfg.wants_obsidian:
        from .viewers import obsidian

        out = obsidian.render(manifest, cfg)
        print(f"  vault   → {_rel(out)}")

    return True


# -- formatting -------------------------------------------------------------


def _range(stats: dict) -> str:
    span = stats.get("date_range") or []
    if len(span) == 2:
        return f"{span[0]} … {span[1]}" if span[0] != span[1] else span[0]
    return "no dates"


def _rel(path: Path) -> str:
    """Show a path relative to the working directory when that is shorter."""
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())

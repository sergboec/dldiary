"""Runtime configuration and the command line."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

DEFAULT_INPUT = "./downloads_test"
DEFAULT_OUTPUT = "."

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*$", re.I)
_SIZE_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """`"2G"` -> 2147483648. Raises ValueError on anything else."""
    m = _SIZE_RE.match(text)
    if not m:
        raise ValueError(f"not a size: {text!r} (try 500M, 2G)")
    return int(float(m.group(1)) * _SIZE_MULT[m.group(2).upper()])


def human_size(n: int) -> str:
    """Bytes as a short human string. Used in viewers and log lines."""
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"


@dataclass
class Config:
    command: str = "all"                 # all | index | render
    input: Path = field(default_factory=lambda: Path(DEFAULT_INPUT))
    output: Path = field(default_factory=lambda: Path(DEFAULT_OUTPUT))

    force: bool = False
    thumb_size: int = 512
    video_frames: int = 3
    skip_larger_than: int | None = None

    only: str = "both"                   # both | html | obsidian
    obsidian_grain: str = "item"         # item | day
    inline_assets: bool = False

    dry_run: bool = False
    verbose: bool = False

    # -- derived paths -----------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.output / "index.json"

    @property
    def previews_dir(self) -> Path:
        return self.output / "previews"

    @property
    def obsidian_dir(self) -> Path:
        return self.output / "obsidian"

    @property
    def html_dir(self) -> Path:
        return self.output / "html"

    @property
    def wants_html(self) -> bool:
        return self.only in ("both", "html")

    @property
    def wants_obsidian(self) -> bool:
        return self.only in ("both", "obsidian")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dldiary",
        description=(
            "Index a downloads folder into a visual diary: a previews/ folder "
            "and one index.json manifest, plus static HTML and Obsidian viewers. "
            "The input folder is never modified."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  dldiary all                      index + render, defaults\n"
            "  dldiary index --input ~/Downloads\n"
            "  dldiary render --only html       rebuild one viewer from index.json\n"
        ),
    )
    p.add_argument(
        "command", nargs="?", default="all",
        choices=("all", "index", "render", "check"),
        help="all (default): index then render; index: previews + manifest; "
             "render: viewers only, from the existing manifest; "
             "check: validate the manifest, previews and viewers",
    )
    p.add_argument("--version", action="version", version=f"dldiary {__version__}")

    io_group = p.add_argument_group("input / output")
    io_group.add_argument(
        "-i", "--input", type=Path, default=Path(DEFAULT_INPUT),
        help=f"folder to index, read-only (default: {DEFAULT_INPUT})",
    )
    io_group.add_argument(
        "-o", "--output", type=Path, default=Path(DEFAULT_OUTPUT),
        help=f"where index.json, previews/, obsidian/ and html/ go "
             f"(default: {DEFAULT_OUTPUT})",
    )

    idx = p.add_argument_group("indexing")
    idx.add_argument("-f", "--force", action="store_true",
                     help="re-extract everything, ignoring fingerprints")
    idx.add_argument("--thumb-size", type=int, default=512, metavar="PX",
                     help="longest edge of a preview, in pixels (default: 512)")
    idx.add_argument("--video-frames", type=int, default=3, metavar="N",
                     help="extra frames sampled per video, beyond the poster "
                          "(default: 3)")
    idx.add_argument("--skip-larger-than", type=parse_size, default=None,
                     metavar="SIZE",
                     help="skip extraction for files above this size, e.g. 2G; "
                          "they are still indexed, with a generic card")

    out = p.add_argument_group("viewers")
    out.add_argument("--only", choices=("both", "html", "obsidian"), default="both",
                     help="which viewers to render (default: both)")
    out.add_argument("--obsidian-grain", choices=("item", "day"), default="item",
                     help="one note per file (default) or one note per day")
    out.add_argument("--inline-assets", action="store_true",
                     help="base64-inline previews into diary.html, making it a "
                          "single portable file")

    misc = p.add_argument_group("misc")
    misc.add_argument("-n", "--dry-run", action="store_true",
                      help="report what would happen; write nothing")
    misc.add_argument("-v", "--verbose", action="store_true",
                      help="per-file logging")
    return p


def parse_args(argv: list[str] | None = None) -> Config:
    args = build_parser().parse_args(argv)
    return Config(
        command=args.command,
        input=args.input.expanduser(),
        output=args.output.expanduser(),
        force=args.force,
        thumb_size=max(64, args.thumb_size),
        video_frames=max(0, args.video_frames),
        skip_larger_than=args.skip_larger_than,
        only=args.only,
        obsidian_grain=args.obsidian_grain,
        inline_assets=args.inline_assets,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

"""Runtime configuration and the command line."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

DEFAULT_INPUT = "./downloads_test"
DEFAULT_OUTPUT = "."

# Points. Measured across 84 real PDFs — tickets, invoices, bank statements
# — by counting how many still showed a name or a street at each threshold:
#
#     10.0pt -> 19 leaked, 81 kept a usable title
#     10.5pt ->  6 leaked, 74
#     12.0pt ->  2 leaked, 69      <- the plateau: 11.5 and 12 score alike
#     14.0pt ->  2 leaked, 48      <- titles fall off a cliff, leaks do not
#
# The two that survive at 12 are CVs, where the name *is* the heading at
# 28pt and 17pt. No size rule reaches those; --blur-pdf does.
DEFAULT_SMALL_TEXT_PT = 12.0

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
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def human_duration(seconds: float) -> str:
    """`"3:07"`, or `"1:02:30"` once there is an hour. Audio and video both
    label a running time, and they labelled it with the same function
    written twice."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Config:
    command: str = "all"                 # all | index | render
    input: Path = field(default_factory=lambda: Path(DEFAULT_INPUT))
    output: Path = field(default_factory=lambda: Path(DEFAULT_OUTPUT))

    thumb_size: int = 512
    video_frames: int = 3
    skip_larger_than: int | None = None

    # Kinds whose previews are rendered deliberately unreadable, and whose
    # extracted text is not stored at all. A set rather than a boolean so
    # widening the scope later is a one-line change.
    blur_kinds: frozenset[str] = frozenset()
    blur_radius: int | None = None       # None = derive from thumb_size

    # Blur only the text a PDF sets below this many points, leaving larger
    # text sharp. On a ticket the fine print is the name, the address, the
    # booking reference and the barcode digits; the event, the venue and
    # the price are set larger and stay readable.
    blur_text_below: float | None = None

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

    # -- blur policy -------------------------------------------------------

    def blurs(self, kind: str) -> bool:
        return kind in self.blur_kinds

    def blur_radius_for(self, kind: str) -> float:
        """Gaussian radius in final pixels, or 0 when this kind is sharp.

        Scaled off the thumbnail size so the effect looks the same whatever
        `--thumb-size` is: a fixed radius that erases text at 512 px would
        barely smudge it at 1024.
        """
        if not self.blurs(kind):
            return 0.0
        if self.blur_radius is not None:
            return float(self.blur_radius)
        return float(max(4, self.thumb_size // 42))

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
    idx.add_argument("--thumb-size", type=int, default=512, metavar="PX",
                     help="longest edge of a preview, in pixels (default: 512)")
    idx.add_argument("--video-frames", type=int, default=3, metavar="N",
                     help="extra frames sampled per video, beyond the poster "
                          "(default: 3)")
    idx.add_argument("--skip-larger-than", type=parse_size, default=None,
                     metavar="SIZE",
                     help="skip extraction for files above this size, e.g. 2G; "
                          "they are still indexed, with a generic card")

    privacy = p.add_argument_group("privacy")
    privacy.add_argument(
        "--blur-pdf", action="store_true",
        help="blur PDF previews entirely, rather than only their small "
             "text, and store no text extracted from one. Office documents "
             "are covered the same way by either blur flag",
    )
    privacy.add_argument(
        "--blur-text-below", type=float, nargs="?", const=DEFAULT_SMALL_TEXT_PT,
        default=None, metavar="PT",
        help=f"blur only the text a PDF sets below this many points, and keep "
             f"no text from below it either (default {DEFAULT_SMALL_TEXT_PT} "
             f"when the flag is given on its own). On a ticket that is the "
             f"name, address, booking reference and barcode digits; the "
             f"event, venue and price are set larger and stay readable",
    )
    privacy.add_argument(
        "--blur-radius", type=int, default=None, metavar="PX",
        help="Gaussian blur radius in pixels (default: scaled from "
             "--thumb-size, about 12 at the default 512)",
    )

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


def _blur_kinds(args) -> frozenset[str]:
    """Which kinds get the blunt treatment: blur the lot, keep no text.

    PDFs can be surgical because PyMuPDF gives every span a font size and a
    bounding box. Office files cannot: PowerPoint declares no size on its
    runs at all — 0 of 181 in a real deck — because sizes are inherited
    from slide layouts and masters, and resolving that needs a layout
    engine. With no size to measure, the only honest reading of "hide the
    private parts" is to hide the document.
    """
    kinds: set[str] = set()
    if args.blur_pdf:
        kinds.add("pdf")
    # Either flag covers Office the same way, so the blunter option is
    # never the less private one.
    if args.blur_pdf or args.blur_text_below is not None:
        kinds.add("document")
    return frozenset(kinds)


def parse_args(argv: list[str] | None = None) -> Config:
    args = build_parser().parse_args(argv)
    return Config(
        command=args.command,
        input=args.input.expanduser(),
        output=args.output.expanduser(),
        thumb_size=max(64, args.thumb_size),
        video_frames=max(0, args.video_frames),
        skip_larger_than=args.skip_larger_than,
        blur_kinds=_blur_kinds(args),
        blur_radius=args.blur_radius,
        blur_text_below=args.blur_text_below,
        only=args.only,
        obsidian_grain=args.obsidian_grain,
        inline_assets=args.inline_assets,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

"""End-to-end checks against the real test corpus in `downloads_test/`.

Run with the project venv:

    .venv/bin/python -m pytest -q
    .venv/bin/python tests/test_smoke.py     # no pytest needed

The corpus is eight real files — three PDFs, a PNG, an MP4, an HTML page, a
Python script and a ZIP — so these are integration tests, not mocks. Every
assertion below is a property of that corpus.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dldiary import SCHEMA_VERSION                          # noqa: E402
from dldiary import dates, manifest as manifest_io          # noqa: E402
from dldiary.config import Config, human_size, parse_size   # noqa: E402
from dldiary.extractors.base import (                       # noqa: E402
    looks_like_prose, title_from_name,
)
from dldiary import check                                   # noqa: E402
from dldiary.indexer import index                           # noqa: E402
from dldiary.provenance import domain_of                    # noqa: E402
from dldiary.scan import scan                               # noqa: E402
from dldiary.viewers import html as html_viewer             # noqa: E402
from dldiary.viewers import obsidian as obsidian_viewer     # noqa: E402

CORPUS = ROOT / "downloads_test"
EXPECTED_FILES = 8


def build(output: Path, **overrides) -> Config:
    cfg = Config(input=CORPUS, output=output, **overrides)
    return cfg


class CorpusTestCase(unittest.TestCase):
    """Index the corpus once per class into a temporary output folder."""

    @classmethod
    def setUpClass(cls):
        if not CORPUS.is_dir():
            raise unittest.SkipTest(f"corpus missing: {CORPUS}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="dldiary-test-"))
        cls.cfg = build(cls.tmp)
        cls.manifest, cls.report = index(cls.cfg)
        manifest_io.save(cls.manifest, cls.cfg.manifest_path)
        cls.by_name = {i.name: i for i in cls.manifest.items}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestIndex(CorpusTestCase):

    def test_every_file_indexed(self):
        self.assertEqual(len(self.manifest.items), EXPECTED_FILES)
        self.assertEqual(self.report.scanned, EXPECTED_FILES)

    def test_every_item_has_a_date_and_a_preview(self):
        for item in self.manifest.items:
            with self.subTest(item.name):
                self.assertTrue(item.date, "no anchor date")
                self.assertNotEqual(item.date_source, "unknown")
                self.assertTrue(item.previews, "no preview generated")

    def test_preview_files_exist_and_reopen(self):
        from PIL import Image

        for item in self.manifest.items:
            for preview in item.previews:
                path = self.cfg.output / preview.path
                with self.subTest(preview.path):
                    self.assertTrue(path.exists(), "preview file missing")
                    with Image.open(path) as img:
                        self.assertEqual((img.width, img.height),
                                         (preview.w, preview.h))

    def test_no_errors(self):
        broken = [(i.name, i.error) for i in self.manifest.items if i.error]
        self.assertEqual(broken, [])

    def test_nothing_written_outside_output(self):
        """The input folder is read-only, and the ZIP is never extracted."""
        before = sorted(p.name for p in CORPUS.iterdir())
        index(build(self.tmp))
        self.assertEqual(sorted(p.name for p in CORPUS.iterdir()), before)

    def test_ids_are_stable_across_runs(self):
        again, _ = index(build(self.tmp))
        self.assertEqual({i.id for i in again.items},
                         {i.id for i in self.manifest.items})


class TestProvenance(CorpusTestCase):

    def test_eventim_ticket(self):
        item = self.by_name["ticketdirect-1.pdf"]
        self.assertEqual(item.source.get("domain"), "eventim.de")
        self.assertEqual(item.source.get("app"), "Firefox")
        self.assertEqual(item.day, "2026-10-24")
        self.assertEqual(item.date_source, "quarantine")
        self.assertIn("Ticket", item.summary)

    def test_telegram_image(self):
        item = self.by_name["image.png"]
        self.assertEqual(item.source.get("app"), "Telegram")
        self.assertEqual(item.meta["width"], 1439)
        self.assertEqual(item.meta["height"], 1005)

    def test_blob_url_keeps_its_origin(self):
        item = self.by_name["server.py"]
        self.assertEqual(item.source.get("domain"), "claude.ai")

    def test_domain_parsing(self):
        self.assertEqual(domain_of("blob:https://claude.ai/abc"), "claude.ai")
        self.assertEqual(domain_of("https://www.eventim.de/x?y=1"), "eventim.de")
        self.assertEqual(domain_of(""), "")
        self.assertEqual(domain_of("not a url"), "")


class TestExtractors(CorpusTestCase):

    def test_video_frames_are_distinct(self):
        """The clip's first keyframe is pure black; posters must not be."""
        item = self.by_name["proskochim_na_seryi.mp4"]
        self.assertGreaterEqual(len(item.previews), 2)
        self.assertAlmostEqual(item.meta["duration"], 11.6, places=1)
        self.assertEqual((item.meta["width"], item.meta["height"]), (1920, 1080))

        from PIL import Image, ImageStat

        for preview in item.previews:
            with Image.open(self.cfg.output / preview.path) as img:
                stddev = ImageStat.Stat(img.convert("L")).stddev[0]
            with self.subTest(preview.path):
                self.assertGreater(stddev, 1.0, "blank frame saved")

    def test_pdf_pages_and_text(self):
        item = self.by_name["31-10-2026--Ticket-01--28302466111-1.pdf"]
        self.assertEqual(item.meta["pages"], 1)
        self.assertIn("Onegin", item.title + item.summary)

    def test_barcode_pdf_rejected_as_text(self):
        """Barcode glyph runs must not become a summary or a title."""
        item = self.by_name["Your ticketdirect order number 541959.pdf"]
        self.assertEqual(item.summary, "")
        self.assertTrue(item.meta.get("no_readable_text"))
        self.assertNotIn("DataWindow", item.title)
        self.assertTrue(item.previews)

    def test_prose_detector(self):
        self.assertTrue(looks_like_prose(
            "Dieses Ticket ist nur gueltig als kompletter Ausdruck."))
        self.assertFalse(looks_like_prose(
            "Ë*Æ{#.N/{,.nÌ Ë*Æ{#.N/{,.nÌ Ë*Æ{#.N/{,.nÌ Ë*Æ{#.N/{,.nÌ"))

    def test_zip_listed_not_extracted(self):
        item = self.by_name["tabs.zip"]
        self.assertEqual(item.meta["members"], 11)
        self.assertEqual(item.meta["entries"], 4)
        listed = item.meta["top_entries"]
        self.assertTrue(all(".DS_Store" not in n for n in listed))
        self.assertTrue(all("__MACOSX" not in n for n in listed))

    def test_html_title_and_encoding(self):
        item = self.by_name["index.html"]
        self.assertEqual(item.meta["encoding"], "utf-8")
        self.assertTrue(item.title)
        self.assertNotEqual(item.title, item.name)


class TestBlur(unittest.TestCase):
    """`--blur-pdf`: unreadable previews, and no text kept from the page."""

    @classmethod
    def setUpClass(cls):
        if not CORPUS.is_dir():
            raise unittest.SkipTest(f"corpus missing: {CORPUS}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="dldiary-blur-"))
        cls.cfg = build(cls.tmp, blur_kinds=frozenset({"pdf"}))
        cls.manifest, _ = index(cls.cfg)
        manifest_io.save(cls.manifest, cls.cfg.manifest_path)
        cls.pdfs = [i for i in cls.manifest.items if i.kind == "pdf"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_corpus_has_pdfs_to_blur(self):
        self.assertEqual(len(self.pdfs), 3)

    def test_previews_are_marked_and_actually_blurred(self):
        """Measure it, rather than trusting the flag that requested it."""
        sharp = Path(tempfile.mkdtemp(prefix="dldiary-sharp-"))
        try:
            plain_cfg = build(sharp)
            plain, _ = index(plain_cfg)
            plain_by_name = {i.name: i for i in plain.items}

            for item in self.pdfs:
                with self.subTest(item.name):
                    self.assertTrue(all(p.blurred for p in item.previews))
                    before = _edge_energy(
                        plain_cfg.output / plain_by_name[item.name].previews[0].path)
                    after = _edge_energy(
                        self.cfg.output / item.previews[0].path)
                    # A preview that was not blurred scores ~1.0 here.
                    # Radius 12 measures about 0.33 on these tickets: the
                    # residue is page-scale structure (margins, the colour
                    # band), not text.
                    self.assertLess(
                        after, before * 0.4,
                        f"edge energy {after:.1f} vs {before:.1f} "
                        f"({after / before:.0%} of sharp) — the blur did not "
                        f"meaningfully destroy detail",
                    )
        finally:
            shutil.rmtree(sharp, ignore_errors=True)

    def test_no_text_is_stored(self):
        for item in self.pdfs:
            with self.subTest(item.name):
                self.assertEqual(item.summary, "")
                self.assertTrue(item.meta.get("text_suppressed"))
                self.assertEqual(item.title, title_from_name(item.name))
                for leaked in ("author", "subject", "keywords"):
                    self.assertNotIn(leaked, item.meta)
                # structural metadata is still useful and not personal
                self.assertIn("pages", item.meta)

    def test_nothing_personal_reaches_the_output(self):
        obsidian_viewer.render(self.manifest, self.cfg)
        html_viewer.render(self.manifest, self.cfg)

        needles = ("Boitsov", "Onegin", "838944373", "JETHRO")
        for path in self.cfg.output.rglob("*"):
            if not path.is_file() or path.suffix == ".webp":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in needles:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle, text)

    def test_other_kinds_are_untouched(self):
        for item in self.manifest.items:
            if item.kind == "pdf":
                continue
            with self.subTest(item.name):
                self.assertFalse(any(p.blurred for p in item.previews))
                self.assertNotIn("text_suppressed", item.meta)

    def test_check_passes_and_catches_a_sharp_preview(self):
        self.assertEqual(
            [str(p) for p in check.run(self.cfg) if p.level == "error"], []
        )
        # Simulate an extractor that bypassed Context.save_preview.
        data = json.loads(self.cfg.manifest_path.read_text(encoding="utf-8"))
        for entry in data["items"]:
            if entry["kind"] == "pdf":
                entry["previews"][0]["blurred"] = False
                break
        self.cfg.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        try:
            errors = [p for p in check.run(self.cfg) if p.level == "error"]
            self.assertTrue(any("not blurred" in p.message for p in errors))
        finally:
            manifest_io.save(self.manifest, self.cfg.manifest_path)

    def test_radius_scales_with_thumbnail_size(self):
        self.assertEqual(Config(thumb_size=512,
                                blur_kinds=frozenset({"pdf"})
                                ).blur_radius_for("pdf"), 12.0)
        self.assertEqual(Config(thumb_size=1024,
                                blur_kinds=frozenset({"pdf"})
                                ).blur_radius_for("pdf"), 24.0)
        self.assertEqual(Config(thumb_size=512).blur_radius_for("pdf"), 0.0)
        self.assertEqual(Config(thumb_size=512, blur_radius=30,
                                blur_kinds=frozenset({"pdf"})
                                ).blur_radius_for("pdf"), 30.0)


class TestBlurSmallText(unittest.TestCase):
    """`--blur-text-below`: hide the fine print, keep the headline."""

    @classmethod
    def setUpClass(cls):
        if not CORPUS.is_dir():
            raise unittest.SkipTest(f"corpus missing: {CORPUS}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="dldiary-small-"))
        cls.cfg = build(cls.tmp, blur_text_below=10.0)
        cls.manifest, _ = index(cls.cfg)
        manifest_io.save(cls.manifest, cls.cfg.manifest_path)
        cls.by_name = {i.name: i for i in cls.manifest.items}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_large_text_survives(self):
        """The point of the feature: the event is still readable."""
        jethro = self.by_name["ticketdirect-1.pdf"]
        self.assertEqual(jethro.title, "JETHRO TULL")
        self.assertIn("The Curiosity Tour", jethro.summary)
        self.assertIn("TEMPODROM", jethro.summary)

        onegin = self.by_name["31-10-2026--Ticket-01--28302466111-1.pdf"]
        self.assertEqual(onegin.title, "Jewgeni Onegin")

    def test_small_text_is_not_stored(self):
        """Names and addresses are set small on both of these tickets."""
        leaks = ("Boitsov", "Möckernstraße", "Bismarckstraße",
                 "10963", "10625", "Helene-Jacobs")
        for item in self.manifest.items:
            if item.kind != "pdf":
                continue
            haystack = f"{item.title}\n{item.summary}\n{item.meta}"
            for leak in leaks:
                with self.subTest(item=item.name, leak=leak):
                    self.assertNotIn(leak, haystack)

    def test_nothing_small_reaches_the_rendered_output(self):
        obsidian_viewer.render(self.manifest, self.cfg)
        html_viewer.render(self.manifest, self.cfg)
        for path in self.cfg.output.rglob("*"):
            if not path.is_file() or path.suffix == ".webp":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for leak in ("Boitsov", "Bismarckstraße", "Möckernstraße"):
                with self.subTest(path=path.name, leak=leak):
                    self.assertNotIn(leak, text)

    def test_barcode_glyphs_do_not_survive_as_text(self):
        """Barcode fonts draw large, so a size filter alone keeps them."""
        for item in self.manifest.items:
            if item.kind == "pdf":
                with self.subTest(item.name):
                    self.assertNotIn("Ë*Æ", item.summary)
                    self.assertNotIn("Ë*Æ", item.title)

    def test_previews_are_marked_and_partly_sharp(self):
        """Partly: a whole-page blur would defeat the purpose."""
        sharp = Path(tempfile.mkdtemp(prefix="dldiary-ref-"))
        try:
            ref_cfg = build(sharp)
            ref, _ = index(ref_cfg)
            ref_by_name = {i.name: i for i in ref.items}

            for item in self.manifest.items:
                if item.kind != "pdf":
                    continue
                with self.subTest(item.name):
                    self.assertTrue(all(p.blurred for p in item.previews))
                    before = _edge_energy(
                        ref_cfg.output / ref_by_name[item.name].previews[0].path)
                    after = _edge_energy(
                        self.cfg.output / item.previews[0].path)
                    # Detail is lost, but nowhere near a whole-page blur,
                    # which lands around a third of sharp.
                    self.assertLess(after, before)
                    self.assertGreater(
                        after, before * 0.45,
                        "selective blur destroyed as much as a full blur — "
                        "is the size threshold catching everything?",
                    )
        finally:
            shutil.rmtree(sharp, ignore_errors=True)

    def test_only_pdfs_and_documents_are_affected(self):
        """Photos, video, code and archives are never touched by this."""
        for item in self.manifest.items:
            if item.kind in ("pdf", "document"):
                continue
            with self.subTest(item.name):
                self.assertFalse(any(p.blurred for p in item.previews))
                self.assertNotIn("text_suppressed", item.meta)

    def test_threshold_is_respected(self):
        """A threshold above everything on the page blurs the lot."""
        tmp = Path(tempfile.mkdtemp(prefix="dldiary-high-"))
        try:
            manifest, _ = index(build(tmp, blur_text_below=99.0))
            for item in manifest.items:
                if item.kind == "pdf":
                    with self.subTest(item.name):
                        self.assertEqual(item.summary, "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _edge_energy(path: Path) -> float:
    """Stddev after an edge filter: high for crisp text, low for a blur."""
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as img:
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).stddev[0]


class TestDates(unittest.TestCase):

    def test_filename_patterns(self):
        cases = {
            "2026-09-13 14.28.18.mp4": (2026, 9, 13, 14),
            "2026-04-11.pdf": (2026, 4, 11, 0),
            "20260222-ring-sizer.stl": (2026, 2, 22, 0),
            "31-10-2026--Ticket.pdf": (2026, 10, 31, 0),
        }
        for name, (y, m, d, h) in cases.items():
            with self.subTest(name):
                got = dates.from_filename(name)
                self.assertIsNotNone(got, f"no date found in {name}")
                self.assertEqual((got.year, got.month, got.day, got.hour),
                                 (y, m, d, h))

    def test_digit_runs_are_not_dates(self):
        """An invoice number must never be read as a date."""
        for name in ("175802554.pdf", "1000007509.jpg", "541959.pdf",
                     "1787554245741.pdf", "28302466111.pdf"):
            with self.subTest(name):
                self.assertIsNone(dates.from_filename(name))

    def test_impossible_dates_rejected(self):
        self.assertIsNone(dates.from_filename("2026-13-45.pdf"))
        self.assertIsNone(dates.from_filename("1832-01-01.pdf"))

    def test_exif_parsing(self):
        got = dates.parse_exif_datetime("2019:07:14 09:31:02")
        self.assertIsNotNone(got)
        self.assertEqual((got.year, got.month, got.day), (2019, 7, 14))
        self.assertIsNone(dates.parse_exif_datetime("0000:00:00 00:00:00"))
        self.assertIsNone(dates.parse_exif_datetime(""))


class TestRepeatability(CorpusTestCase):
    """The tool is one-shot: a second run rebuilds, it does not accumulate."""

    def test_second_run_reproduces_the_same_index(self):
        again, report = index(build(self.tmp))
        self.assertEqual(report.scanned, EXPECTED_FILES)
        self.assertEqual(len(again.items), EXPECTED_FILES)
        self.assertEqual({i.id for i in again.items},
                         {i.id for i in self.manifest.items})
        self.assertEqual(
            [(i.id, i.date, i.date_source) for i in again.sorted_items()],
            [(i.id, i.date, i.date_source) for i in self.manifest.sorted_items()],
        )

    def test_vanished_file_leaves_no_entry(self):
        """Nothing is read back, so the folder is the whole truth."""
        sandbox = Path(tempfile.mkdtemp(prefix="dldiary-oneshot-"))
        try:
            inbox = sandbox / "in"
            inbox.mkdir()
            shutil.copy2(CORPUS / "server.py", inbox / "server.py")
            shutil.copy2(CORPUS / "tabs.zip", inbox / "tabs.zip")

            cfg = Config(input=inbox, output=sandbox / "out")
            first, _ = index(cfg)
            manifest_io.save(first, cfg.manifest_path)
            self.assertEqual(len(first.items), 2)
            before = sorted(p.name for p in cfg.previews_dir.glob("*.webp"))
            self.assertEqual(len(before), 2)

            (inbox / "tabs.zip").unlink()
            second, _ = index(Config(input=inbox, output=sandbox / "out"))

            self.assertEqual([i.name for i in second.items], ["server.py"])
            # and its preview is cleaned up rather than left behind
            after = sorted(p.name for p in cfg.previews_dir.glob("*.webp"))
            self.assertEqual(len(after), 1)
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_manifest_round_trip(self):
        reloaded = manifest_io.load(self.cfg.manifest_path)
        self.assertEqual(len(reloaded.items), EXPECTED_FILES)
        self.assertEqual({i.id for i in reloaded.items},
                         {i.id for i in self.manifest.items})
        self.assertEqual(reloaded.to_dict()["by_day"],
                         self.manifest.to_dict()["by_day"])


class TestManifestShape(CorpusTestCase):

    def test_schema_and_aggregates(self):
        data = json.loads(self.cfg.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], SCHEMA_VERSION)
        self.assertEqual(data["stats"]["items"], EXPECTED_FILES)

        self.assertEqual(
            sorted(data["by_day"]),
            ["2026-10-23", "2026-10-24", "2026-10-26", "2026-10-28",
             "2026-10-29", "2026-10-31"],
        )
        self.assertEqual(data["by_day"]["2026-10-23"]["count"], 2)
        self.assertEqual(data["by_day"]["2026-10-29"]["count"], 2)

        counted = sum(d["count"] for d in data["by_day"].values())
        self.assertEqual(counted, EXPECTED_FILES)

    def test_items_are_sorted_newest_first(self):
        data = json.loads(self.cfg.manifest_path.read_text(encoding="utf-8"))
        dates_in_order = [i["date"] for i in data["items"]]
        self.assertEqual(dates_in_order, sorted(dates_in_order, reverse=True))


class TestViewers(CorpusTestCase):

    def test_html_is_self_contained(self):
        out = html_viewer.render(self.manifest, self.cfg)
        text = out.read_text(encoding="utf-8")

        self.assertTrue(out.exists())
        self.assertNotIn("http://", text.split('"items"')[0])  # no CDN in shell
        self.assertIn('<script type="application/json"', text)

        payload = json.loads(
            text.split('id="data">', 1)[1].split("</script>", 1)[0]
        )
        self.assertEqual(len(payload["items"]), EXPECTED_FILES)

        for item in payload["items"]:
            for preview in item["previews"]:
                with self.subTest(preview["src"]):
                    self.assertTrue((out.parent / preview["src"]).exists())

    def test_html_inline_assets_embeds_images(self):
        cfg = build(self.tmp, inline_assets=True)
        out = html_viewer.render(self.manifest, cfg)
        self.assertIn("data:image/webp;base64,", out.read_text(encoding="utf-8"))

    def test_obsidian_notes_and_base(self):
        vault = obsidian_viewer.render(self.manifest, self.cfg)
        notes = sorted((vault / "items").glob("*.md"))
        self.assertEqual(len(notes), EXPECTED_FILES)

        base = (vault / "Downloads Diary.base").read_text(encoding="utf-8")
        for key in ("filters:", "views:", "type: cards", "image: note.preview",
                    "direction: DESC"):
            self.assertIn(key, base)

        for note in notes:
            text = note.read_text(encoding="utf-8")
            with self.subTest(note.name):
                self.assertTrue(text.startswith("---\n"))
                front = text.split("---", 2)[1]
                self.assertIn("tags: [downloads-diary]", front)
                # Every wikilink must resolve inside the vault.
                for link in _wikilinks(text):
                    self.assertTrue((vault / link).exists(), link)

    def test_obsidian_day_grain(self):
        cfg = build(self.tmp, obsidian_grain="day")
        vault = obsidian_viewer.render(self.manifest, cfg)
        notes = sorted(p.name for p in (vault / "items").glob("*.md"))
        self.assertEqual(
            notes,
            ["2026-10-23.md", "2026-10-24.md", "2026-10-26.md",
             "2026-10-28.md", "2026-10-29.md", "2026-10-31.md"],
        )

    def test_rerender_is_deterministic(self):
        first = html_viewer.render(self.manifest, self.cfg).read_text("utf-8")
        second = html_viewer.render(self.manifest, self.cfg).read_text("utf-8")
        self.assertEqual(first, second)


class TestUnits(unittest.TestCase):

    def test_parse_size(self):
        self.assertEqual(parse_size("2G"), 2 * 1024**3)
        self.assertEqual(parse_size("500M"), 500 * 1024**2)
        self.assertEqual(parse_size("1024"), 1024)
        with self.assertRaises(ValueError):
            parse_size("big")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1536), "1.5 KB")

    def test_scan_skips_noise(self):
        names = {i.name for i in scan(CORPUS)}
        self.assertEqual(len(names), EXPECTED_FILES)
        self.assertTrue(all(not n.startswith(".") for n in names))


def _wikilinks(text: str) -> list[str]:
    import re

    return re.findall(r"!?\[\[([^\]]+)\]\]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

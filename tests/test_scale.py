"""Scale and hostile-input tests.

The smoke suite runs against eight well-behaved files and passed happily
while the real 257-file page rendered completely blank. Those tests were
not wrong, they were just testing the wrong thing: every assumption that
broke in production was about *volume* and *bad values*, and the corpus had
neither.

So this file builds a synthetic corpus that carries, on purpose, each
failure actually seen in the wild:

* a non-finite float in metadata      → `"gps":[NaN,NaN]` made JSON.parse throw
* a megabyte `data:` URI as a source  → 70% of the manifest was one URL
* two files, same day, same stem      → three Obsidian notes vanished silently
* hundreds of single-item days        → the page grew to 34,000 px

and asserts the properties that must hold regardless: strict JSON, viewer
parity, bounded payload. Run with the project venv:

    .venv/bin/python tests/test_scale.py
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dldiary import check, manifest as manifest_io                  # noqa: E402
from dldiary.config import Config                                   # noqa: E402
from dldiary.indexer import index                                   # noqa: E402
from dldiary.manifest import MAX_SUMMARY, MAX_URL, Item, Manifest   # noqa: E402
from dldiary.viewers import html as html_viewer                     # noqa: E402
from dldiary.viewers import obsidian as obsidian_viewer             # noqa: E402

N_ITEMS = 420
PAYLOAD_BUDGET = 900 * 1024      # bytes of diary.html for 420 entries


def synthetic_manifest(n: int = N_ITEMS, *, hostile: bool = True) -> Manifest:
    """A manifest shaped like a real one, with the nasty cases included."""
    manifest = Manifest(input="/synthetic")
    start = datetime(2026, 1, 1, 9, 30)

    for i in range(n):
        # A long tail of single-item days, which is what real folders look
        # like and what made the day-per-row layout fall over.
        when = start + timedelta(days=i // 2 if i % 3 else i, minutes=i)
        item = Item(
            id=f"{i:012x}",
            name=f"file-{i:04d}.pdf",
            ext="pdf",
            kind="pdf",
            mime="application/pdf",
            size=1000 + i,
            src_path=f"/synthetic/file-{i:04d}.pdf",
            date=when.isoformat(timespec="seconds"),
            date_source="quarantine",
            title=f"Document {i}",
            summary="lorem ipsum " * 10,
        )
        item.meta = {"pages": (i % 7) + 1}
        item.source = {"app": "Firefox", "domain": "example.com",
                       "url": f"https://example.com/{i}"}
        manifest.items.append(item)

    if not hostile:
        return manifest

    # 1. Non-finite floats — the bug that blanked the real page.
    manifest.items[0].meta["gps"] = [float("nan"), float("nan")]
    manifest.items[1].meta["ratio"] = float("inf")
    manifest.items[2].meta["neg"] = float("-inf")

    # 2. An unbounded source URL: a data: URI carrying a whole image.
    manifest.items[3].source["url"] = "data:image/png;base64," + "A" * 1_200_000

    # 3. Same day, same stem, different extension — the collision that ate
    #    three notes without a word.
    day = manifest.items[10].date
    for n, (ext, kind) in enumerate(
            (("png", "image"), ("omr", "other"), ("PNG", "image"))):
        twin = Item(
            id=f"twin{n}", name=f"beatles.{ext}", ext=ext.lower(),
            kind=kind, mime="application/octet-stream", size=4096,
            src_path=f"/synthetic/beatles.{ext}", date=day,
            date_source="quarantine", title="beatles",
        )
        manifest.items.append(twin)

    # 4. Oversized text fields.
    manifest.items[4].summary = "x" * 5000
    manifest.items[5].title = "y" * 1000
    manifest.items[6].meta["blurb"] = "z" * 9000
    manifest.items[7].meta.update({f"k{j}": j for j in range(120)})

    # 5. Names that are hostile to a filesystem.
    manifest.items[8].name = 'we:ird/na*me?"<>|.pdf'
    manifest.items[9].name = "  " + "n" * 300 + ".pdf"

    return manifest


class HostileManifestTest(unittest.TestCase):
    """Properties that must hold no matter what the extractors produced."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="dldiary-scale-"))
        cls.cfg = Config(input=cls.tmp / "in", output=cls.tmp / "out")
        cls.manifest = synthetic_manifest()
        for item in cls.manifest.items:
            item.normalize()
        manifest_io.save(cls.manifest, cls.cfg.manifest_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_manifest_is_strict_json(self):
        """What Python will happily write, a browser must still accept."""
        raw = self.cfg.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("NaN", raw.replace("base64,", ""))

        bad: list[str] = []
        json.loads(raw, parse_constant=bad.append)
        self.assertEqual(bad, [], "non-standard JSON constants present")

    def test_no_non_finite_survives(self):
        data = json.loads(self.cfg.manifest_path.read_text(encoding="utf-8"))

        def walk(node, path="$"):
            if isinstance(node, float):
                self.assertTrue(math.isfinite(node), f"{path} is {node}")
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

        walk(data)

    def test_fields_are_capped(self):
        data = json.loads(self.cfg.manifest_path.read_text(encoding="utf-8"))
        for item in data["items"]:
            with self.subTest(item["name"][:30]):
                self.assertLessEqual(len(item["source"].get("url", "")), MAX_URL)
                self.assertLessEqual(len(item.get("summary", "")), MAX_SUMMARY + 1)
                self.assertLessEqual(len(item.get("meta", {})), 40)

    def test_data_uri_is_not_stored_whole(self):
        size = self.cfg.manifest_path.stat().st_size
        self.assertLess(size, 1_000_000,
                        "a single data: URI is dominating the manifest again")

    def test_check_passes_on_a_normalized_manifest(self):
        problems = [p for p in check.run(self.cfg) if p.level == "error"]
        # Previews do not exist in this synthetic corpus; ignore those.
        real = [p for p in problems if "preview" not in p.message]
        self.assertEqual([str(p) for p in real], [])


class ViewerScaleTest(unittest.TestCase):
    """Viewers must represent every entry, and stay a usable size."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="dldiary-viewers-"))
        cls.cfg = Config(input=cls.tmp / "in", output=cls.tmp / "out")
        cls.manifest = synthetic_manifest()
        for item in cls.manifest.items:
            item.normalize()
        manifest_io.save(cls.manifest, cls.cfg.manifest_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_entry_becomes_a_note(self):
        """254 notes for 257 entries must be an error, not a shrug."""
        vault = obsidian_viewer.render(self.manifest, self.cfg)
        notes = list((vault / "items").glob("*.md"))
        self.assertEqual(len(notes), len(self.manifest.items))

    def test_same_stem_different_extension_do_not_collide(self):
        vault = obsidian_viewer.render(self.manifest, self.cfg)
        names = sorted(p.name for p in (vault / "items").glob("*beatles*"))
        self.assertEqual(len(names), 3, names)
        self.assertEqual(len(set(n.lower() for n in names)), 3)

    def test_collision_is_loud_not_silent(self):
        """If dedup ever regresses, the viewer must raise."""
        manifest = Manifest(input="/x")
        for i in range(2):
            manifest.items.append(Item(
                id=f"id{i}", name="same.pdf", ext="pdf", kind="pdf",
                mime="application/pdf", size=1, src_path=f"/x/{i}/same.pdf",
                date="2026-01-01T00:00:00", date_source="birth",
            ))
        # Identical day, name and extension: only the id separates them.
        vault = obsidian_viewer.render(manifest, self.cfg)
        self.assertEqual(len(list((vault / "items").glob("*.md"))), 2)

    def test_html_payload_stays_bounded(self):
        out = html_viewer.render(self.manifest, self.cfg)
        size = out.stat().st_size
        self.assertLess(
            size, PAYLOAD_BUDGET,
            f"diary.html is {size/1024:.0f} KB for "
            f"{len(self.manifest.items)} entries — the view model is leaking "
            f"manifest fields again",
        )

    def test_html_omits_fields_the_page_never_draws(self):
        out = html_viewer.render(self.manifest, self.cfg)
        payload = json.loads(
            out.read_text(encoding="utf-8")
            .split('id="data">', 1)[1].split("</script>", 1)[0]
        )
        row = payload["items"][0]
        for leaked in ("fingerprint", "content_key", "date_candidates",
                       "indexed_at", "mime"):
            self.assertNotIn(leaked, row)

    def test_html_is_parseable_as_strict_json(self):
        out = html_viewer.render(self.manifest, self.cfg)
        blob = (out.read_text(encoding="utf-8")
                .split('id="data">', 1)[1].split("</script>", 1)[0])
        bad: list[str] = []
        data = json.loads(blob, parse_constant=bad.append)
        self.assertEqual(bad, [])
        self.assertEqual(len(data["items"]), len(self.manifest.items))

    def test_embedded_json_cannot_break_out_of_its_script_tag(self):
        manifest = Manifest(input="/x")
        manifest.items.append(Item(
            id="xss", name="</script><script>alert(1)</script>.pdf",
            ext="pdf", kind="pdf", mime="application/pdf", size=1,
            src_path="/x/a.pdf", date="2026-01-01T00:00:00",
            date_source="birth", summary="</script>",
        ))
        out = html_viewer.render(manifest, self.cfg)
        text = out.read_text(encoding="utf-8")
        blob = text.split('id="data">', 1)[1].split("</script>", 1)[0]
        # If the name leaked verbatim, the blob is truncated and invalid.
        json.loads(blob)


class BlurLeakTest(unittest.TestCase):
    """With blur on, no text from a blurred kind may reach any output."""

    def test_nothing_from_a_blurred_kind_is_written_anywhere(self):
        tmp = Path(tempfile.mkdtemp(prefix="dldiary-blurleak-"))
        try:
            cfg = Config(input=tmp / "in", output=tmp / "out",
                         blur_kinds=frozenset({"pdf"}))
            manifest = Manifest(input="/synthetic")
            secret = "Nachname-Geheim-99887766"
            for i in range(6):
                item = Item(
                    id=f"blur{i:08x}", name=f"ticket-{i}.pdf", ext="pdf",
                    kind="pdf", mime="application/pdf", size=2048,
                    src_path=f"/synthetic/ticket-{i}.pdf",
                    date=f"2026-0{i + 1}-15T10:00:00",
                    date_source="quarantine",
                )
                # An extractor that ignored the policy would leave these.
                item.title = secret
                item.summary = f"{secret} seat 14F"
                item.meta = {"author": secret, "pages": 2}
                manifest.items.append(item)
                item.normalize()

            manifest_io.save(manifest, cfg.manifest_path)
            html_viewer.render(manifest, cfg)
            obsidian_viewer.render(manifest, cfg)

            # check() must notice, rather than the leak passing silently.
            errors = [p for p in check.run(cfg) if p.level == "error"]
            self.assertTrue(
                any("text stored for a blurred kind" in p.message
                    for p in errors),
                "check did not flag stored text for a blurred kind",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DeepScaleTest(unittest.TestCase):
    """A bigger corpus, to keep an eye on how cost grows."""

    def test_two_thousand_entries(self):
        tmp = Path(tempfile.mkdtemp(prefix="dldiary-deep-"))
        try:
            cfg = Config(input=tmp / "in", output=tmp / "out")
            manifest = synthetic_manifest(2000, hostile=False)
            for item in manifest.items:
                item.normalize()
            manifest_io.save(manifest, cfg.manifest_path)

            reloaded = manifest_io.load(cfg.manifest_path)
            self.assertEqual(len(reloaded.items), 2000)

            out = html_viewer.render(manifest, cfg)
            per_item = out.stat().st_size / 2000
            self.assertLess(per_item, 900,
                            f"{per_item:.0f} bytes of HTML per entry")

            vault = obsidian_viewer.render(manifest, cfg)
            self.assertEqual(
                len(list((vault / "items").glob("*.md"))), 2000
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EmptyCorpusTest(unittest.TestCase):
    """The degenerate end of the range deserves the same care."""

    def test_empty_input_folder(self):
        tmp = Path(tempfile.mkdtemp(prefix="dldiary-empty-"))
        try:
            (tmp / "in").mkdir()
            cfg = Config(input=tmp / "in", output=tmp / "out")
            manifest, report = index(cfg)
            self.assertEqual(report.scanned, 0)

            manifest_io.save(manifest, cfg.manifest_path)
            data = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(data["items"], [])
            self.assertEqual(data["stats"]["date_range"], [])

            out = html_viewer.render(manifest, cfg)
            self.assertIn("Downloads", out.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

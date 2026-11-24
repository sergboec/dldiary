# Downloads as a Diary

Your Downloads folder is already a journal — every ticket, screenshot, invoice
and video that passed through your life landed there with a timestamp. This
tool reads it and turns it into something you can actually look at, so the
originals become deletable.

One indexing pass produces exactly two things:

```
previews/     many preview assets — page renders, video frames, thumbnails, cards
index.json    ONE manifest: which previews belong to what, and when it happened
```

Everything else is a **viewer** that reads the manifest and nothing else:
a static HTML timeline, and an Obsidian vault with a Base. Viewers own no
truth — delete one, re-render, nothing is lost.

**The input folder is never modified.** No moving, no renaming, no deleting,
and archives are listed but never extracted.

---

## Install

The tool never installs anything or creates an environment. You do:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Versions are pinned exactly. Pillow is required; the rest degrade gracefully
(a missing `av` means videos get a plain card and one warning, not a crash).

| package | why |
|---|---|
| `pillow==12.3.0` | images, thumbnails, text cards — **required** |
| `pymupdf==1.28.2` | PDF page rendering, text and metadata; also SVG and EPUB |
| `av==17.1.0` | video frames and container metadata, via libav |
| `xattr==1.3.1` | macOS download date, source app and source URL |
| `mutagen==1.48.1` | audio tags and embedded cover art |

No command-line tools are used — no ffmpeg, sips, qlmanage or exiftool.

## Run

```bash
.venv/bin/python -m dldiary
```

That indexes `./downloads_test` into the current folder. For the real thing:

```bash
.venv/bin/python -m dldiary --input ~/Downloads --output ~/diary
```

Output:

```
<output>/
├── index.json        the manifest — the single point of entry
├── previews/         the preview assets it points at
├── obsidian/         viewer 1 — open this folder as a vault
└── html/diary.html   viewer 2 — open in any browser
```

### Commands

| | |
|---|---|
| `dldiary all` | index, then render both viewers (default) |
| `dldiary index` | previews + manifest only |
| `dldiary render` | rebuild viewers from the existing manifest, no re-extraction |

### Options

| flag | effect |
|---|---|
| `-i, --input` | folder to index, read-only (default `./downloads_test`) |
| `-o, --output` | where everything is written (default `.`) |
| `--thumb-size PX` | longest edge of a preview (default 512) |
| `--video-frames N` | extra frames per video beyond the poster (default 3) |
| `--skip-larger-than` | e.g. `2G` — still indexed, but not opened |
| `--only html\|obsidian` | render just one viewer |
| `--obsidian-grain day` | one note per day instead of one per file |
| `--inline-assets` | base64 the previews into `diary.html` — one portable file |
| `-n, --dry-run` | report what would happen, write nothing |
| `-v, --verbose` | per-file logging |

### Worked examples

**Try it on the sample folder first.** Writes into the current directory, so
you can see the shape of the output before pointing it at anything real:

```bash
.venv/bin/python -m dldiary all --input ./downloads_test --output ./sample
```

**Look before you leap.** Lists what would be indexed and writes nothing —
worth doing once against a folder with thousands of files in it:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary --dry-run
```

**The real run.** A 24 GB folder with a couple of films in it takes about
half a minute; `--skip-larger-than` indexes the giants as entries but does
not open them, which is most of the time saved:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary --skip-larger-than 2G -v
```

**One file you can send.** Base64s the previews into the page, so
`diary.html` stands alone with no `assets/` folder beside it:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary --only html --inline-assets
```

**Re-render without re-reading 24 GB.** After editing a viewer, or to
switch Obsidian to one note per day, rebuild from the existing manifest:

```bash
.venv/bin/python -m dldiary render --output ~/diary --obsidian-grain day
```

**Bigger thumbnails for a photo-heavy folder**, and more frames per video:

```bash
.venv/bin/python -m dldiary all --input ~/Pictures/inbox --output ~/diary-photos --thumb-size 900 --video-frames 5
```

**A yearly ritual.** The tool is one-shot, so each year gets its own folder
and the previous one is left alone:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary-2026
```

Then clear out Downloads — the diary no longer depends on it.

The tool is **one-shot**. There is no reuse cache and no `--force`: every run
reads every file and builds the manifest from nothing. That matches what it
is for — run it, get a diary, then clear the Downloads folder out.

Two things follow. A second run into the same output folder *replaces* the
diary rather than adding to it, so if you have already emptied Downloads,
point the next run at a different `--output`. And the generated Obsidian
notes are pure output: the whole folder is rewritten each time, so nothing
you edit inside it survives.

---

## What ends up in an entry

The richest signal is free, and most people don't know it's there. macOS
stores on every downloaded file:

- `com.apple.quarantine` — when it was downloaded, and which app fetched it
- `kMDItemWhereFroms` — the URL it came from

In a real 266-file Downloads folder, 239 files carry the first and 115 the
second. So entries read like this with no guessing at all:

```
31-10-2026--Ticket-01…pdf   26 Oct 16:34  Firefox   mail-attachment.googleusercontent.com
                            "Jewgeni Onegin — Schillertheater Berlin, 31.10.2026, 19:30"
image.png                   23 Oct 17:50  Telegram
ticketdirect-1.pdf          24 Oct 11:03  Firefox   eventim.de
```

### Previews by file type

| kind | preview | extra |
|---|---|---|
| image | thumbnail, EXIF rotation applied | GPS coordinates |
| pdf | page 1 + up to 3 more pages | page count, author, opening text |
| video | poster + 3 frames across the running time | duration, resolution, fps, codec |
| audio | embedded cover art, else a card | artist, album, duration, bitrate |
| text / code | rendered card of the first lines | encoding, line count, HTML `<title>` |
| docx / pptx / xlsx | the document's own embedded thumbnail, else a card | author, slide count |
| zip | a card listing the contents | entry count, uncompressed size |
| anything else | a card with the type and size | |

Nothing ever ends up without a preview, so the timeline has no holes.

### Which date an entry gets

The file's modification time, and nothing else — the same value Finder shows
in its **Date Modified** column.

There used to be six competing sources (EXIF capture date, container creation
time, a date spelled out in the filename, the download timestamp, filesystem
birth time, mtime), resolved by precedence and recorded in a `date_source`
field. Measured over 265 real entries, that machinery moved 38 of them off
their mtime day — and two thirds of what it collected could never win
anything. A screenshot taken in 2019 and re-saved today is, for a diary about
a downloads folder, a file that arrived today. One source of truth, no guess
to audit.

The download timestamp is still read from `com.apple.quarantine` and kept as
`source.downloaded_at`; it is provenance, not the anchor.

---

## The viewers

### HTML

`html/diary.html` — one file, no CDN, no build step, no framework.

- Year heatmap with month and weekday labels, coloured by each day's dominant file type; click a day to jump to it. Years holding only a file or two are folded into an "Earlier" row instead of getting 365 cells of empty grid
- A continuous month-by-month grid with day markers inline, rather than one row per day. A quiet day costs one tile, not a whole row with an empty gutter beside it — which is the difference between a 17,000 px page and a 34,000 px one
- Months past the first few are built as shells and filled as you approach them, so opening the page is cheap however long the diary gets
- Documents are letterboxed and photos are cropped, because a PDF page cropped to 4:3 is a white band
- Filter by kind, source app, source domain, or free text over names and extracted text
- Click any card for a lightbox with every preview, full metadata and a `file://` link to the original

The page embeds a *view model*, not the manifest: no content hashes,
rejected date candidates or absolute paths, since it draws none of them. For 257 entries that is the difference between 1.7 MB and 0.28 MB.

### Obsidian

`obsidian/` is a vault folder. Open it directly, or move `items/` and
`previews/` into a vault you already have.

- One note per file, with the preview as a `preview:` property
- `Downloads Diary.base` with three views: a **Gallery** of cover images, a sortable **Everything** table, and **By source**
- `--obsidian-grain day` collapses to one note per day with a gallery in the body

The notes are generated. They are rewritten on every render, so don't keep
anything in them except a corrected `date:`.

---

## The manifest is a contract

One producer writes `index.json`; several consumers read it. When nothing
enforces that contract, a single bad value takes everything down quietly —
and did. Two photos with an unreadable EXIF GPS rational produced
`"gps":[NaN,NaN]`; Python emits that happily, `JSON.parse` refuses it, and a
257-entry page rendered blank with no error anywhere. Four characters.

Three rules now hold the line, and they are worth keeping if you extend this:

**Strict JSON, always.** `manifest.save` sanitizes non-finite floats and
writes with `allow_nan=False`, so a bad value fails loudly at write time
rather than silently at read time. The HTML viewer builds its own payload,
so it runs the same sanitizer — a guard in one place is not a guard.

**Untrusted input is bounded at the boundary.** Extractors read files other
people made. A `kMDItemWhereFroms` can be a `data:` URI carrying an entire
PNG — one such URL was 70% of a 1.8 MB manifest. `Item.normalize()` runs
once, right after extraction, and caps URLs, titles, summaries and metadata.
Anything shortened says so.

**A viewer that drops a row must fail.** Obsidian note names once used the
filename *stem*, so `beatles.png` and `beatles.omr` on one day collided and
three entries disappeared without a word. Names now carry the extension, and
the viewer asserts that note count equals entry count.

All of this is enforced where the data is produced — `sanitize()` and
`Item.normalize()` on the way into the manifest, and the viewer's own parity
assertion on the way out — rather than by a separate pass that re-reads what
the same run has just written.

## Tests

```bash
.venv/bin/python tests/test_smoke.py
```

```bash
.venv/bin/python tests/test_scale.py
```

**`test_smoke.py`** — 30 integration tests against the eight real files in
`downloads_test/`: extraction, dates, provenance, the manifest contract,
both viewers. It asserts that the input folder is byte-for-byte
unchanged by a run and that the ZIP is never extracted.

**`test_scale.py`** — the tests that were missing. The smoke suite passed
while the real page was blank, because its corpus had neither volume nor bad
values. This one builds a synthetic 420-entry corpus carrying each failure
actually seen in the wild — a NaN in metadata, a megabyte `data:` URI, two
files sharing a day and a stem, hundreds of single-item days, a filename
containing `</script>` — and asserts the properties that must hold
regardless: strict JSON, viewer parity, bounded payload, no manifest-only
fields leaking into the page. A 2000-entry case watches how cost grows.

If you add an extractor, add its pathological case there.

## Layout

```
dldiary/
  manifest.py      index.json — contract, caps, sanitizing, atomic save
  scan.py          top-level walk, stable ids, dates, duplicate keys
  provenance.py    xattr → download date, source app, source URL
  thumbs.py        Pillow resize/encode and the card renderer
  indexer.py       the pass that ties it together
  extractors/      one module per file kind, failures isolated
  viewers/         html.py, obsidian.py — read the manifest, write a view
```

Measured on a real 257-file, 24.8 GB Downloads folder: a full index takes
about 22 s, a re-index with nothing changed 0.3 s.

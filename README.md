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
| `dldiary all` | index, render both viewers, then check (default) |
| `dldiary index` | previews + manifest only |
| `dldiary render` | rebuild viewers from the existing manifest, no re-extraction |
| `dldiary check` | validate the manifest, the previews and the viewers |

### Options

| flag | effect |
|---|---|
| `-i, --input` | folder to index, read-only (default `./downloads_test`) |
| `-o, --output` | where everything is written (default `.`) |
| `--thumb-size PX` | longest edge of a preview (default 512) |
| `--video-frames N` | extra frames per video beyond the poster (default 3) |
| `--skip-larger-than` | e.g. `2G` — still indexed, but not opened |
| `--blur-text-below PT` | blur the text a PDF sets below PT points, keep larger text sharp (default 12) |
| `--blur-pdf` | blur every PDF preview entirely and store no text from one |
| `--blur-radius PX` | override the blur strength (default scales from `--thumb-size`) |
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

**A diary you can show someone.** Blurs the fine print on every PDF — names,
addresses, booking references — while leaving the headline readable, so a
ticket still says what you went to see:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary --blur-text-below
```

**Or hide the PDFs completely**, if even the event names are too much:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary --blur-pdf
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

**Check an existing diary** without rebuilding anything — the thing to run
if a page ever looks wrong:

```bash
.venv/bin/python -m dldiary check --output ~/diary
```

**A yearly ritual.** The tool is one-shot, so each year gets its own folder
and the previous one is left alone:

```bash
.venv/bin/python -m dldiary all --input ~/Downloads --output ~/diary-2026 --blur-pdf
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
| image | thumbnail, EXIF rotation applied | camera, lens, GPS, capture date |
| pdf | page 1 + up to 3 more pages | page count, author, opening text |
| video | poster + 3 frames across the running time | duration, resolution, fps, codec |
| audio | embedded cover art, else a card | artist, album, duration, bitrate |
| text / code | rendered card of the first lines | encoding, line count, HTML `<title>` |
| docx / pptx / xlsx | the document's own embedded thumbnail, else a card | author, created date, slide count |
| zip | a card listing the contents | entry count, uncompressed size |
| anything else | a card with the type and size | |

Nothing ever ends up without a preview, so the timeline has no holes.

### Which date wins

Several dates compete for one file. All candidates are kept in the manifest
and the winner is recorded in `date_source`, so a wrong guess is visible
rather than silent:

1. `exif` — EXIF capture date (photos)
2. `media` — container creation time (video, audio)
3. `filename` — a date written into the name, e.g. `2026-09-13 14.28.18.mp4`
4. `quarantine` — when it was downloaded
5. `birth` — filesystem creation time
6. `mtime` — last modified

Filename matching is strict on purpose: separators are required, and a bare
digit run only counts at exactly eight digits starting with a plausible year.
An invoice called `175802554.pdf` is never read as a date.

Every candidate is kept in the manifest, and the Base has a **Check the
date** view listing entries resting on a weak guess (`birth` or `mtime`).

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
- `Downloads Diary.base` with four views: a **Gallery** of cover images, a sortable **Everything** table, **By source**, and **Check the date** for entries resting on a weak guess
- `--obsidian-grain day` collapses to one note per day with a gallery in the body

The notes are generated. They are rewritten on every render, so don't keep
anything in them except a corrected `date:`.

---

---

## Blurring PDFs

86 of the 257 entries in a real Downloads folder are PDFs, mostly tickets
and invoices. The sensitive part of a ticket is not what you went to see —
it is your name, your street, and the booking and card numbers. On every
ticket I measured, those are set in small type and the interesting part is
set large:

```
17.3pt  JETHRO TULL                  10.0pt  Jewgeni Onegin
11.3pt  TEMPODROM                     9.0pt  Schillertheater, Bismarckstraße 110, 10625 Berlin
10.5pt  Donnerstag 05.Nov.26          8.0pt  Guten Tag Sergei Boitsov,
 9.0pt  Sergei Boitsov                7.0pt  Oper von Pjotr I. Tschaikowski
 7.6pt  Möckernstraße 10, 10963 Berlin
```

So the rule is a font size, and the size is the parameter:

```bash
.venv/bin/python -m dldiary all --blur-text-below        # 12pt
.venv/bin/python -m dldiary all --blur-text-below 10     # keep more readable
```

Text below the threshold is blurred in the preview **and not stored** —
it is kept out of the summary, the title, `index.json`, the HTML and the
notes. Text at or above it stays sharp and searchable. The blur is baked
into the `.webp`, so no sharp copy exists for a viewer, a screenshot or a
recipient to get at, and only the rectangles holding small text are
touched, so the page still looks like the page.

The default of 12 was measured, not picked. Across 84 real PDFs — tickets,
invoices, bank statements — counting how many still showed a name or a
street at each threshold:

| threshold | PDFs leaking a name or street | PDFs keeping a usable title |
|---|---|---|
| 10 pt | 19 | 81 |
| 10.5 pt | 6 | 74 |
| **12 pt** | **2** | **69** |
| 14 pt | 2 | 48 |

12 sits on a plateau: going to 14 costs 21 more titles and prevents nothing.
The two that survived at 12 were CVs, where the name *is* the heading at
28pt — no size rule reaches those. **Check it against your own documents**;
if a name survives, raise it.

Document metadata has no font size, so `author`, `subject` and `keywords`
are dropped whenever any blurring is on. That matters more than it sounds:
in this corpus the author field held five different people's names and,
once, a full street address.

Three things it does not cover:

* **Images.** Barcodes, QR codes, signatures and scanned pages are not
  text, so a size rule cannot see them. They stay sharp.
* **PDFs with no text layer.** There is nothing to measure, so the whole
  page is blurred rather than left readable. The entry records
  `blurred_whole_page`.
* **Filenames.** `31-10-2026--Ticket-01--28302466111-1.pdf` still shows its
  own name; nothing here renames your files, and a file called
  `conference - Yourname.pptx` announces itself.
* **Surgical treatment for Office files.** A `.docx` or `.pptx` has no
  usable font size to filter on — PowerPoint declares none at all on its
  runs, inheriting them from slide layouts and masters. So Office files get
  the blunt treatment instead: either blur flag blurs their preview whole,
  drops their text, and drops `author`, `subject` and `lastModifiedBy`.
  That last one is what actually leaked here — across this folder those
  fields held five different people's names.
* **Your home directory.** Every entry stores the file's path, so
  `/Users/yourname/` appears throughout `index.json` and the page.

### The blunt version

`--blur-pdf` blurs every PDF preview end to end and stores no text from one
at all — no summary, no title, no `author`, `subject` or `keywords`. It
cannot miss, which the size rule can, and it covers barcodes. `pages`,
`producer`, `format` and the creation date stay either way: structural, not
personal, and the dates still feed date resolution.

`dldiary check` errors if any preview of a blurred kind is unblurred, or if
text was stored for one anyway.

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

`dldiary check` enforces all of this from outside, against what is on disk:

```bash
.venv/bin/python -m dldiary check --output ~/diary
```

It parses the way a browser would, confirms every preview exists and matches
its recorded dimensions, finds duplicate ids, oversized fields, orphaned
previews and viewers that lost entries. `dldiary all` runs it automatically.

## Tests

```bash
.venv/bin/python tests/test_smoke.py
```

```bash
.venv/bin/python tests/test_scale.py
```

**`test_smoke.py`** — 33 integration tests against the eight real files in
`downloads_test/`: extraction, dates, provenance, blurring, the manifest
schema, both viewers. It asserts that the input folder is byte-for-byte
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
  manifest.py      index.json — schema, caps, sanitizing, atomic save
  check.py         standalone validation of manifest, previews and viewers
  scan.py          top-level walk, stable ids, duplicate keys
  provenance.py    xattr → download date, source app, source URL
  dates.py         candidates and precedence
  thumbs.py        Pillow resize/encode and the card renderer
  indexer.py       the pass that ties it together
  extractors/      one module per file kind, failures isolated
  viewers/         html.py, obsidian.py — read the manifest, write a view
```

Measured on a real 257-file, 24.8 GB Downloads folder: a full index takes
about 22 s, a re-index with nothing changed 0.3 s.

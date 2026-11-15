"""The HTML viewer: one self-contained page built from the manifest.

No CDN, no build step, no framework. The manifest is embedded as JSON and a
few hundred lines of vanilla JavaScript turn it into a year heatmap and a
month-by-month timeline of preview cards.

Previews are hardlinked into `html/assets/` by default, so the page costs no
extra disk. `--inline-assets` base64-inlines them instead, producing a single
file you can send to someone.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .. import manifest as manifest_io
from ..config import Config, human_size
from ..manifest import Manifest
from ..thumbs import KIND_COLORS
from . import assets

_PLACEHOLDER = "/*__DLDIARY_DATA__*/"


def render(manifest: Manifest, cfg: Config) -> Path:
    """Write `html/diary.html` and return its path."""
    html_dir = cfg.html_dir
    html_dir.mkdir(parents=True, exist_ok=True)

    payload = _payload(manifest, cfg, html_dir)
    document = (
        _SHELL
        .replace("/*__CSS__*/", _CSS)
        .replace("/*__JS__*/", _JS)
        .replace(_PLACEHOLDER, _encode(payload))
    )

    out = html_dir / "diary.html"
    out.write_text(document, encoding="utf-8")
    return out


# -- data -------------------------------------------------------------------


def _payload(manifest: Manifest, cfg: Config, html_dir: Path) -> dict:
    """The view model: only what the page actually draws.

    Deliberately not the manifest. The manifest carries fingerprints, inode
    numbers, every rejected date candidate and absolute source paths — none
    of which this page renders, and all of which it used to embed. Emitting
    a projection instead took the page from 1.7 MB to a third of that, and
    keeps a change to the manifest from silently changing the page's weight.
    """
    referenced = [p.path for item in manifest.items for p in item.previews]

    if cfg.inline_assets:
        resolve = _inliner(cfg.output)
    else:
        mapping = assets.mirror_previews(
            referenced, cfg.output, html_dir / "assets", prefix="assets/"
        )
        assets.prune(html_dir / "assets", {Path(p).name for p in referenced})
        resolve = mapping.get

    items = []
    for item in manifest.sorted_items(newest_first=True):
        previews = []
        for preview in item.previews:
            src = resolve(preview.path)
            if src:
                previews.append({"src": src, "role": preview.role})

        row: dict = {
            "id": item.id,
            "name": item.name,
            "kind": item.kind,
            "ext": item.ext,
            "sizeh": human_size(item.size),
            "date": item.date,
            "previews": previews,
        }
        # Everything below is optional: omitting empties rather than writing
        # `"": ""` 257 times is most of the remaining size.
        if item.title and item.title != item.name:
            row["title"] = item.title
        if item.summary:
            row["summary"] = item.summary
        if item.date_source not in ("quarantine", ""):
            row["dateSource"] = item.date_source
        for key, prop in (("app", "app"), ("domain", "domain")):
            if item.source.get(key):
                row[prop] = item.source[key]
        # A data: URI is a payload, not somewhere you can go back to.
        url = item.source.get("url", "")
        if url and not item.source.get("url_is_data"):
            row["url"] = url
        if item.src_path:
            row["path"] = item.src_path
        if item.meta:
            row["meta"] = item.meta
        if item.missing:
            row["missing"] = True
        if item.error:
            row["error"] = item.error
        items.append(row)

    stats = manifest.build_stats()
    return {
        "generatedAt": manifest.generated_at,
        "input": manifest.input,
        "stats": {
            "items": stats["items"],
            "bytesh": human_size(stats["bytes"]),
            "range": stats["date_range"],
            "duplicates": stats["duplicates"],
            "missing": stats["missing"],
        },
        "byDay": {
            day: bucket["count"]
            for day, bucket in manifest.build_by_day().items()
        },
        "dayKind": _dominant_kinds(manifest),
        "kinds": stats["kinds"],
        "apps": stats["apps"],
        "domains": stats["domains"],
        "colors": {k: "#%02x%02x%02x" % v for k, v in KIND_COLORS.items()},
        "items": items,
    }


def _dominant_kinds(manifest: Manifest) -> dict[str, str]:
    """The kind that best characterises each day, for heatmap colour.

    Computed here rather than in the browser: it is a fixed property of the
    data, and recomputing it on every filter change was pointless work.
    """
    tally: dict[str, dict[str, int]] = {}
    for item in manifest.items:
        if item.day:
            tally.setdefault(item.day, {})
            tally[item.day][item.kind] = tally[item.day].get(item.kind, 0) + 1
    return {
        day: max(kinds.items(), key=lambda kv: (kv[1], kv[0]))[0]
        for day, kinds in tally.items()
    }


def _encode(payload: dict) -> str:
    """Serialise the view model for embedding in the page.

    Two hazards, both of which have bitten:

    * `NaN` and `Infinity` — Python writes them, `JSON.parse` refuses them,
      and the page renders blank with no clue why. The manifest guards
      against this on save, but a viewer builds its own JSON, so the guard
      has to be here too rather than somewhere upstream.
    * `</script>` inside a filename would close the tag early and truncate
      the data. Escaping the angle brackets keeps the JSON valid and the
      string intact.
    """
    text = json.dumps(
        manifest_io.sanitize(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _inliner(output: Path):
    """Return a resolver that turns a preview path into a data URI."""
    cache: dict[str, str] = {}

    def resolve(rel: str) -> str:
        if rel in cache:
            return cache[rel]
        source = output / rel
        if not source.exists():
            return ""
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        cache[rel] = f"data:image/webp;base64,{encoded}"
        return cache[rel]

    return resolve


# -- document ---------------------------------------------------------------


_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Downloads Diary</title>
<style>/*__CSS__*/</style>
</head>
<body>
<header class="masthead">
  <div class="wrap">
    <h1>Downloads&nbsp;Diary</h1>
    <p class="lede" id="lede"></p>
  </div>
</header>

<div class="controls" id="controls">
  <div class="wrap controls-inner">
    <input type="search" id="q" placeholder="Search names, titles, text…"
           autocomplete="off" spellcheck="false">
    <div class="chips" id="kindChips"></div>
    <select id="appFilter"><option value="">any app</option></select>
    <select id="domainFilter"><option value="">any source</option></select>
    <button id="reset" type="button" class="ghost">Reset</button>
    <span class="count" id="count"></span>
  </div>
</div>

<main class="wrap">
  <section id="heatmap" class="heatmap"></section>
  <section id="timeline" class="timeline"></section>
  <p id="empty" class="empty" hidden>Nothing matches those filters.</p>
</main>

<div class="lightbox" id="lightbox" hidden>
  <button class="lb-close" id="lbClose" aria-label="Close">&times;</button>
  <button class="lb-nav lb-prev" id="lbPrev" aria-label="Previous">&#8249;</button>
  <button class="lb-nav lb-next" id="lbNext" aria-label="Next">&#8250;</button>
  <div class="lb-body">
    <div class="lb-stage"><img id="lbImg" alt=""><div class="lb-strip" id="lbStrip"></div></div>
    <aside class="lb-meta" id="lbMeta"></aside>
  </div>
</div>

<script type="application/json" id="data">/*__DLDIARY_DATA__*/</script>
<script>/*__JS__*/</script>
</body>
</html>
"""

_CSS = """
:root{
  --bg:#15171a; --surface:#1c1e22; --surface-2:#21242a; --line:#2b2f37;
  --fg:#e6e8ec; --dim:#8c93a0; --faint:#5e6672; --accent:#6ea8fe;
  --radius:10px; --cell:11px; --cellgap:3px;
  --sans:ui-sans-serif,-apple-system,"SF Pro Text",Inter,system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);
  font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
a{color:var(--accent)}

/* masthead */
.masthead{padding:46px 0 22px}
.masthead h1{margin:0;font-size:clamp(26px,4vw,40px);font-weight:620;letter-spacing:-.02em}
.lede{margin:8px 0 0;color:var(--dim);font-size:14px}
.lede b{color:var(--fg);font-weight:600}

/* controls */
.controls{
  position:sticky;top:0;z-index:30;background:rgba(21,23,26,.88);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line);
}
.controls-inner{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-block:11px}
input[type=search],select,button{
  font:inherit;font-size:13px;color:var(--fg);background:var(--surface);
  border:1px solid var(--line);border-radius:7px;padding:7px 10px;
}
input[type=search]{min-width:210px;flex:1 1 210px}
input[type=search]:focus,select:focus,button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
select,button{cursor:pointer}
button.ghost:hover{background:var(--surface-2)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{
  display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:6px 10px;
  border-radius:999px;border:1px solid var(--line);background:var(--surface);
  cursor:pointer;user-select:none;color:var(--dim);
}
.chip .dot{width:8px;height:8px;border-radius:50%;background:var(--c)}
.chip[aria-pressed=true]{color:var(--fg);border-color:var(--c);background:var(--surface-2)}
.count{margin-left:auto;color:var(--faint);font-size:12px;font-variant-numeric:tabular-nums}

/* heatmap */
.heatmap{padding:24px 0 4px}
.year{margin-bottom:14px}
.year-head{display:flex;align-items:baseline;gap:10px;margin-bottom:5px}
.year-head h3{margin:0;font-size:13px;font-weight:600;letter-spacing:.04em;color:var(--dim)}
.year-head span{font-size:11.5px;color:var(--faint)}
.cal-scroll{overflow-x:auto;overflow-y:hidden;padding-bottom:2px;scrollbar-width:thin}
.cal-scroll::-webkit-scrollbar{height:6px}
.cal-scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
.cal-scroll::-webkit-scrollbar-track{background:transparent}
.cal{display:inline-grid;grid-template-columns:auto 1fr;gap:0 6px;align-items:start}
.cal-months{
  grid-column:2;display:grid;gap:var(--cellgap);
  font:10px/1 var(--mono);color:var(--faint);letter-spacing:.04em;margin-bottom:4px;
}
.cal-dows{
  grid-column:1;grid-row:2;display:grid;grid-template-rows:repeat(7,var(--cell));
  gap:var(--cellgap);font:9px/var(--cell) var(--mono);color:var(--faint);text-align:right;
}
.hm{
  grid-column:2;grid-row:2;display:grid;grid-auto-flow:column;
  grid-template-rows:repeat(7,var(--cell));gap:var(--cellgap);
}
.hm i{width:var(--cell);height:var(--cell);border-radius:2px;background:#22262c;display:block}
.hm i[data-n]{cursor:pointer}
.hm i[data-n]:hover{outline:1.5px solid var(--fg);outline-offset:1px}
.quiet{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:2px 0 16px}
.quiet-label{font-size:11.5px;color:var(--faint)}
.quiet button{
  font:11.5px var(--mono);padding:4px 9px;border-radius:999px;color:var(--dim);
}
.quiet button:hover{color:var(--fg);border-color:var(--accent)}

/* timeline */
.timeline{padding:10px 0 90px}
.month{margin-bottom:26px;content-visibility:auto}
.month > h2{
  position:sticky;top:52px;z-index:20;margin:0 0 12px;padding:7px 0;display:flex;
  gap:10px;align-items:baseline;font-size:13px;font-weight:600;letter-spacing:.07em;
  text-transform:uppercase;color:var(--dim);
  background:linear-gradient(var(--bg) 72%,transparent);
}
.month > h2 small{font:11px/1 var(--mono);letter-spacing:0;text-transform:none;color:var(--faint)}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}

/* a day marker is one cell in the flow, so a quiet day costs one tile,
   not a whole row with an empty gutter beside it */
.daymark{
  display:flex;flex-direction:column;justify-content:center;align-items:flex-end;
  padding:0 12px 0 0;border-right:1px solid var(--line);scroll-margin-top:120px;
}
.daymark b{font-size:27px;font-weight:600;letter-spacing:-.02em;line-height:1}
.daymark span{font:10px/1.5 var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.07em}
.daymark em{font:10px/1.5 var(--mono);color:var(--faint);font-style:normal;margin-top:3px}

/* card */
.card{
  margin:0;background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden;cursor:pointer;
  transition:border-color .13s ease,transform .13s ease;
  display:flex;flex-direction:column;
}
.card:hover{border-color:var(--c);transform:translateY(-2px)}
.card:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.thumb{position:relative;aspect-ratio:4/3;background:#101216;overflow:hidden;border-bottom:1px solid var(--line)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
/* documents are the wrong shape for a crop: a cropped page is a white band */
.card.fit-contain .thumb img{object-fit:contain}
.badge{
  position:absolute;left:7px;top:7px;font:600 10px/1 var(--mono);letter-spacing:.06em;
  text-transform:uppercase;padding:4px 6px;border-radius:4px;background:var(--c);color:#fff;
}
.pages{
  position:absolute;right:7px;bottom:7px;font:500 10px/1 var(--mono);padding:4px 6px;
  border-radius:4px;background:rgba(0,0,0,.66);color:#fff;
}
.card figcaption{padding:9px 10px 11px;min-width:0}
.card .name{
  font-size:12.5px;font-weight:500;line-height:1.32;margin-bottom:3px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden;word-break:break-word;
}
.card .sub{font:11px/1.4 var(--mono);color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card.is-missing{opacity:.55}

/* lightbox */
.lightbox{
  position:fixed;inset:0;z-index:100;background:rgba(10,11,13,.93);
  backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;
}
.lightbox[hidden]{display:none}
.lb-body{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:22px;width:min(1120px,92vw);max-height:88vh}
.lb-stage{display:flex;flex-direction:column;gap:10px;min-width:0}
.lb-stage img{max-width:100%;max-height:72vh;object-fit:contain;border-radius:8px;background:#0e1013}
.lb-strip{display:flex;gap:7px;flex-wrap:wrap}
.lb-strip img{width:56px;height:56px;object-fit:cover;border-radius:5px;cursor:pointer;opacity:.5;border:1px solid var(--line)}
.lb-strip img[aria-current=true]{opacity:1;border-color:var(--accent)}
.lb-meta{overflow-y:auto;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;font-size:13px}
.lb-meta h2{margin:0 0 4px;font-size:16px;line-height:1.3;word-break:break-word}
.lb-meta .fname{font:11px/1.4 var(--mono);color:var(--faint);word-break:break-all;margin-bottom:14px}
.lb-meta .summary{
  color:var(--dim);font-size:12.5px;white-space:pre-wrap;margin:0 0 14px;max-height:170px;
  overflow:auto;padding-left:10px;border-left:2px solid var(--line);
}
.lb-meta dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;margin:0}
.lb-meta dt{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.lb-meta dd{margin:0;font:12px/1.45 var(--mono);word-break:break-word}
.lb-close,.lb-nav{position:absolute;background:none;border:0;color:var(--dim);cursor:pointer;padding:10px;line-height:1}
.lb-close{top:14px;right:18px;font-size:30px}
.lb-nav{top:50%;transform:translateY(-50%);font-size:46px}
.lb-prev{left:12px}
.lb-next{right:12px}
.lb-close:hover,.lb-nav:hover{color:var(--fg)}

.empty{color:var(--faint);text-align:center;padding:60px 0}
.empty code{font:12px var(--mono);color:var(--dim)}

@media (max-width:760px){
  .cards{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
  .daymark b{font-size:21px}
  .lb-body{grid-template-columns:1fr;max-height:92vh;overflow:auto}
  .lb-nav{font-size:32px}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .card{transition:none}
}
"""

_JS = """
(function(){
"use strict";

var DATA;
try {
  DATA = JSON.parse(document.getElementById('data').textContent);
} catch (err) {
  // A blank page tells you nothing. Say what broke, and where.
  document.querySelector('main').innerHTML =
    '<p class="empty">This page could not read its own data:<br><br><code>' +
    String(err.message).replace(/[<>&]/g, '') +
    '</code><br><br>Run <code>dldiary check</code> to find the bad entry.</p>';
  throw err;
}

var MONTHS = ['January','February','March','April','May','June','July',
              'August','September','October','November','December'];
var MON_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var DOW = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

// Cropping suits a photograph and ruins a document: a PDF page cropped to
// 4:3 is a white band. These kinds are letterboxed instead.
var CONTAIN = {pdf:1, document:1, text:1, code:1, archive:1, other:1, audio:1};

// A year with two files in it does not deserve 365 cells of empty grid.
var DENSE_YEAR = 8;
// Months beyond this are built as empty shells and filled on approach.
var WINDOW_AFTER = 6;
var FILL_MARGIN = '1400px';

var state = {q:'', kinds:new Set(), app:'', domain:''};
var visible = [];
var observer = null;

function el(tag, cls, text){
  var n = document.createElement(tag);
  if(cls) n.className = cls;
  if(text != null) n.textContent = text;
  return n;
}
function color(kind){ return DATA.colors[kind] || DATA.colors.other; }
function iso(d){
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') +
         '-' + String(d.getDate()).padStart(2,'0');
}
function hhmm(s){
  if(!s || s.length < 16) return '';
  var t = s.slice(11,16);
  return t === '00:00' ? '' : t;
}
function fmtSize(n){
  var u = ['B','KB','MB','GB','TB'], i = 0;
  while(n >= 1024 && i < u.length-1){ n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}
function bytesOf(it){
  var m = /^([\\d.]+)\\s*(B|KB|MB|GB|TB)$/.exec(it.sizeh || '');
  if(!m) return 0;
  var mult = {B:1, KB:1024, MB:1048576, GB:1073741824, TB:1099511627776};
  return parseFloat(m[1]) * mult[m[2]];
}

/* ---------- header ---------- */
function header(){
  var s = DATA.stats, bits = [];
  bits.push('<b>' + s.items + '</b> entries');
  bits.push('<b>' + s.bytesh + '</b>');
  if(s.range && s.range.length === 2){
    bits.push(s.range[0] === s.range[1] ? s.range[0] : s.range[0] + ' – ' + s.range[1]);
  }
  if(s.duplicates) bits.push(s.duplicates + ' duplicate' + (s.duplicates > 1 ? 's' : ''));
  if(s.missing) bits.push(s.missing + ' no longer on disk');
  document.getElementById('lede').innerHTML = bits.join(' &nbsp;·&nbsp; ');
}

/* ---------- filters ---------- */
function buildControls(){
  var chips = document.getElementById('kindChips');
  Object.keys(DATA.kinds).forEach(function(kind){
    var chip = el('button','chip');
    chip.type = 'button';
    chip.setAttribute('aria-pressed','false');
    chip.style.setProperty('--c', color(kind));
    chip.appendChild(el('span','dot'));
    chip.appendChild(el('span', null, kind + ' ' + DATA.kinds[kind]));
    chip.addEventListener('click', function(){
      if(state.kinds.has(kind)){ state.kinds.delete(kind); chip.setAttribute('aria-pressed','false'); }
      else { state.kinds.add(kind); chip.setAttribute('aria-pressed','true'); }
      apply();
    });
    chips.appendChild(chip);
  });

  fill('appFilter', DATA.apps);
  fill('domainFilter', DATA.domains);

  document.getElementById('q').addEventListener('input', debounce(function(e){
    state.q = e.target.value.toLowerCase().trim(); apply();
  }, 140));
  document.getElementById('appFilter').addEventListener('change', function(e){
    state.app = e.target.value; apply();
  });
  document.getElementById('domainFilter').addEventListener('change', function(e){
    state.domain = e.target.value; apply();
  });
  document.getElementById('reset').addEventListener('click', function(){
    state = {q:'', kinds:new Set(), app:'', domain:''};
    document.getElementById('q').value = '';
    document.getElementById('appFilter').value = '';
    document.getElementById('domainFilter').value = '';
    chips.querySelectorAll('.chip').forEach(function(c){ c.setAttribute('aria-pressed','false'); });
    apply();
  });
}
function fill(id, counts){
  var sel = document.getElementById(id);
  Object.keys(counts).forEach(function(key){
    var o = el('option', null, key + ' (' + counts[key] + ')');
    o.value = key; sel.appendChild(o);
  });
}
function debounce(fn, ms){
  var t; return function(){ var a = arguments, c = this;
    clearTimeout(t); t = setTimeout(function(){ fn.apply(c, a); }, ms); };
}

function matches(it){
  if(state.kinds.size && !state.kinds.has(it.kind)) return false;
  if(state.app && it.app !== state.app) return false;
  if(state.domain && it.domain !== state.domain) return false;
  if(state.q){
    var hay = (it.name + ' ' + (it.title||'') + ' ' + (it.summary||'') + ' ' +
               (it.domain||'') + ' ' + (it.app||'')).toLowerCase();
    if(hay.indexOf(state.q) === -1) return false;
  }
  return true;
}

/* ---------- heatmap ---------- */
function buildHeatmap(counts){
  var host = document.getElementById('heatmap');
  host.innerHTML = '';
  var days = Object.keys(counts);
  if(!days.length) return;

  var max = 1, perYear = {};
  days.forEach(function(d){
    if(counts[d] > max) max = counts[d];
    var y = d.slice(0,4);
    perYear[y] = perYear[y] || {files:0, days:0};
    perYear[y].files += counts[d];
    perYear[y].days++;
  });

  var years = Object.keys(perYear).sort().reverse();
  var quiet = [];

  years.forEach(function(year){
    if(perYear[year].files < DENSE_YEAR){ quiet.push(year); return; }
    host.appendChild(yearGrid(year, counts, max, perYear[year]));
  });

  if(quiet.length) host.appendChild(quietRow(quiet, perYear, counts));
}

function yearGrid(year, counts, max, summary){
  var block = el('div','year');
  var head = el('div','year-head');
  head.appendChild(el('h3', null, year));
  head.appendChild(el('span', null,
    summary.files + ' files on ' + summary.days + ' day' + (summary.days > 1 ? 's' : '')));
  block.appendChild(head);

  var start = new Date(+year, 0, 1);
  var end = new Date(+year, 11, 31);
  var pad = start.getDay();

  var cal = el('div','cal');

  // Month labels, each spanning the weeks it covers.
  var monthRow = el('div','cal-months');
  var spans = monthSpans(+year, pad);
  monthRow.style.gridTemplateColumns =
    spans.map(function(s){ return 'repeat(' + s.weeks + ', var(--cell))'; }).join(' ');
  spans.forEach(function(s){
    var label = el('span', null, s.weeks >= 3 ? MON_SHORT[s.month] : '');
    label.style.gridColumn = 'span ' + s.weeks;
    monthRow.appendChild(label);
  });
  cal.appendChild(monthRow);

  var dows = el('div','cal-dows');
  for(var r = 0; r < 7; r++) dows.appendChild(el('span', null, (r % 2 === 1) ? DOW[r] : ''));
  cal.appendChild(dows);

  var grid = el('div','hm');
  for(var p = 0; p < pad; p++) grid.appendChild(el('i'));
  for(var d = new Date(start); d <= end; d.setDate(d.getDate() + 1)){
    grid.appendChild(cell(iso(d), counts, max));
  }
  cal.appendChild(grid);

  var scroll = el('div','cal-scroll');
  scroll.appendChild(cal);
  block.appendChild(scroll);
  return block;
}

function monthSpans(year, pad){
  var out = [];
  for(var m = 0; m < 12; m++){
    var firstIndex = pad + dayOfYear(new Date(year, m, 1));
    var nextIndex = (m === 11)
      ? pad + dayOfYear(new Date(year, 11, 31)) + 1
      : pad + dayOfYear(new Date(year, m + 1, 1));
    out.push({month:m, weeks: Math.max(1, Math.round((nextIndex - firstIndex) / 7))});
  }
  return out;
}
function dayOfYear(d){
  return Math.floor((d - new Date(d.getFullYear(), 0, 1)) / 86400000);
}

function cell(day, counts, max){
  var i = el('i');
  var n = counts[day];
  if(!n){ i.title = day; return i; }
  i.style.background = color(DATA.dayKind[day] || 'other');
  i.style.opacity = (0.3 + 0.7 * (n / max)).toFixed(2);
  i.dataset.n = n;
  i.title = day + ' — ' + n + ' file' + (n > 1 ? 's' : '');
  i.addEventListener('click', function(){ jumpToDay(day); });
  return i;
}

function quietRow(years, perYear, counts){
  var row = el('div','quiet');
  row.appendChild(el('span','quiet-label','Earlier:'));
  years.forEach(function(year){
    var b = el('button', null, year + ' · ' + perYear[year].files +
      ' file' + (perYear[year].files > 1 ? 's' : ''));
    b.type = 'button';
    b.addEventListener('click', function(){
      var first = Object.keys(counts).filter(function(d){ return d.slice(0,4) === year; }).sort()[0];
      if(first) jumpToDay(first);
    });
    row.appendChild(b);
  });
  return row;
}

function jumpToDay(day){
  var month = document.getElementById('m-' + day.slice(0,7));
  if(month) fillMonth(month);
  var node = document.getElementById('d-' + day);
  if(node) node.scrollIntoView({block:'center'});
  else if(month) month.scrollIntoView({block:'start'});
}

/* ---------- timeline ---------- */
function buildTimeline(){
  var host = document.getElementById('timeline');
  if(observer){ observer.disconnect(); observer = null; }
  host.innerHTML = '';

  document.getElementById('count').textContent =
    visible.length + ' of ' + DATA.items.length + ' shown';
  document.getElementById('empty').hidden = visible.length > 0;

  var byMonth = {};
  var order = [];
  visible.forEach(function(it){
    var key = it.date ? it.date.slice(0,7) : 'undated';
    if(!byMonth[key]){ byMonth[key] = []; order.push(key); }
    byMonth[key].push(it);
  });
  order.sort().reverse();

  observer = new IntersectionObserver(function(entries){
    entries.forEach(function(e){ if(e.isIntersecting) fillMonth(e.target); });
  }, {rootMargin: FILL_MARGIN});

  order.forEach(function(key, index){
    var items = byMonth[key];
    var section = el('section','month');
    section.id = 'm-' + key;
    section._items = items;

    var bytes = items.reduce(function(a, b){ return a + bytesOf(b); }, 0);
    var h2 = el('h2', null, key === 'undated'
      ? 'No date' : MONTHS[+key.slice(5,7) - 1] + ' ' + key.slice(0,4));
    h2.appendChild(el('small', null, items.length + ' · ' + fmtSize(bytes)));
    section.appendChild(h2);

    var grid = el('div','cards');
    section.appendChild(grid);

    // The first months render straight away so the page is useful at once;
    // the rest are shells with a reserved height, filled as they approach.
    if(index < WINDOW_AFTER){
      fillMonth(section);
    } else {
      grid.style.minHeight = estimateHeight(section, items.length) + 'px';
      observer.observe(section);
    }
    host.appendChild(section);
  });
}

function estimateHeight(section, count){
  // Reserve roughly the right height so the scrollbar is honest before a
  // month is filled. Bias low: a short shell just makes the observer fire
  // a little early, while a tall one leaves a visible hole in the page.
  var grid = section.querySelector('.cards');
  var width = (grid && grid.clientWidth) ||
              document.getElementById('timeline').clientWidth || 1100;
  var cols = Math.max(1, Math.floor(width / 182));
  return Math.ceil((count + 1) / cols) * 215;
}

function fillMonth(section){
  if(!section || section._filled) return;
  section._filled = true;
  if(observer) observer.unobserve(section);

  var grid = section.querySelector('.cards');
  var frag = document.createDocumentFragment();
  var lastDay = null;

  section._items.forEach(function(it){
    var day = it.date ? it.date.slice(0,10) : '';
    if(day && day !== lastDay){
      lastDay = day;
      frag.appendChild(dayMark(day, section._items));
    }
    frag.appendChild(card(it));
  });

  grid.appendChild(frag);
  grid.style.minHeight = '';
}

function dayMark(day, items){
  var d = new Date(day + 'T00:00:00');
  var n = items.filter(function(i){ return i.date && i.date.slice(0,10) === day; }).length;
  var mark = el('div','daymark');
  mark.id = 'd-' + day;
  mark.appendChild(el('b', null, String(d.getDate())));
  mark.appendChild(el('span', null, DOW[d.getDay()]));
  mark.appendChild(el('em', null, n + (n > 1 ? ' files' : ' file')));
  return mark;
}

function card(it){
  var fig = el('figure','card' + (it.missing ? ' is-missing' : '') +
                       (CONTAIN[it.kind] ? ' fit-contain' : ''));
  fig.style.setProperty('--c', color(it.kind));
  fig.tabIndex = 0;

  var thumb = el('div','thumb');
  if(it.previews.length){
    var img = el('img');
    img.src = it.previews[0].src;
    img.loading = 'lazy';
    img.decoding = 'async';
    img.alt = it.title || it.name;
    thumb.appendChild(img);
  }
  thumb.appendChild(el('span','badge', it.ext || it.kind));
  var corner = cornerLabel(it);
  if(corner) thumb.appendChild(el('span','pages', corner));
  fig.appendChild(thumb);

  var cap = el('figcaption');
  cap.appendChild(el('div','name', it.title || it.name));
  var sub = [hhmm(it.date), it.sizeh, it.domain || it.app].filter(Boolean);
  cap.appendChild(el('div','sub', sub.join(' · ')));
  fig.appendChild(cap);

  fig.addEventListener('click', function(){ openLightbox(it); });
  fig.addEventListener('keydown', function(e){
    if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openLightbox(it); }
  });
  return fig;
}

function cornerLabel(it){
  var m = it.meta || {};
  if(m.duration_human) return m.duration_human;
  if(m.pages > 1) return m.pages + ' pp';
  if(m.entries) return m.entries + ' files';
  if(m.slides) return m.slides + ' slides';
  if(m.width && m.height) return m.width + '×' + m.height;
  return '';
}

/* ---------- lightbox ---------- */
var lb = {open:false, item:null, frame:0};

function openLightbox(it){
  lb.open = true; lb.item = it; lb.frame = 0;
  document.getElementById('lightbox').hidden = false;
  document.body.style.overflow = 'hidden';
  paintLightbox();
}
function closeLightbox(){
  lb.open = false;
  document.getElementById('lightbox').hidden = true;
  document.body.style.overflow = '';
}
function step(delta){
  var i = visible.indexOf(lb.item);
  if(i === -1) return;
  lb.item = visible[(i + delta + visible.length) % visible.length];
  lb.frame = 0;
  paintLightbox();
}

function paintLightbox(){
  var it = lb.item;
  var previews = it.previews || [];
  var img = document.getElementById('lbImg');
  img.src = previews.length ? previews[lb.frame].src : '';
  img.alt = it.title || it.name;

  var strip = document.getElementById('lbStrip');
  strip.innerHTML = '';
  if(previews.length > 1){
    previews.forEach(function(p, i){
      var t = el('img');
      t.src = p.src;
      t.setAttribute('aria-current', i === lb.frame ? 'true' : 'false');
      t.addEventListener('click', function(){ lb.frame = i; paintLightbox(); });
      strip.appendChild(t);
    });
  }

  var meta = document.getElementById('lbMeta');
  meta.innerHTML = '';
  meta.appendChild(el('h2', null, it.title || it.name));
  if(it.title && it.title !== it.name) meta.appendChild(el('div','fname', it.name));
  if(it.summary) meta.appendChild(el('p','summary', it.summary));

  var dl = el('dl');
  function row(label, value, href){
    if(!value) return;
    dl.appendChild(el('dt', null, label));
    var dd = el('dd');
    if(href){
      var a = el('a', null, value);
      a.href = href; a.rel = 'noreferrer noopener';
      dd.appendChild(a);
    } else { dd.textContent = value; }
    dl.appendChild(dd);
  }

  var when = it.date ? new Date(it.date) : null;
  row('when', when && !isNaN(when) ? when.toLocaleString() : 'unknown');
  row('date from', it.dateSource || 'quarantine');
  row('kind', it.kind + (it.ext && it.ext !== it.kind ? ' · ' + it.ext : ''));
  row('size', it.sizeh);
  row('app', it.app);
  row('source', it.domain, it.url || null);

  var m = it.meta || {};
  Object.keys(m).forEach(function(k){
    if(k === 'duration' || k === 'width' || k === 'height') return;
    if(m[k + '_human'] !== undefined) return;     // show the readable twin only
    var v = m[k];
    if(v == null || v === '' || typeof v === 'object') return;
    row(k.replace(/_human$/,'').replace(/_/g,' '), String(v));
  });
  if(m.width && m.height) row('dimensions', m.width + ' × ' + m.height);
  if(it.error) row('error', it.error);
  if(it.missing) row('missing', 'source file is gone');
  row('file', it.path, it.path ? 'file://' + encodeURI(it.path) : null);

  meta.appendChild(dl);
}

/* ---------- wiring ---------- */
function apply(){
  visible = DATA.items.filter(matches);
  var counts = {};
  visible.forEach(function(it){
    if(it.date){ var d = it.date.slice(0,10); counts[d] = (counts[d] || 0) + 1; }
  });
  buildHeatmap(counts);
  buildTimeline();
}

document.getElementById('lbClose').addEventListener('click', closeLightbox);
document.getElementById('lbPrev').addEventListener('click', function(){ step(-1); });
document.getElementById('lbNext').addEventListener('click', function(){ step(1); });
document.getElementById('lightbox').addEventListener('click', function(e){
  if(e.target === this) closeLightbox();
});
document.addEventListener('keydown', function(e){
  if(!lb.open) return;
  if(e.key === 'Escape') closeLightbox();
  else if(e.key === 'ArrowLeft') step(-1);
  else if(e.key === 'ArrowRight') step(1);
});

header();
buildControls();
apply();
})();
"""

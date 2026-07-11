#!/usr/bin/env python3
"""rfc_tool.py — look up, search, and render IETF RFCs, for the /rfc skill.

Stdlib only; no third-party dependencies, no virtualenv. Talks to the RFC
Editor (https://www.rfc-editor.org) and caches everything under
``~/.cache/rfc-skill/`` so repeat lookups are offline and instant.

Three call forms (dispatched by looking for --list / --find / --sync, else IDs):

  rfc_tool.py ID [ID...] [--text|--markdown|--raw|--info] [--toc] [--section S]
              [--grep PAT] [--max-chars N]
      Print one or more RFCs. IDs may be "2119", "rfc2119", "RFC 2119", or a
      subseries name ("BCP14", "STD97", "FYI36") which expands to its RFCs.
      Default output is the original hand-wrapped 72/80-column text, verbatim,
      inside a fenced block. --markdown reflows it into markdown instead.

  rfc_tool.py --list [QUERY] [SURFACES] [FILTERS] [--glob|--regex] [--limit N]
  rfc_tool.py --find [QUERY] ...            (alias of --list)
      List RFCs as "number + title", optionally filtered by QUERY. QUERY is a
      plain case-insensitive substring by default, or a --glob / --regex
      pattern. It is matched against the surfaces selected with --title /
      --abstract / --keywords / --content (default: title + abstract +
      keywords). --content searches the full text of the RFCs it can reach
      (see "Content search" below).

  rfc_tool.py --sync [--limit N] | --update | --cache-info | --clear-cache
      Cache maintenance: bulk-download RFC texts, refresh the metadata index,
      report on, or empty the cache.

FILTERS: --status S, --stream S, --year Y[-Y], --author A, --number LO-HI,
--current (not obsoleted), --obsolete (obsoleted only).

Content search. The metadata index (title/abstract/keywords, ~9.8k entries) is
one 13 MB download and covers most searches. Full text is fetched per-RFC and
cached. So --content searches: every RFC already cached, plus — if the metadata
FILTERS narrow the field to at most --max-fetch (default 120) documents — those,
fetched on demand. Run --sync once to hold the whole corpus locally and make
--content exhaustive and offline.

Run with no arguments (or -h/--help) for a short usage message.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Configuration / constants
# ---------------------------------------------------------------------------

BASE = "https://www.rfc-editor.org"
INDEX_URL = f"{BASE}/rfc-index.xml"
TEXT_URL = BASE + "/rfc/rfc{n}.txt"
INFO_URL = BASE + "/info/rfc{n}"
HTML_URL = BASE + "/rfc/rfc{n}.html"

NS = "{https://www.rfc-editor.org/rfc-index}"

USER_AGENT = "rfc-skill/1.0 (Claude Code skill; +https://www.rfc-editor.org)"
HTTP_TIMEOUT = 30
INDEX_MAX_AGE = 7 * 24 * 3600  # auto-refresh the metadata index after a week

DEFAULT_MAX_CHARS = 50_000  # output budget; overridable with --max-chars
DEFAULT_LIMIT = 60  # listing rows before truncation
DEFAULT_MAX_FETCH = 120  # docs --content may pull over the network in one go
SYNC_WORKERS = 8

SUBSERIES = ("BCP", "STD", "FYI")

# Headings that carry no section number but still open a section.
UNNUMBERED_HEADINGS = {
    "abstract",
    "status of this memo",
    "copyright notice",
    "table of contents",
    "acknowledgement",
    "acknowledgements",
    "acknowledgment",
    "acknowledgments",
    "author's address",
    "authors' addresses",
    "author's addresses",
    "authors addresses",
    "author information",
    "contributors",
    "references",
    "normative references",
    "informative references",
    "full copyright statement",
    "intellectual property",
    "iesg note",
    "index",
    "notices",
}

BOILERPLATE_HEADINGS = {
    "status of this memo",
    "copyright notice",
    "full copyright statement",
    "intellectual property",
    "iesg note",
}


def cache_dir() -> Path:
    env = os.environ.get("RFC_SKILL_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return root / "rfc-skill"


def index_path() -> Path:
    return cache_dir() / "index.json"


def text_path(n: int) -> Path:
    return cache_dir() / "txt" / f"rfc{n}.txt"


def log(msg: str) -> None:
    """Diagnostics go to stderr — stdout is the markdown that lands in context."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Metadata index
# ---------------------------------------------------------------------------


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _docnums(entry: ET.Element, tag: str) -> list[int]:
    out = []
    for group in entry.findall(f"{NS}{tag}"):
        for doc in group.findall(f"{NS}doc-id"):
            n = parse_id(_text(doc))
            if n and n[0] == "RFC":
                out.append(n[1])
    return out


def _also(entry: ET.Element) -> list[str]:
    out = []
    for group in entry.findall(f"{NS}is-also"):
        for doc in group.findall(f"{NS}doc-id"):
            out.append(_text(doc))
    return out


def parse_index_xml(data: bytes) -> dict[str, Any]:
    """Parse rfc-index.xml into a compact dict keyed by RFC number (as str)."""
    root = ET.fromstring(data)
    rfcs: dict[str, Any] = {}
    subseries: dict[str, list[int]] = {}

    for entry in root.findall(f"{NS}rfc-entry"):
        ident = parse_id(_text(entry.find(f"{NS}doc-id")))
        if not ident or ident[0] != "RFC":
            continue
        num = ident[1]

        date = entry.find(f"{NS}date")
        month = _text(date.find(f"{NS}month")) if date is not None else ""
        year = _text(date.find(f"{NS}year")) if date is not None else ""

        abstract = ""
        abs_el = entry.find(f"{NS}abstract")
        if abs_el is not None:
            paras = [_text(p) for p in abs_el.findall(f"{NS}p")]
            abstract = "\n\n".join(p for p in paras if p) or _text(abs_el)

        kw_el = entry.find(f"{NS}keywords")
        keywords = (
            [_text(k) for k in kw_el.findall(f"{NS}kw") if _text(k)]
            if kw_el is not None
            else []
        )

        fmt_el = entry.find(f"{NS}format")
        formats = (
            [_text(f) for f in fmt_el.findall(f"{NS}file-format")]
            if fmt_el is not None
            else []
        )

        pages = _text(entry.find(f"{NS}page-count"))
        rfcs[str(num)] = {
            "n": num,
            "title": _text(entry.find(f"{NS}title")),
            "authors": [
                _text(a.find(f"{NS}name"))
                for a in entry.findall(f"{NS}author")
                if _text(a.find(f"{NS}name"))
            ],
            "month": month,
            "year": int(year) if year.isdigit() else 0,
            "status": _text(entry.find(f"{NS}current-status")),
            "pub_status": _text(entry.find(f"{NS}publication-status")),
            "stream": _text(entry.find(f"{NS}stream")),
            "wg": _text(entry.find(f"{NS}wg_acronym")),
            "area": _text(entry.find(f"{NS}area")),
            "pages": int(pages) if pages.isdigit() else 0,
            "abstract": abstract,
            "keywords": keywords,
            "formats": formats,
            "obsoletes": _docnums(entry, "obsoletes"),
            "obsoleted_by": _docnums(entry, "obsoleted-by"),
            "updates": _docnums(entry, "updates"),
            "updated_by": _docnums(entry, "updated-by"),
            "also": _also(entry),
            "doi": _text(entry.find(f"{NS}doi")),
            "errata": entry.find(f"{NS}errata-url") is not None,
            "draft": _text(entry.find(f"{NS}draft")),
        }

    for series in SUBSERIES:
        tag = f"{NS}{series.lower()}-entry"
        for entry in root.findall(tag):
            name = _text(entry.find(f"{NS}doc-id")).upper().replace(" ", "")
            members = _docnums(entry, "is-also")
            if name:
                subseries[name] = members

    return {
        "fetched": time.time(),
        "source": INDEX_URL,
        "count": len(rfcs),
        "rfcs": rfcs,
        "subseries": subseries,
    }


def load_index(refresh: bool = False, quiet: bool = False) -> dict[str, Any]:
    """Return the metadata index, fetching/refreshing the cache as needed.

    Falls back to a stale cache when the network is unavailable — an offline
    lookup of something already cached must keep working.
    """
    path = index_path()
    cached: dict[str, Any] | None = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None

    fresh_enough = (
        cached is not None
        and not refresh
        and (time.time() - float(cached.get("fetched", 0))) < INDEX_MAX_AGE
    )
    if fresh_enough:
        return cached  # type: ignore[return-value]

    try:
        if not quiet:
            log(f"fetching RFC index ({INDEX_URL}) ...")
        index = parse_index_xml(http_get(INDEX_URL, timeout=120))
    except (urllib.error.URLError, OSError, ET.ParseError, TimeoutError) as e:
        if cached is not None:
            if not quiet:
                log(f"warning: index refresh failed ({e}); using cached index")
            return cached
        raise SystemExit(
            f"error: could not fetch the RFC index ({e}).\n"
            f"  {INDEX_URL} must be reachable at least once to build the cache."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    tmp.replace(path)
    if not quiet:
        log(f"indexed {index['count']} RFCs -> {path}")
    return index


# ---------------------------------------------------------------------------
# Identifier resolution
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^\s*(RFC|BCP|STD|FYI)?[\s\-_]*0*(\d+)\s*$", re.I)


def parse_id(token: str) -> tuple[str, int] | None:
    """"rfc2119" / "RFC-2119" / "2119" / "BCP14" -> ("RFC", 2119) / ("BCP", 14)."""
    m = _ID_RE.match(token or "")
    if not m:
        return None
    series = (m.group(1) or "RFC").upper()
    return series, int(m.group(2))


def resolve_ids(tokens: Iterable[str], index: dict[str, Any]) -> list[int]:
    """Expand user-supplied IDs into a de-duplicated list of RFC numbers."""
    out: list[int] = []
    for tok in tokens:
        ident = parse_id(tok)
        if not ident:
            raise SystemExit(
                f"error: not an RFC identifier: {tok!r}\n"
                "  expected e.g. 2119, rfc2119, RFC-2119, BCP14, STD97 — "
                "or use --list QUERY to search by keyword."
            )
        series, num = ident
        if series == "RFC":
            if str(num) not in index["rfcs"]:
                raise SystemExit(f"error: RFC {num} is not in the index.")
            out.append(num)
        else:
            key = f"{series}{num}"
            members = index["subseries"].get(key)
            if not members:
                raise SystemExit(f"error: {key} is not in the index (or has no RFCs).")
            log(f"{key} -> {', '.join('RFC ' + str(m) for m in members)}")
            out.extend(members)
    seen: set[int] = set()
    return [n for n in out if not (n in seen or seen.add(n))]


# ---------------------------------------------------------------------------
# Full text: fetch + cache
# ---------------------------------------------------------------------------


def get_text(n: int, allow_fetch: bool = True, quiet: bool = False) -> str | None:
    """Return the plain text of RFC n, from cache or the network. None on failure."""
    path = text_path(n)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not allow_fetch:
        return None

    url = TEXT_URL.format(n=n)
    try:
        raw = http_get(url)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if not quiet:
            log(f"warning: could not fetch {url} ({e})")
        return None

    text = raw.decode("utf-8", errors="replace")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # a read-only cache is survivable; we still have the text in hand
    return text


def sync_texts(nums: list[int], quiet: bool = False) -> tuple[int, int]:
    """Bulk-fetch texts for nums (skipping cached). Returns (fetched, failed)."""
    todo = [n for n in nums if not text_path(n).exists()]
    if not todo:
        return 0, 0
    if not quiet:
        log(f"fetching {len(todo)} RFC texts with {SYNC_WORKERS} workers ...")

    done = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=SYNC_WORKERS) as pool:
        futures = {pool.submit(get_text, n, True, True): n for n in todo}
        for fut in concurrent.futures.as_completed(futures):
            if fut.result():
                done += 1
            else:
                failed += 1
            if not quiet and (done + failed) % 250 == 0:
                log(f"  {done + failed}/{len(todo)} ...")
    return done, failed


# ---------------------------------------------------------------------------
# Text structure: pagination, headings, sections
# ---------------------------------------------------------------------------

# Anchored nowhere on purpose: most RFCs end a page with "Postel ... [Page 1]",
# but some (RFC 821) mirror it to "[Page 2] ... Postel". Only ever tested against
# the last line of a page, where a false positive is not a realistic worry.
_FOOTER_RE = re.compile(r"\[Page\s+\S+\]")
_HEADER_RE = re.compile(r"^RFC\s+\d+\s+.*\d{4}\s*$", re.I)
_HEADER_SCAN = 3  # leading lines of a page that may be running-header cruft

_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?(?:\s+(\S.*?))?\s*$")
_APPENDIX_HEADING_RE = re.compile(
    r"^(?:Appendix\s+)?([A-Z](?:\.\d+)*)\.\s+(\S.*?)\s*$"
)


def _norm_header(line: str) -> str:
    """Collapse whitespace and digits so "[Page 3]"-style variance doesn't matter."""
    return re.sub(r"\d+", "#", " ".join(line.split())).lower()


def depaginate(text: str) -> str:
    """Strip form feeds, page footers, and running page headers.

    Header layout is not standardized across five decades of RFCs — 1981-era
    documents run a two-line "September 1981 / Internet Protocol" head, later
    ones a single "RFC 2616  HTTP/1.1  June 1999" line. Rather than encode every
    era's layout, headers are found by the property that defines them: they
    repeat, near-identically, at the top of most pages.
    """
    if "\f" not in text:
        return text.replace("\r\n", "\n")

    pages = [p.split("\n") for p in text.replace("\r\n", "\n").split("\f")]

    # Drop the "[Page N]" footer (indented in old RFCs, flush-left in newer ones).
    for lines in pages:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and _FOOTER_RE.search(lines[-1]):
            lines.pop()

    # Tally the first few non-blank lines of each page after the first; whatever
    # recurs across pages is running-header cruft, whatever doesn't is content.
    freq: dict[str, int] = {}
    for lines in pages[1:]:
        seen = 0
        for line in lines:
            if not line.strip():
                continue
            freq[_norm_header(line)] = freq.get(_norm_header(line), 0) + 1
            seen += 1
            if seen >= _HEADER_SCAN:
                break
    threshold = max(2, int(0.4 * max(1, len(pages) - 1)))

    out: list[str] = []
    for i, lines in enumerate(pages):
        start = 0
        if i > 0:
            dropped = 0
            while start < len(lines) and dropped < _HEADER_SCAN:
                line = lines[start]
                if not line.strip():  # blanks around the header go with it
                    start += 1
                    continue
                if freq.get(_norm_header(line), 0) >= threshold or _HEADER_RE.match(line):
                    start += 1
                    dropped += 1
                    continue
                break
        lines = lines[start:]
        while lines and not lines[-1].strip():
            lines.pop()
        if out and lines:
            out.append("")  # page seam becomes a single blank line
        out.extend(lines)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def heading_of(line: str) -> tuple[str, str, int] | None:
    """Classify a line as a section heading -> (number, title, level).

    Headings sit at column 0 in RFC text; body prose is indented. Returns None
    for anything else.
    """
    if not line or line[0].isspace() or not line.strip():
        return None
    stripped = line.rstrip()
    if len(stripped) > 80 or _HEADER_RE.match(stripped) or _FOOTER_RE.search(stripped):
        return None

    m = _NUM_HEADING_RE.match(stripped)
    if m and m.group(2):
        num = m.group(1)
        return num, m.group(2).strip(), min(6, num.count(".") + 1)

    m = _APPENDIX_HEADING_RE.match(stripped)
    if m:
        num = m.group(1)
        return num, m.group(2).strip(), min(6, num.count(".") + 1)

    key = stripped.rstrip(":").strip().lower()
    if key in UNNUMBERED_HEADINGS:
        return "", stripped.rstrip(":").strip(), 1
    return None


class Section:
    __slots__ = ("num", "title", "level", "start", "end")

    def __init__(self, num: str, title: str, level: int, start: int):
        self.num, self.title, self.level, self.start = num, title, level, start
        self.end = start

    @property
    def label(self) -> str:
        return f"{self.num}. {self.title}" if self.num else self.title


def outline(lines: list[str]) -> list[Section]:
    """Extract the section outline from body lines (not from the printed TOC)."""
    secs: list[Section] = []
    in_toc = False
    for i, line in enumerate(lines):
        h = heading_of(line)
        if not h:
            continue
        num, title, level = h
        # The printed Table of Contents repeats every heading; its entries are
        # indented, so they never reach here — but guard the heading itself.
        if title.lower() == "table of contents":
            in_toc = True
        elif in_toc and num:
            in_toc = False
        secs.append(Section(num, title, level, i))
    for a, b in zip(secs, secs[1:]):
        a.end = b.start
    if secs:
        secs[-1].end = len(lines)
    return secs


def _sec_key(num: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in num.split("."))


def select_sections(secs: list[Section], want: str) -> list[Section]:
    """Pick sections by number ("5.6.1", includes subsections) or title substring."""
    want = want.strip()
    hits: list[Section] = []
    if re.match(r"^[A-Za-z0-9]+(\.\d+)*$", want) and any(
        s.num.lower() == want.lower() for s in secs
    ):
        root = next(s for s in secs if s.num.lower() == want.lower())
        rk = _sec_key(root.num)
        for s in secs:
            if not s.num:
                continue
            k = _sec_key(s.num)
            if k[: len(rk)] == rk:  # the section itself and its descendants
                hits.append(s)
    else:
        low = want.lower()
        hits = [s for s in secs if low in s.title.lower() or low in s.label.lower()]
    return hits


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(
    r"^(\s*)("
    r"o|[-*+]|"  # o / - / * / +
    r"\(?\d+[.)]|"  # 1. / (1) / 1)
    r"\(?[a-zA-Z][.)]|"  # a. / (a)
    r"\[[^\]\s]+\]"  # [RFC2119]  (reference entries)
    r")\s+(\S.*)$"
)
_ARTWORK_RE = re.compile(r"(\+[-+]{2,})|(\|)|(-{3,})|(={3,})|(<-{2,})|(-{2,}>)|(\\|/{2,})")
_COLUMNS_RE = re.compile(r"\S {3,}\S")
_CODEISH_RE = re.compile(r"(=>|::=|=/|\*\(|\bOWS\b|^\s*\S+\s*=\s|\{|\}|;\s*$)")
_RFCREF_RE = re.compile(r"\[RFC\s?(\d{1,5})\]")


def _fence(body: str, lang: str = "text") -> str:
    """Fence body, widening the delimiter if it contains backtick runs."""
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    bar = "`" * max(3, longest + 1)
    return f"{bar}{lang}\n{body}\n{bar}"


def _body_indent(lines: list[str]) -> int:
    counts: dict[int, int] = {}
    for line in lines:
        if not line.strip() or heading_of(line):
            continue
        ind = len(line) - len(line.lstrip())
        counts[ind] = counts.get(ind, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else 3


def _is_artwork(block: list[str], base: int) -> bool:
    """Diagrams, ABNF, tables, aligned author blocks — anything reflow would ruin."""
    if any(_ARTWORK_RE.search(l) for l in block):
        return True
    indents = [len(l) - len(l.lstrip()) for l in block if l.strip()]
    if indents and not _BULLET_RE.match(block[0]):
        if min(indents) >= base + 3:
            return True
        # Set-off material one notch deeper than prose: fence it if it reads like
        # grammar or code rather than a sentence (an indented ABNF production,
        # "1#element => element *( OWS "," OWS element )", must not be reflowed).
        if min(indents) >= base + 2 and any(_CODEISH_RE.search(l) for l in block):
            return True
    columnar = sum(1 for l in block if _COLUMNS_RE.search(l))
    return columnar >= max(2, (len(block) + 1) // 2)


_CAPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ,.'\"()\-]*$")


def _is_caption(block: list[str], base: int) -> bool:
    """A lone centered line under a diagram ("Figure 4.") is a caption, not code."""
    if len(block) != 1:
        return False
    line = block[0]
    indent = len(line) - len(line.lstrip())
    stripped = line.strip()
    return (
        indent >= base + 3
        and len(stripped) < 72
        and bool(_CAPTION_RE.match(stripped))
        and not _ARTWORK_RE.search(stripped)
    )


def _linkify(text: str) -> str:
    return _RFCREF_RE.sub(
        lambda m: f"[RFC {m.group(1)}]({BASE}/rfc/rfc{m.group(1)}.html)", text
    )


def _render_bullets(block: list[str], base: int, links: bool) -> list[str]:
    """Turn a hanging-indent RFC list into markdown bullets."""
    items: list[tuple[int, str, str]] = []  # (indent, marker, text)
    for line in block:
        m = _BULLET_RE.match(line)
        if m:
            indent, marker, rest = len(m.group(1)), m.group(2), m.group(3)
            items.append((indent, marker, rest.strip()))
        elif items:
            items[-1] = (items[-1][0], items[-1][1], items[-1][2] + " " + line.strip())
        else:
            items.append((base, "", line.strip()))

    out = []
    indents = sorted({i for i, _, _ in items})
    for indent, marker, text in items:
        depth = indents.index(indent)
        pad = "  " * depth
        if links:
            text = _linkify(text)
        if re.match(r"^\(?\d+[.)]$", marker):
            num = re.sub(r"\D", "", marker)
            out.append(f"{pad}{num}. {text}")
        elif marker.startswith("["):
            out.append(f"{pad}- **{marker}** {text}")
        else:
            out.append(f"{pad}- {text}")
    return out


def to_markdown(
    text: str,
    num: int,
    links: bool = True,
    keep_toc: bool = False,
    boilerplate: bool = True,
) -> str:
    """Convert RFC plain text into markdown: reflowed prose, real headings,
    fenced artwork. Heuristic by construction — the text is the normative form."""
    lines = depaginate(text).split("\n")
    base = _body_indent(lines)

    out: list[str] = []
    block: list[str] = []
    skip_section = False
    in_toc = False

    def flush() -> None:
        nonlocal block
        if not block:
            return
        if not (skip_section or in_toc):
            if _is_caption(block, base):
                out.append(f"*{block[0].strip()}*")
            elif _is_artwork(block, base):
                dedent = min(len(l) - len(l.lstrip()) for l in block if l.strip())
                body = "\n".join(l[dedent:] if l.strip() else "" for l in block)
                out.append(_fence(body.rstrip()))
            elif _BULLET_RE.match(block[0]):
                out.extend(_render_bullets(block, base, links))
            else:
                # Unwrap to a paragraph. RFC text wraps hyphenated compounds at
                # the hyphen ("comma-\ndelimited"), so a naive space-join yields
                # "comma- delimited"; rejoin those without the space.
                para = ""
                for line in block:
                    piece = line.strip()
                    if para and re.search(r"\w-$", para) and piece[:1].islower():
                        para += piece
                    elif para:
                        para += " " + piece
                    else:
                        para = piece
                para = re.sub(r"\s{2,}", " ", para).strip()
                out.append(_linkify(para) if links else para)
            out.append("")
        block = []

    for line in lines:
        h = heading_of(line)
        if h:
            flush()
            hnum, htitle, level = h
            key = htitle.lower().rstrip(":")
            in_toc = key == "table of contents" and not keep_toc
            skip_section = (not boilerplate) and key in BOILERPLATE_HEADINGS
            if in_toc:
                out.append("## Table of Contents")
                out.append("")
                out.append("*(omitted — run with `--toc` for a section outline)*")
                out.append("")
                continue
            if skip_section:
                continue
            label = f"{hnum}. {htitle}" if hnum else htitle
            out.append(f"{'#' * min(6, level + 1)} {label}")
            out.append("")
            continue
        if not line.strip():
            flush()
        else:
            block.append(line)
    flush()

    md = "\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


# ---------------------------------------------------------------------------
# Rendering: metadata card, document, listings
# ---------------------------------------------------------------------------


def _reflist(nums: list[int]) -> str:
    return ", ".join(f"[RFC {n}]({BASE}/rfc/rfc{n}.html)" for n in nums) or "—"


def render_info(meta: dict[str, Any], index: dict[str, Any]) -> str:
    n = meta["n"]
    lines = [f"## RFC {n} — {meta['title']}", ""]

    also = [a for a in meta.get("also", [])]
    facts = [
        ("Status", meta.get("status") or "—"),
        ("Stream", meta.get("stream") or "—"),
        ("Date", f"{meta.get('month','')} {meta.get('year','')}".strip() or "—"),
        ("Pages", str(meta.get("pages") or "—")),
        ("Authors", ", ".join(meta.get("authors") or []) or "—"),
    ]
    if meta.get("wg"):
        facts.append(("Working group", meta["wg"]))
    if also:
        facts.append(("Also", ", ".join(also)))
    for k, v in facts:
        lines.append(f"- **{k}:** {v}")

    for label, key in (
        ("Obsoletes", "obsoletes"),
        ("Obsoleted by", "obsoleted_by"),
        ("Updates", "updates"),
        ("Updated by", "updated_by"),
    ):
        if meta.get(key):
            lines.append(f"- **{label}:** {_reflist(meta[key])}")

    if meta.get("obsoleted_by"):
        lines.append("")
        lines.append(
            f"> ⚠️ **RFC {n} is obsolete.** It has been superseded by "
            f"{_reflist(meta['obsoleted_by'])}."
        )

    if meta.get("keywords"):
        lines.append(f"- **Keywords:** {', '.join(meta['keywords'])}")
    lines.append(f"- **Links:** [text]({TEXT_URL.format(n=n)}) · "
                 f"[html]({HTML_URL.format(n=n)}) · [info]({INFO_URL.format(n=n)})"
                 + (f" · [errata]({BASE}/errata/rfc{n})" if meta.get("errata") else ""))

    if meta.get("abstract"):
        lines += ["", "**Abstract**", "", meta["abstract"]]
    return "\n".join(lines)


def subtree_end(secs: list[Section], i: int) -> int:
    """End offset of section i *including* its subsections — what --section prints."""
    level = secs[i].level
    for j in range(i + 1, len(secs)):
        if secs[j].level <= level:
            return secs[j].start
    return secs[-1].end


def render_toc(num: int, secs: list[Section], lines: list[str]) -> str:
    out = [f"## RFC {num} — section outline", ""]
    for i, s in enumerate(secs):
        size = sum(len(l) + 1 for l in lines[s.start : subtree_end(secs, i)])
        indent = "  " * (s.level - 1)
        sel = s.num or s.title
        out.append(f"{indent}- `{sel}` {s.label}  *(~{size:,} chars incl. subsections)*")
    out += [
        "",
        f"Print one with: `--section <number-or-title>` "
        f"(e.g. `--section {secs[1].num or secs[1].title!r}`)" if len(secs) > 1 else "",
    ]
    return "\n".join(l for l in out if l is not None)


def render_grep(num: int, lines: list[str], pattern: str, use_regex: bool,
                context: int) -> str:
    try:
        rx = re.compile(pattern if use_regex else re.escape(pattern), re.I)
    except re.error as e:
        raise SystemExit(f"error: bad --grep pattern: {e}")

    hits = [i for i, l in enumerate(lines) if rx.search(l)]
    if not hits:
        return f"## RFC {num} — no lines match `{pattern}`"

    keep: set[int] = set()
    for i in hits:
        keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))

    out = [f"## RFC {num} — {len(hits)} matching line(s) for `{pattern}`", ""]
    chunk: list[str] = []
    prev = -99
    for i in sorted(keep):
        if i != prev + 1 and chunk:
            out.append(_fence("\n".join(chunk)))
            out.append("")
            chunk = []
        chunk.append(f"{i + 1:5d}  {lines[i]}")
        prev = i
    if chunk:
        out.append(_fence("\n".join(chunk)))
    return "\n".join(out)


def render_document(n: int, meta: dict[str, Any], index: dict[str, Any],
                    args) -> str:
    text = get_text(n, allow_fetch=not args.offline)
    if text is None:
        return (
            f"## RFC {n} — {meta['title']}\n\n"
            f"*(text unavailable — could not fetch {TEXT_URL.format(n=n)})*\n\n"
            + render_info(meta, index)
        )

    header = [render_info(meta, index)] if args.info or args.header else []
    if args.info:
        return "\n".join(header)

    body_text = depaginate(text) if (args.markdown or args.toc or args.section
                                     or args.grep or args.depaginate) else text
    lines = body_text.split("\n")

    if args.toc:
        secs = outline(lines)
        if not secs:
            return f"## RFC {n} — no section headings found (short or unusual layout)"
        return "\n".join(header + [render_toc(n, secs, lines)])

    if args.grep:
        return "\n".join(header + [render_grep(n, lines, args.grep, args.regex,
                                               args.context)])

    if args.section:
        secs = outline(lines)
        picked: list[Section] = []
        for want in args.section:
            hits = select_sections(secs, want)
            if not hits:
                avail = ", ".join(s.num for s in secs if s.num)[:400]
                raise SystemExit(
                    f"error: RFC {n} has no section {want!r}.\n"
                    f"  available: {avail}\n"
                    f"  (run with --toc for the full outline)"
                )
            picked.extend(hits)
        seen: set[int] = set()
        picked = [s for s in picked if not (s.start in seen or seen.add(s.start))]
        picked.sort(key=lambda s: s.start)
        chunks = ["\n".join(lines[s.start : s.end]).rstrip() for s in picked]
        body_text = "\n\n".join(chunks)
        lines = body_text.split("\n")

    if args.markdown:
        body = to_markdown(body_text, n, links=not args.no_links,
                           keep_toc=args.keep_toc,
                           boilerplate=not args.no_boilerplate)
    elif args.raw:
        body = body_text.rstrip()
    else:
        title = f"RFC {n}: {meta['title']}"
        body = _fence(body_text.rstrip())
        header = header or [f"## {title}", ""]

    return "\n".join(header + [body])


# ---------------------------------------------------------------------------
# Search / listing
# ---------------------------------------------------------------------------


def make_matcher(query: str, glob: bool, regex: bool):
    if not query:
        return lambda s: False
    if regex:
        try:
            rx = re.compile(query, re.I)
        except re.error as e:
            raise SystemExit(f"error: bad --regex pattern {query!r}: {e}")
        return lambda s: bool(rx.search(s))
    if glob:
        pat = query.lower()
        if not any(c in pat for c in "*?["):
            pat = f"*{pat}*"
        return lambda s: fnmatch.fnmatch(s.lower(), pat)
    low = query.lower()
    return lambda s: low in s.lower()


def year_filter(spec: str):
    m = re.match(r"^(\d{4})(?:\s*-\s*(\d{4}))?$", spec.strip())
    if not m:
        raise SystemExit(f"error: --year wants YYYY or YYYY-YYYY, got {spec!r}")
    lo = int(m.group(1))
    hi = int(m.group(2) or m.group(1))
    return lambda y: lo <= y <= hi


def number_filter(spec: str):
    m = re.match(r"^(\d+)(?:\s*-\s*(\d+))?$", spec.strip())
    if not m:
        raise SystemExit(f"error: --number wants N or LO-HI, got {spec!r}")
    lo = int(m.group(1))
    hi = int(m.group(2) or 99999)
    return lambda n: lo <= n <= hi


def apply_filters(index: dict[str, Any], args) -> list[dict[str, Any]]:
    metas = list(index["rfcs"].values())
    if args.status:
        s = args.status.lower()
        metas = [m for m in metas if s in (m.get("status") or "").lower()]
    if args.stream:
        s = args.stream.lower()
        metas = [m for m in metas if s in (m.get("stream") or "").lower()]
    if args.author:
        a = args.author.lower()
        metas = [m for m in metas
                 if any(a in au.lower() for au in m.get("authors") or [])]
    if args.year:
        ok = year_filter(args.year)
        metas = [m for m in metas if ok(m.get("year") or 0)]
    if args.number:
        ok = number_filter(args.number)
        metas = [m for m in metas if ok(m["n"])]
    if args.current:
        metas = [m for m in metas if not m.get("obsoleted_by")]
    if args.obsolete:
        metas = [m for m in metas if m.get("obsoleted_by")]
    if args.std:
        metas = [m for m in metas if m.get("also")]
    return metas


def search(index: dict[str, Any], args) -> tuple[list[tuple[int, dict, str]], str]:
    """Filter + rank. Returns (scored rows, note-about-content-search)."""
    metas = apply_filters(index, args)
    note = ""

    surfaces = {
        "title": args.title,
        "abstract": args.abstract,
        "keywords": args.keywords,
        "content": args.content,
    }
    if not any(surfaces.values()):  # default surfaces
        surfaces.update(title=True, abstract=True, keywords=True)

    if not args.query:
        rows = [(0, m, "") for m in metas]
        return rows, note

    match = make_matcher(args.query, args.glob, args.regex)

    texts: dict[int, str] = {}
    if surfaces["content"]:
        narrowed = any([args.status, args.stream, args.year, args.author,
                        args.number, args.current, args.obsolete])
        cached = {m["n"] for m in metas if text_path(m["n"]).exists()}
        if args.offline:
            pool = sorted(cached)
            note = (f"*Content search covered the {len(pool)} RFC(s) already cached "
                    f"(offline mode).*")
        elif narrowed and len(metas) <= args.max_fetch:
            pool = [m["n"] for m in metas]
            missing = len(pool) - len(cached & set(pool))
            if missing:
                sync_texts(pool, quiet=args.quiet)
            note = (f"*Content search covered all {len(pool)} RFC(s) matching the "
                    f"filters ({missing} fetched, rest cached).*")
        else:
            pool = sorted(cached & {m["n"] for m in metas})
            if not pool:
                note = (
                    "*No RFC texts are cached, so `--content` had nothing to search. "
                    "Narrow the field with `--year` / `--status` / `--number` (up to "
                    f"{args.max_fetch} docs are fetched on demand), or run `--sync` "
                    "once to cache the full corpus.*"
                )
            else:
                note = (
                    f"*Content search covered the {len(pool)} RFC(s) already cached "
                    f"(of {len(metas)} matching the filters). Narrow with `--year` / "
                    f"`--status` / `--number` to fetch more on demand, or `--sync` "
                    f"the full corpus.*"
                )
        for n in pool:
            t = get_text(n, allow_fetch=False)
            if t:
                texts[n] = t

    # Ranking. Where the query hit matters far more than how many surfaces it hit
    # in: the document *about* QUIC outranks one that merely cites QUIC in its
    # abstract and keywords. So title-centrality dominates (x10), and everything
    # else only breaks ties among equally-titled hits.
    #
    # Ties are then broken by canonicality, because for any given term the answer
    # people want is usually the defining spec: a full standard, a member of an
    # STD/BCP subseries, a long document, one that other RFCs have had to update.
    # And an obsoleted spec sinks below every live one.
    word_rx = None
    if args.query and not args.regex and not args.glob:
        word_rx = re.compile(rf"\b{re.escape(args.query)}\b", re.I)

    def kw_topical(m: dict) -> bool:
        """True when a whole keyword *is* the query — the index's keywords are
        curated, so RFC 5321 carrying kw "SMTP" is as strong a topical signal as
        a title hit. It has to be, since its title never says "SMTP"."""
        kws = [k.lower() for k in m.get("keywords") or []]
        if args.regex or args.glob:
            return any(match(k) and len(k) <= len(args.query) + 8 for k in kws)
        return args.query.lower() in kws

    def canonicality(m: dict) -> int:
        # Deliberately light on maturity level. In the modern IETF the standards
        # track rarely advances, so the defining specs (TLS 1.3, QUIC) sit at
        # "Proposed Standard" forever while niche documents reach "Internet
        # Standard" — weighting status heavily floats the wrong docs to the top.
        status = (m.get("status") or "").upper()
        pts = 2 if "INTERNET STANDARD" in status else (1 if "STANDARD" in status
                                                       or "PRACTICE" in status else 0)
        if m.get("also"):
            pts += 2  # part of an STD / BCP / FYI subseries
        pts += min(8, (m.get("pages") or 0) // 20)  # defining specs run long
        pts += min(4, len(m.get("updated_by") or []))  # others had to amend it
        # The spec that defines a thing tends to be *named after* it and little
        # else ("HTTP Semantics"), while documents that merely build on it carry
        # the term inside a longer title about something else ("Transport Layer
        # Security (TLS) Transport Model for the Simple Network Management
        # Protocol (SNMP)"). Brevity is the cheapest usable proxy for that.
        pts += max(0, 8 - len(m.get("title") or "") // 12)
        return pts

    rows: list[tuple[int, dict, str]] = []
    for m in metas:
        title_hit = 0
        why = []
        if surfaces["title"] and match(m["title"]):
            # A whole-word hit is topical; a hit inside a longer word is noise
            # ("quic" in "Quick Flag Changes").
            title_hit = 4 if (word_rx is None or word_rx.search(m["title"])) else 2
            why.append("title")

        if surfaces["keywords"] and kw_topical(m):
            title_hit = max(title_hit, 4)
            why.append("keyword=")

        score = 10 * title_hit
        # Whole-word, so "tls" doesn't hit the keyword "dtls".
        kw_hit = any(
            (word_rx.search(k) if word_rx else match(k))
            for k in m.get("keywords") or []
        )
        if surfaces["keywords"] and kw_hit and "keyword=" not in why:
            score += 2
            why.append("keyword")
        if surfaces["abstract"] and m.get("abstract") and match(m["abstract"]):
            score += 1
            why.append("abstract")
        if surfaces["content"] and m["n"] in texts and match(texts[m["n"]]):
            score += 1
            why.append("text")

        if score:
            score += canonicality(m)
            if m.get("obsoleted_by"):
                score -= 8  # superseded specs sink below the ones still in force
            rows.append((score, m, "+".join(why)))

    return rows, note


def render_list(rows: list[tuple[int, dict, str]], total: int, args,
                note: str) -> str:
    if args.sort == "number":
        rows.sort(key=lambda r: r[1]["n"])
    elif args.sort == "newest":
        rows.sort(key=lambda r: -r[1]["n"])
    else:  # relevance
        rows.sort(key=lambda r: (-r[0], -r[1]["n"]))

    limit = None if args.all else args.limit
    shown = rows[:limit] if limit else rows

    head = f"## RFC search — {len(rows)} match"
    head += "" if len(rows) == 1 else "es"
    if args.query:
        kind = "regex" if args.regex else ("glob" if args.glob else "substring")
        head += f" for {kind} `{args.query}`"
    out = [head, ""]
    if note:
        out += [note, ""]

    if not rows:
        out.append("*Nothing matched. Try a broader query, drop the filters, or "
                   "search `--content` after `--sync`.*")
        return "\n".join(out)

    if args.numbers:
        out.append(" ".join(str(m["n"]) for _, m, _ in shown))
    else:
        for score, m, why in shown:
            flag = " ~~(obsolete)~~" if m.get("obsoleted_by") else ""
            status = (m.get("status") or "").title()
            badge = f" **[{', '.join(m['also'])}]**" if m.get("also") else ""
            line = f"- **RFC {m['n']}** — {m['title']}{badge}{flag}"
            meta_bits = [b for b in (status, str(m.get("year") or ""),
                                     m.get("stream", "")) if b]
            line += f"  *({', '.join(meta_bits)})*" if meta_bits else ""
            if args.verbose and why:
                line += f"  `match: {why}`"
            out.append(line)
            if args.verbose and m.get("abstract"):
                snippet = re.sub(r"\s+", " ", m["abstract"])[:280]
                out.append(f"  > {snippet}{'…' if len(m['abstract']) > 280 else ''}")

    if limit and len(rows) > limit:
        out += ["", f"*Showing {len(shown)} of {len(rows)} — "
                    f"use `--limit N` or `--all` for the rest.*"]
    out += ["", "*Print one with `/rfc <number>` (verbatim text) or "
                "`/rfc <number> --markdown`; `--info` for metadata only.*"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Cache maintenance
# ---------------------------------------------------------------------------


def do_cache_info(index: dict[str, Any]) -> str:
    d = cache_dir()
    txts = sorted((d / "txt").glob("rfc*.txt")) if (d / "txt").is_dir() else []
    size = sum(p.stat().st_size for p in txts) + (
        index_path().stat().st_size if index_path().exists() else 0
    )
    age = (time.time() - float(index.get("fetched", 0))) / 86400
    return "\n".join([
        "## /rfc cache",
        "",
        f"- **Location:** `{d}`",
        f"- **Index:** {index['count']} RFCs, refreshed {age:.1f} day(s) ago",
        f"- **Texts cached:** {len(txts)} of {index['count']}",
        f"- **Disk:** {size / 1e6:.1f} MB",
        "",
        "`--sync` caches every RFC text (makes `--content` exhaustive and offline); "
        "`--update` refreshes the index; `--clear-cache` empties it.",
    ])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """\
## /rfc — IETF RFC lookup

**Print a document**

```
/rfc 2119                  # the original 72-column text, verbatim, in a fence
/rfc 9110 --markdown       # reflowed into markdown (headings, prose, fenced art)
/rfc 8446 --info           # metadata card only: status, dates, obsoletes graph
/rfc 9110 --toc            # section outline with sizes
/rfc 9110 --section 5.6.1  # just that section (and its subsections)
/rfc 9110 --grep etag      # matching lines with context
/rfc BCP14                 # subseries expand: BCP14 -> RFC 2119 + RFC 8174
```

**Search / list**

```
/rfc --list quic                     # number + title, substring over title+abstract+keywords
/rfc --list --title http             # restrict the surface to titles
/rfc --list --regex '^TLS' --title   # regex; --glob for glob patterns
/rfc --list oauth --status proposed --year 2012-2020 -v
/rfc --list --content hpack --number 7000-7999   # full-text search (fetches on demand)
/rfc --list --status "internet standard" --current --all
```

**Cache**: `--sync` (cache every RFC text; makes `--content` exhaustive + offline),
`--update` (refresh index), `--cache-info`, `--clear-cache`.

Big RFCs are large — RFC 9110 is ~500 KB. Prefer `--toc` then `--section`, or
`--grep`, over dumping a whole document; output is capped by `--max-chars`.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rfc", add_help=False)
    p.add_argument("ids", nargs="*", default=[])

    mode = p.add_argument_group("mode")
    mode.add_argument("--list", "--find", dest="list", action="store_true")
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--update", action="store_true")
    mode.add_argument("--cache-info", action="store_true")
    mode.add_argument("--clear-cache", action="store_true")
    mode.add_argument("-h", "--help", action="store_true")

    doc = p.add_argument_group("document output")
    doc.add_argument("-m", "--markdown", action="store_true")
    doc.add_argument("-t", "--text", action="store_true")
    doc.add_argument("--raw", action="store_true")
    doc.add_argument("-i", "--info", action="store_true")
    doc.add_argument("--header", action="store_true")
    doc.add_argument("--toc", action="store_true")
    doc.add_argument("-s", "--section", action="append", default=[])
    doc.add_argument("-g", "--grep")
    doc.add_argument("-C", "--context", type=int, default=2)
    doc.add_argument("--depaginate", action="store_true")
    doc.add_argument("--keep-toc", action="store_true")
    doc.add_argument("--no-boilerplate", action="store_true")
    doc.add_argument("--no-links", action="store_true")

    srch = p.add_argument_group("search")
    srch.add_argument("--title", action="store_true")
    srch.add_argument("--abstract", action="store_true")
    srch.add_argument("--keywords", action="store_true")
    srch.add_argument("--content", action="store_true")
    srch.add_argument("--glob", action="store_true")
    srch.add_argument("--regex", action="store_true")
    srch.add_argument("--status")
    srch.add_argument("--stream")
    srch.add_argument("--year")
    srch.add_argument("--author")
    srch.add_argument("--number")
    srch.add_argument("--current", action="store_true")
    srch.add_argument("--obsolete", action="store_true")
    srch.add_argument("--std", action="store_true",
                      help="only RFCs in an STD/BCP/FYI subseries")
    srch.add_argument("--sort", choices=("relevance", "number", "newest"))
    srch.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT)
    srch.add_argument("-a", "--all", action="store_true")
    srch.add_argument("-v", "--verbose", action="store_true")
    srch.add_argument("--numbers", action="store_true")
    srch.add_argument("--max-fetch", type=int, default=DEFAULT_MAX_FETCH)

    misc = p.add_argument_group("misc")
    misc.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    misc.add_argument("--offline", action="store_true")
    misc.add_argument("--quiet", action="store_true")
    return p


def budget(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    if nl > max_chars * 0.6:
        cut = cut[:nl]
    fences = len(re.findall(r"^`{3,}", cut, re.M))
    if fences % 2:  # never leave a fence hanging open
        cut += "\n```"
    return (
        cut
        + f"\n\n---\n\n*[output truncated at {max_chars:,} characters — "
        "narrow it with `--section` / `--grep` / `--toc`, or raise `--max-chars`]*"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print(f"*(ignoring unknown option(s): {' '.join(unknown)})*\n")

    if args.help or (not argv):
        print(USAGE)
        return 0

    # In --list mode the positionals are the query; in doc mode they are IDs.
    args.query = " ".join(args.ids).strip() if args.list else ""
    if args.sort is None:
        args.sort = "relevance" if args.query else "number"

    if args.clear_cache:
        d = cache_dir()
        if d.exists():
            shutil.rmtree(d)
        print(f"## /rfc cache cleared\n\nRemoved `{d}`.")
        return 0

    index = load_index(refresh=args.update, quiet=args.quiet)

    if args.update:
        print(f"## /rfc index updated\n\n{index['count']} RFCs indexed from "
              f"`{INDEX_URL}`.")
        return 0

    if args.cache_info:
        print(do_cache_info(index))
        return 0

    if args.sync:
        nums = [m["n"] for m in apply_filters(index, args)]
        if args.limit and not args.all and args.limit != DEFAULT_LIMIT:
            nums = nums[: args.limit]
        done, failed = sync_texts(nums, quiet=args.quiet)
        print(f"## /rfc sync\n\nCached {done} new text(s); {failed} failed; "
              f"{len(nums)} in scope.\n\n{do_cache_info(index)}")
        return 0

    if args.list:
        rows, note = search(index, args)
        print(budget(render_list(rows, len(index["rfcs"]), args, note),
                     args.max_chars))
        return 0

    if not args.ids:
        print(USAGE)
        return 0

    nums = resolve_ids(args.ids, index)
    chunks = []
    for n in nums:
        meta = index["rfcs"][str(n)]
        chunks.append(render_document(n, meta, index, args))
    print(budget("\n\n---\n\n".join(chunks), args.max_chars))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

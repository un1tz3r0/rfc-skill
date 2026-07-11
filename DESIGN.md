# DESIGN — `/rfc` skill

## Purpose & intent

A Claude Code skill, `/rfc`, that pulls **real IETF RFCs** from the RFC Editor and
puts them into the conversation — either as the original hand-wrapped 72-column
plain text, verbatim, or reflowed into markdown. It turns "what does the spec
actually say?" into one command, grounded in the normative document rather than
recalled from training.

Original request (2026-07-10), faithfully summarized:

- Look up RFCs **by number or by keyword**.
- **Convert to markdown**, *or* emit the **plaintext in fenced blocks as-is** —
  the hand-formatted 80-column monospace is the point, and worth preserving.
- **List RFCs by number + title**, optionally filtered by a search query.
- The query may be a plain substring, a `--glob`, or a `--regex`.
- Searchable surfaces: `--title` or `--content`, **both by default**.
- Same shape as the `pydoc-skill` in `..` (nested layout, `!`-injection, the
  install/package/clean/update tooling).

## Constraints

- **System `python3`, standard library only.** No third-party deps, no venv —
  matching the pydoc skill, and keeping the engine trivially portable.
- Output is **markdown on stdout only**; diagnostics (fetch progress, warnings)
  go to stderr so the injected context stays clean.
- Output must be **bounded** — RFC 9110 alone is ~500 KB of text, ten times a
  comfortable context budget — so there is a character budget with a clean
  truncation notice, and section-level retrieval to avoid needing it.
- **Polite to the RFC Editor.** Everything is cached; the full corpus is only
  downloaded on an explicit `--sync`.

## Architecture

Nested layout (mirrors `pydoc-skill` / `use-yt-dlp` in this collection):

```
rfc-skill/              dev repo — dev-meta + build tooling (NOT shipped)
├── scripts/            install.py · package.py · clean.py · update.py
├── {install,package,clean,update}.sh   thin wrappers -> scripts/*.py
├── .skillignore · .cleanup · .gitignore
├── DESIGN.md · TODO.md · CHANGELOG.md · README.md
└── rfc/                THE SHIPPABLE SKILL  →  ~/.claude/skills/rfc/  →  /rfc
    ├── SKILL.md        name: rfc; dynamically injects the engine
    └── scripts/rfc_tool.py     the lookup/search/render engine (stdlib only)
```

Why nested: the inner dir name (`rfc`) becomes the command, and dev-meta never
leaks into the shipped bundle because only `rfc/` is packaged.

**Skill entrypoint (`rfc/SKILL.md`).** Dynamic context injection:
`` !`python3 "${CLAUDE_SKILL_DIR}/scripts/rfc_tool.py" $ARGUMENTS` ``. Claude Code
runs the engine at invoke time and replaces the line with its output, so the spec
text is in context the moment the skill fires. `allowed-tools: Bash(python3:*)`
lets the assistant re-run the engine for follow-ups (drilling into a section a
listing or outline surfaced).

## Data sources & caching

| What | Where | Size | When |
|---|---|---|---|
| Metadata for all ~9,800 RFCs | `rfc-index.xml` | 13.6 MB | once, auto-refreshed weekly |
| Full text of one RFC | `/rfc/rfcNNNN.txt` | 4 KB – 500 KB | on demand |

Cache lives in `~/.cache/rfc-skill/` (override with `RFC_SKILL_CACHE`;
`XDG_CACHE_HOME` respected): the parsed index as `index.json`, texts under
`txt/`. Repeat lookups are offline and instant. A stale cache is preferred over
a hard failure when the network is down.

The XML index is the quiet win: it carries **title, authors, date, status,
stream, page count, keywords, abstract, and the obsoletes/updates graph** for
every RFC. That makes almost every search — and every "is this still current?"
question — answerable from one 13 MB download, with zero document fetches.

## CLI grammar (the contract)

Mode is chosen by scanning for `--list`/`--find`, else cache verbs, else IDs.

- `ID [ID...] [--text|--markdown|--raw|--info] [--toc] [--section S] [--grep P]`
- `--list [QUERY] [SURFACES] [FILTERS] [--glob|--regex] [--limit N|--all]`
- `--sync | --update | --cache-info | --clear-cache`

SURFACES: `--title --abstract --keywords --content` (default: title + abstract +
keywords). FILTERS: `--status --stream --year --author --number --current
--obsolete --std`. Common: `--max-chars N`, `--offline`, `--quiet`.

## Key decisions & rationale

- **Verbatim text is the default; markdown is opt-in.** The 72-column text is the
  normative artifact and its diagrams are load-bearing. Markdown conversion is
  lossy by nature, so it never happens unless asked for.
- **IDs and subseries.** `2119`, `rfc2119`, `RFC-2119` all resolve; `BCP14` and
  `STD97` expand to their member RFCs (BCP 14 correctly yields *both* RFC 2119
  and RFC 8174 — the pair everyone forgets).
- **Depagination detects headers by repetition, not by layout.** Page furniture
  is not standardized across five decades: 1981-era RFCs run a two-line
  `September 1981` / `Internet Protocol` head and an indented `[Page 11]` footer;
  1999-era ones use a flush-left `RFC 2616 ... June 1999`; post-2019 RFCs are
  unpaginated with no form feeds at all. Rather than encode each era, the
  converter tallies the first few lines of every page and drops whatever *recurs*
  across most of them. One rule, five decades, verified clean on RFCs 791 / 793 /
  1035 / 2616 / 2119.
- **Markdown conversion is conservative about what it reflows.** Prose is
  unwrapped into real paragraphs (the context-efficiency win), but anything that
  looks like artwork — box-drawing characters, bit-field tables, ABNF, aligned
  columns, deeply-indented blocks — is left byte-for-byte inside a fence. Ruining
  a packet diagram to save a few tokens is a bad trade. Lone centered lines under
  a diagram are captions, not code, and become italics.
- **Big documents are addressed, not dumped.** `--toc` derives an outline from
  the body headings (not the printed table of contents) and annotates each entry
  with its subtree size, so the model can pick; `--section 5.6.1` prints that
  subtree; `--grep` prints matching lines with context. The whole-document dump
  is capped by `--max-chars` with a notice pointing at those flags.
- **Relevance ranking weights *where* a hit landed, not how many surfaces it hit.**
  Additive surface-counting ranked "Compatible Version Negotiation for QUIC" above
  RFC 9000 (*the* QUIC spec) because it matched in three surfaces. Title-centrality
  now dominates (×10), and ties break on canonicality: subseries membership,
  length, maturity, and how many RFCs have had to amend it.
  - Maturity is weighted *lightly* on purpose. In the modern IETF the standards
    track rarely advances, so the defining specs (TLS 1.3, QUIC) sit at "Proposed
    Standard" forever while niche documents reach "Internet Standard" — weighting
    status heavily floats an SNMP transport model above RFC 8446.
  - A whole-keyword hit counts as a title-strength hit, because the RFC Editor's
    keywords are curated and the canonical spec is often named after the
    *expansion*, not the acronym: RFC 5321's title never contains "SMTP", and RFC
    1035's never contains "DNS". Without this, neither is findable by its own name.
  - Whole-word matching, so `tls` does not hit the keyword `dtls`.
- **Obsolescence is surfaced everywhere.** Obsoleted RFCs are struck through in
  listings, sink in the ranking, and carry a warning in `--info` naming their
  successor. Quoting RFC 2616 at someone in 2026 is the failure mode this skill
  exists to prevent.
- **Two-phase content search.** Full text is 9,800 documents; the index is one
  file. So `--content` searches what is cached, plus — when metadata filters
  narrow the field to `--max-fetch` (default 120) documents — those, fetched
  concurrently on demand. An unnarrowed `--content` degrades to the cached subset
  with an explicit note rather than firing 9,800 requests. `--sync` is the opt-in
  escape hatch for an exhaustive offline corpus.

## Known limitations

- Markdown conversion is **heuristic**, and pre-1990 RFCs (hand-typeset, wildly
  irregular) convert worst. The text is the normative form; `--text` is lossless.
- Ranking cannot reliably identify the base spec for broad umbrella terms
  ("ipv6", "dns") where hundreds of RFCs legitimately match. Mitigated by showing
  `[STD86]`-style badges in listings and offering `--std --current`.
- `--content` is only exhaustive after `--sync` (~9,800 fetches).
- Errata are linked but not inlined; Internet-Drafts are not covered.

## Milestone 2 (deferred)

- Convert from the **XML source** (`rfcNNNN.xml`, RFC 8650+) where it exists —
  lossless markdown, no heuristics — falling back to the text converter otherwise.
- Bulk `--sync` from a tarball rather than ~9,800 individual GETs.
- Internet-Draft lookup via the IETF Datatracker API; inline errata.

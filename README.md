# rfc-skill — `/rfc`

A Claude Code skill that looks up **real IETF RFCs** from the RFC Editor and puts
them into the conversation — either as the original hand-wrapped 72-column plain
text, verbatim in a fenced block, or reflowed into markdown. Search all ~9,800
RFCs by number, title, abstract, keyword, or full text; check what obsoletes
what; pull a single section out of a 500 KB spec.

Standard library only. No third-party dependencies, no virtualenv.

## Install

```bash
./install.sh                 # copy rfc/ -> ~/.claude/skills/rfc/  (then /rfc works)
./install.sh --symlink       # symlink instead, so edits here stay live
./install.sh --dry-run       # preview
./install.sh --uninstall     # remove
```

`~/.claude/skills/` is already watched, so `/rfc` should register in the current
session (restart Claude Code if it doesn't appear).

## Use

```
/rfc 2119                    # the original 72-column text, verbatim, in a fence
/rfc 9110 --markdown         # reflowed to markdown (diagrams + ABNF stay fenced)
/rfc 8446 --info             # metadata card: status, dates, obsoletes graph
/rfc BCP14                   # subseries expand -> RFC 2119 + RFC 8174
```

Big RFCs are big — RFC 9110 is ~500 KB. Address them instead of dumping them:

```
/rfc 9110 --toc              # section outline, each with its size
/rfc 9110 --section 5.6.1    # just that section (and its subsections)
/rfc 9110 --grep etag -C 3   # matching lines with context
```

Search:

```
/rfc --list quic                        # title + abstract + keywords (default)
/rfc --list --title http                # restrict the surface
/rfc --list --regex '^TLS' --title      # regex; --glob for glob patterns
/rfc --list oauth --status proposed --year 2012-2020 -v
/rfc --list ipv6 --std --current        # only RFCs that *are* an STD/BCP
/rfc --list --content hpack --number 7000-7999   # full text, fetched on demand
```

Surfaces: `--title --abstract --keywords --content`. Filters: `--status --stream
--year --author --number --current --obsolete --std`. Also `--limit N` / `--all`,
`-v`, `--max-chars N`, `--offline`. Run `/rfc` with no arguments for usage.

**Obsolescence is surfaced everywhere** — superseded RFCs are struck through in
listings and carry a warning in `--info` naming their successor. (RFC 2616 is not
the current HTTP spec; RFC 9110 is.)

## Cache

Everything is cached under `~/.cache/rfc-skill/` (override with `RFC_SKILL_CACHE`),
so repeat lookups are offline and instant:

- the metadata index for all ~9,800 RFCs — one 13.6 MB download, auto-refreshed weekly
- the full text of each RFC you actually open

```
/rfc --cache-info      # what's cached
/rfc --update          # refresh the metadata index
/rfc --sync            # download every RFC text (makes --content exhaustive + offline)
/rfc --clear-cache     # empty it
```

## Layout

```
rfc-skill/              this repo — dev-meta + build tooling (not shipped)
├── rfc/                the shippable skill  ->  ~/.claude/skills/rfc/  ->  /rfc
│   ├── SKILL.md
│   └── scripts/rfc_tool.py       the lookup/search/render engine
├── scripts/            install.py · package.py · clean.py · update.py
├── install.sh · package.sh · clean.sh · update.sh
├── .skillignore · .cleanup · .gitignore
└── DESIGN.md · TODO.md · CHANGELOG.md · README.md
```

Only `rfc/` is installed or packaged; everything else is development tooling.

## Maintenance

```bash
./package.sh     # build rfc.skill (zip) for upload; runs update first, obeys .skillignore
./clean.sh       # remove caches, .venv, *.skill/*.zip (source is protected)
./update.sh      # git pull --ff-only if this is a checkout with a remote (else no-op)
```

See **DESIGN.md** for architecture and decisions — including why depagination
detects page headers by repetition rather than by layout (five decades of RFCs
disagree about where the header goes), and why the search ranking deliberately
under-weights maturity level.

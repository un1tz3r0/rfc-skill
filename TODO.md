# TODO — `/rfc` skill

## Milestone 1 — core skill + tooling  ✅ (2026-07-10)

- [x] Engine `rfc/scripts/rfc_tool.py` (stdlib only, system python3)
  - [x] Index: fetch + parse `rfc-index.xml` -> compact `index.json` cache (9,794 RFCs)
  - [x] ID resolution: `2119` / `rfc2119` / `RFC-2119` / `BCP14` / `STD97` / `FYI36`
  - [x] Doc mode: verbatim 72-col text in a fence (default), `--markdown`, `--raw`
  - [x] `--info` metadata card (status, stream, authors, abstract, obsoletes graph)
  - [x] `--toc` derived from body headings; `--section N` extraction; `--grep PAT -C N`
  - [x] Text -> markdown converter (depaginate, headings, unwrap prose,
        fence artwork/ABNF, bullets, captions, linkify `[RFCnnnn]`)
  - [x] `--list` / `--find`: number + title, substring / `--glob` / `--regex`
  - [x] Search surfaces: `--title` / `--abstract` / `--keywords` / `--content`
  - [x] Filters: `--status --stream --year --author --number --current --obsolete --std`
  - [x] Relevance ranking (title-centrality + canonicality), `--limit`, `--sort`
  - [x] Two-phase `--content` search (metadata narrow -> fetch -> grep) + `--sync`
  - [x] Output budget (`--max-chars`) with clean truncation notice
  - [x] Offline tolerance: cache hits work with no network (`--offline`)
- [x] `rfc/SKILL.md` with dynamic `!`-injection; user + model invocable
- [x] Lifecycle tooling: `install` / `package` / `clean` / `update` (.py + .sh)
- [x] Config: `.skillignore`, `.cleanup`, `.gitignore`
- [x] Dev-meta: DESIGN / TODO / CHANGELOG / README
- [x] Verification: 22 RFCs x {text, markdown, toc, info} with no crashes;
      page-furniture leak sweep clean across 20 RFCs spanning 1969-2022
- [x] `./install.sh` -> `~/.claude/skills/rfc/`; `/rfc` is live

## Activation (optional, do once)

- [x] `git init` + push to GitHub (un1tz3r0/rfc-skill) — `update.sh` now has a remote.
- [ ] `python3 rfc/scripts/rfc_tool.py --sync` if exhaustive offline `--content`
      search is wanted (~9,800 fetches; the index alone covers most searches).

## Milestone 2 — deferred

- [ ] Convert from the XML source (`rfcNNNN.xml`, RFC 8650+) where it exists —
      lossless markdown, no heuristics — falling back to the text converter
- [ ] Bulk `--sync` from a tarball rather than ~9,800 individual GETs
- [ ] Internet-Draft lookup via the IETF Datatracker API
- [ ] Inline errata (`--errata`); currently only linked

## Polish / maybe-later

- [ ] Ranking still can't pick the base spec for broad umbrella terms
      ("ipv6" -> RPL outranks RFC 8200). Mitigated by `--std` + STD badges;
      a citation-graph signal would fix it properly.
- [ ] Pre-1990 RFCs (hand-typeset, irregular) convert worst to markdown
- [ ] `--json` output mode for programmatic consumers
- [ ] Windows `.ps1` wrappers to match the collection's cross-platform skills

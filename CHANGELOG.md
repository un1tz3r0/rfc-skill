# Changelog — `/rfc` skill

All notable changes to this skill are recorded here. Grouped by
Added / Changed / Fixed / Removed, newest first.

## 2026-07-10 — initial build (Milestone 1)

### Added
- Lookup/search/render engine `rfc/scripts/rfc_tool.py` (system python3, stdlib only):
  - Print any RFC as the original 72-column text (verbatim, fenced — the default),
    as `--markdown`, as `--raw`, or as an `--info` metadata card.
  - ID resolution for `2119` / `rfc2119` / `RFC-2119`, plus subseries expansion
    (`BCP14` -> RFC 2119 + RFC 8174; `STD97` -> the HTTP core).
  - Large-document handling: `--toc` (outline derived from body headings, each
    annotated with its subtree size), `--section N` (prints that subtree),
    `--grep PAT -C N` (matching lines with context).
  - Text -> markdown converter: depagination, real headings, prose unwrapped to
    paragraphs, diagrams/ABNF/tables preserved byte-for-byte in fences, bullets,
    `[RFCnnnn]` cross-references linkified.
  - `--list` / `--find` search over title + abstract + keywords (default) or
    `--content` full text, with plain-substring, `--glob`, and `--regex` matching.
  - Filters: `--status`, `--stream`, `--year`, `--author`, `--number`, `--current`,
    `--obsolete`, `--std`; `--limit` / `--all` / `-v` / `--sort`.
  - Metadata index (~9,800 RFCs) parsed from `rfc-index.xml` into a local
    `index.json`; full texts cached per document under `~/.cache/rfc-skill/`.
  - Cache verbs: `--sync`, `--update`, `--cache-info`, `--clear-cache`; `--offline`.
  - Output budget (`--max-chars`) with a truncation notice pointing at `--toc` /
    `--section` / `--grep`.
- `rfc/SKILL.md`: dynamic `!`-injection of the engine with `$ARGUMENTS`;
  user- and model-invocable; `allowed-tools: Bash(python3:*)`.
- Lifecycle tooling (thin `*.sh` wrappers over `scripts/*.py`), adapted from
  `pydoc-skill`: `install`, `package`, `clean`, `update`.
- Config: `.skillignore`, `.cleanup`, `.gitignore`.
- Dev-meta: DESIGN.md, TODO.md, CHANGELOG.md, README.md.

### Fixed
- **Depagination across five decades of layouts.** Page furniture is not
  standardized: 1981-era RFCs run a two-line `September 1981` / `Internet
  Protocol` header and an *indented* `[Page 11]` footer; 1999-era ones use a
  flush-left `RFC 2616 ... June 1999`; post-2019 RFCs have no form feeds at all.
  Header/footer regexes tuned to one era leaked page furniture into the output of
  the others. Headers are now identified by the property that actually defines
  them — they *recur* at the top of most pages — which is layout-agnostic and
  verified clean on RFCs 791 / 793 / 1035 / 2616 / 2119.
- **Search ranking surfaced derivative specs over defining ones.** Additive
  surface-counting put "Compatible Version Negotiation for QUIC" above RFC 9000,
  an SNMP transport model above RFC 8446 (TLS 1.3), and a DSN extension above
  RFC 5321 (SMTP). Fixed by making title-centrality dominate, weighting maturity
  level only lightly (the modern IETF rarely advances the standards track, so the
  defining specs sit at "Proposed Standard" forever), treating a whole-keyword hit
  as title-strength (RFC 5321's title never says "SMTP"; RFC 1035's never says
  "DNS"), and matching whole words so `tls` no longer hits the keyword `dtls`.
- Figure captions under ASCII diagrams were being fenced as code; they are now
  italicized.
- `--toc` reported each section's own size, not the size of the subtree that
  `--section` would actually print.
- Fenced output widens its backtick delimiter when the RFC text itself contains
  backtick runs, and a truncated fence is always closed.

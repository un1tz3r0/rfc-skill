---
name: rfc
description: >-
  Look up, search, and print IETF RFCs from the RFC Editor — the real normative
  text, not recalled knowledge. Resolves an RFC by number ("2119", "rfc9110") or
  subseries ("BCP14", "STD97") and prints it either as the original hand-wrapped
  72-column plain text, verbatim in a fenced block, or reflowed into markdown.
  Searches all ~9,800 RFCs by number, title, abstract, keyword, or full text,
  with substring, glob, or regex matching, and filters by status, stream, year,
  author, and whether a spec is current or obsoleted. Large RFCs can be reduced
  to a section outline, a single section, or grep-with-context. Use whenever a
  protocol detail, header, status code, ABNF grammar, or requirement level needs
  to be quoted or checked against the actual specification. Invoke as
  `/rfc <number>`, `/rfc --list <query>`, or `/rfc <number> --markdown`.
when_to_use: >-
  Triggers: "what does RFC N say", "look up RFC N", "which RFC defines X", "is
  RFC N still current / what obsoletes it", quoting MUST/SHOULD requirements,
  checking a protocol's wire format, headers, status codes, or ABNF grammar
  before implementing against it. Prefer this over recalling a spec from memory —
  RFCs are long, frequently obsoleted, and precise wording matters.
compatibility: Requires the system python3 (3.10+) and, for the first lookup, network access to www.rfc-editor.org. Standard library only — no third-party dependencies. Everything is cached under ~/.cache/rfc-skill/, so repeat lookups work offline.
allowed-tools: Bash(python3:*)
---

# /rfc — IETF RFC lookup

The block below is generated live from the RFC Editor's own documents and
metadata index. It is the actual specification text — read it as normative and
quote from it directly rather than paraphrasing from memory.

!`python3 "${CLAUDE_SKILL_DIR}/scripts/rfc_tool.py" $ARGUMENTS`

---

**Following up.** Re-run the engine directly for anything else (it is read-only
and caches to `~/.cache/rfc-skill/`):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/rfc_tool.py" <ids-or-flags>
```

- `<number>` — the original 72-column text, verbatim in a fence. `BCP14` / `STD97`
  expand to their member RFCs. `--markdown` reflows it (headings, prose unwrapped,
  diagrams and ABNF left fenced); `--info` prints metadata only.
- `--toc` then `--section <n>` — **do this for anything large.** RFC 9110 is
  ~500 KB; dumping it whole wastes the context window and gets truncated. The
  outline lists every section with its size, and `--section 5.6.1` prints just
  that subtree. `--grep PAT -C 3` finds matching lines with context.
- `--list QUERY` — search by title + abstract + keywords (default), or restrict
  with `--title` / `--abstract` / `--content` (full text). `--glob` / `--regex`
  switch QUERY from plain substring to a pattern.
- Filters: `--status`, `--stream`, `--year 2015-2020`, `--author`, `--number`,
  `--current` (not obsoleted), `--obsolete`, `--std` (STD/BCP/FYI members only).

**Check whether a spec is still in force.** Obsoleted RFCs are marked
`~~(obsolete)~~` in listings and carry a warning in `--info`; the metadata card
links what superseded them. RFC 2616 is *not* the current HTTP spec — RFC 9110
is. When the user names an old RFC, say so.

**Ranking is a heuristic.** Results are ordered by where the query hit (title
beats abstract) and by signals of canonicality (subseries membership, length,
maturity). For a broad umbrella term ("ipv6", "dns") the base spec may not lead —
add `--std --current` to surface the RFC that *is* the standard, and look for the
`[STD86]`-style badge in listings.

If nothing matches, broaden the query, drop filters, or try `--regex`. Full-text
`--content` search only reaches RFCs whose text is cached or reachable within
`--max-fetch`; narrow with `--year` / `--number` / `--status`, or run `--sync`
once to cache the whole corpus for exhaustive offline searching.

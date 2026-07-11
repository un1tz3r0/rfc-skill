#!/usr/bin/env python3
"""package.py — build a clean, up-to-date `rfc.skill` bundle for upload.

Zips the shippable ``rfc/`` subdirectory (the skill itself) into
``rfc.skill`` — a zip archive with a ``.skill`` suffix, matching the other
bundles in this skills collection. Build tooling and dev-meta at the repo root
are never included, because only ``rfc/`` is archived.

Steps:
  1. Run ``update.py`` first to pull any recent changes from git (skipped with
     ``--no-update`` or when this is not a git checkout).
  2. Walk ``rfc/``, dropping built-in noise plus anything matched by
     ``.skillignore`` (gitignore-style globs; ``#`` comments; ``!`` re-includes).
  3. Write ``rfc.skill``.

Run via ``./package.sh`` or directly:

    python3 scripts/package.py [-o OUT.skill] [--no-update] [-n|--dry-run]
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "rfc"
SKILL_NAME = SKILL_DIR.name

# Always dropped, regardless of .skillignore.
SKIP_DIR_NAMES = {"__pycache__", ".git", ".claude"}
SKIP_FILE_NAMES = {".DS_Store"}
SKIP_FILE_SUFFIXES = {".pyc"}


# --- .skillignore (gitignore-lite) -----------------------------------------

def load_ignore_patterns(*paths: Path) -> list[tuple[bool, str]]:
    """Parse .skillignore file(s) into (negated, pattern) rules, in order."""
    rules: list[tuple[bool, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if " #" in s:  # strip inline comment
                s = s.split(" #", 1)[0].strip()
            if not s:
                continue
            neg = s.startswith("!")
            if neg:
                s = s[1:].strip()
            rules.append((neg, s))
    return rules


def _matches(rel: Path, pat: str) -> bool:
    rel_posix = rel.as_posix()
    if fnmatch.fnmatch(rel_posix, pat):
        return True
    if "/" not in pat:  # bare name matches any path component
        return any(fnmatch.fnmatch(part, pat) for part in rel.parts)
    # path-shaped pattern: also treat as a directory prefix
    return fnmatch.fnmatch(rel_posix, pat.rstrip("/") + "/*")


def is_ignored(rel: Path, rules: list[tuple[bool, str]]) -> bool:
    ignored = False
    for neg, pat in rules:
        if _matches(rel, pat):
            ignored = not neg
    return ignored


# --- collection -------------------------------------------------------------

def collect_files(rules: list[tuple[bool, str]]) -> list[Path]:
    files: list[Path] = []
    for path in SKILL_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(SKILL_DIR)
        if set(rel.parts) & SKIP_DIR_NAMES:
            continue
        if rel.name in SKIP_FILE_NAMES or rel.suffix in SKIP_FILE_SUFFIXES:
            continue
        if is_ignored(rel, rules):
            continue
        files.append(path)
    return sorted(files)


def run_update() -> None:
    update = REPO_ROOT / "scripts" / "update.py"
    if not update.is_file():
        return
    print("→ updating (git pull) …", flush=True)
    # Tolerant: update.py returns 0 even when there is nothing/no remote to pull.
    subprocess.run([sys.executable, str(update)], check=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="package.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--output", default=None,
                   help=f"output path (default: ./{SKILL_NAME}.skill)")
    p.add_argument("--name", default=SKILL_NAME,
                   help=f"archive root directory name (default: {SKILL_NAME})")
    p.add_argument("--no-update", action="store_true",
                   help="skip the git pull step")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="list files that would be archived, then exit")
    args = p.parse_args(argv)

    if not SKILL_DIR.is_dir():
        print(f"error: shippable skill dir not found: {SKILL_DIR}", file=sys.stderr)
        return 1

    if not args.no_update and not args.dry_run:
        run_update()

    rules = load_ignore_patterns(REPO_ROOT / ".skillignore", SKILL_DIR / ".skillignore")
    files = collect_files(rules)
    if not files:
        print("error: no files matched", file=sys.stderr)
        return 1

    out = Path(args.output).resolve() if args.output else REPO_ROOT / f"{args.name}.skill"

    if args.dry_run:
        for f in files:
            print(f.relative_to(SKILL_DIR))
        print(f"\n{len(files)} files would be archived into {out}", file=sys.stderr)
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arcname = (Path(args.name) / f.relative_to(SKILL_DIR)).as_posix()
            zf.write(f, arcname)
            total += f.stat().st_size

    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({len(files)} files, {kb:.1f} KB compressed, "
          f"{total / 1024:.1f} KB uncompressed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

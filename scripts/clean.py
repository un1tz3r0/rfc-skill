#!/usr/bin/env python3
"""clean.py — delete regenerable artifacts from the skill repo.

Removes, from the repo root down:
  * any directory tagged with a ``CACHEDIR.TAG`` file (the cache-dir convention),
  * virtualenvs (``.venv`` / ``venv``) and ``__pycache__`` directories,
  * ``*.pyc`` / ``*.pyo`` files and ``.DS_Store``,
  * anything matched by ``.cleanup`` or ``.skillignore`` (gitignore-style globs).

A hardcoded protect-list keeps it from ever touching version control or source:
``.git``, the ``scripts/`` and ``rfc/`` source dirs (as whole dirs), and any
``*.py`` / ``*.sh`` / ``*.md`` file plus the dotfile configs. So even an overly
broad ignore pattern cannot delete the skill itself.

Run via ``./clean.sh`` or directly:

    python3 scripts/clean.py [-n|--dry-run]
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CACHEDIR_TAG = "CACHEDIR.TAG"
CACHE_DIR_NAMES = {"__pycache__", ".venv", "venv", ".pytest_cache",
                   ".mypy_cache", ".ruff_cache"}
JUNK_FILE_SUFFIXES = {".pyc", ".pyo"}
JUNK_FILE_NAMES = {".DS_Store"}

# Never delete these whole directories (still descend to clean caches inside).
PROTECT_DIR_NAMES = {".git", "scripts", "rfc"}
# Never delete files with these suffixes / names (source & config safety net).
PROTECT_FILE_SUFFIXES = {".py", ".sh", ".ps1", ".md"}
PROTECT_FILE_NAMES = {".gitignore", ".skillignore", ".cleanup", "SKILL.md"}


def load_ignore_patterns(*paths: Path) -> list[tuple[bool, str]]:
    rules: list[tuple[bool, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if " #" in s:
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
    if "/" not in pat:
        return any(fnmatch.fnmatch(part, pat) for part in rel.parts)
    return fnmatch.fnmatch(rel_posix, pat.rstrip("/") + "/*")


def ignore_match(rel: Path, rules: list[tuple[bool, str]]) -> bool:
    hit = False
    for neg, pat in rules:
        if _matches(rel, pat):
            hit = not neg
    return hit


def is_protected_file(rel: Path) -> bool:
    if ".git" in rel.parts:
        return True
    if rel.name in PROTECT_FILE_NAMES:
        return True
    return rel.suffix in PROTECT_FILE_SUFFIXES


def is_cache_dir(d: Path) -> bool:
    return d.name in CACHE_DIR_NAMES or (d / CACHEDIR_TAG).is_file()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="clean.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="list what would be removed, then exit")
    args = p.parse_args(argv)

    rules = load_ignore_patterns(REPO_ROOT / ".cleanup", REPO_ROOT / ".skillignore")
    to_remove: list[Path] = []
    seen_dirs: set[Path] = set()

    # Walk top-down so we can prune whole cache dirs without descending.
    stack = [REPO_ROOT]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            rel = entry.relative_to(REPO_ROOT)
            if entry.is_dir() and not entry.is_symlink():
                if entry.name in PROTECT_DIR_NAMES or ".git" in rel.parts:
                    if entry.name != ".git":
                        stack.append(entry)  # descend into source dirs
                    continue
                if is_cache_dir(entry) or ignore_match(rel, rules):
                    to_remove.append(entry)
                    seen_dirs.add(entry)
                else:
                    stack.append(entry)
            else:
                if is_protected_file(rel):
                    continue
                if (entry.suffix in JUNK_FILE_SUFFIXES
                        or entry.name in JUNK_FILE_NAMES
                        or ignore_match(rel, rules)):
                    to_remove.append(entry)

    # Drop files that live under a directory already slated for removal.
    pruned = [p for p in to_remove
              if not any(parent in seen_dirs for parent in p.parents)]

    if not pruned:
        print("nothing to clean.")
        return 0

    for path in pruned:
        rel = path.relative_to(REPO_ROOT)
        kind = "dir " if path.is_dir() else "file"
        if args.dry_run:
            print(f"would remove {kind} {rel}")
            continue
        print(f"removing {kind} {rel}")
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as e:
            print(f"  warning: could not remove {rel}: {e}", file=sys.stderr)

    print(f"\n{len(pruned)} item(s) "
          f"{'would be ' if args.dry_run else ''}removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

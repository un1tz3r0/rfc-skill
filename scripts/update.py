#!/usr/bin/env python3
"""update.py — pull recent changes for this skill from git, tolerantly.

If the repo root is a git checkout with a configured upstream/remote, run a
fast-forward-only ``git pull``. If it is not a git repo, has no remote, or git
is unavailable, print a short note and exit 0 — so callers like ``package.py``
can invoke it unconditionally without failing on a fresh, not-yet-published
skill.

Run via ``./update.sh`` or directly:

    python3 scripts/update.py [--remote NAME] [--branch NAME]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, text=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="update.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--remote", default=None, help="remote to pull from (default: tracked upstream)")
    p.add_argument("--branch", default=None, help="branch to pull (default: current)")
    args = p.parse_args(argv)

    if shutil.which("git") is None:
        print("update: git not found on PATH — skipping.")
        return 0

    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print(f"update: {REPO_ROOT} is not a git checkout — skipping.")
        return 0

    remotes = _git("remote").stdout.split()
    if not remotes:
        print("update: no git remote configured — skipping (nothing to pull yet).")
        return 0

    cmd = ["pull", "--ff-only"]
    if args.remote and args.branch:
        cmd += [args.remote, args.branch]
    elif args.remote:
        cmd += [args.remote]

    print(f"update: git {' '.join(cmd)} …")
    result = _git(*cmd)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        # Non-fatal: report and carry on (e.g. no upstream set, or diverged).
        sys.stderr.write(result.stderr)
        print("update: git pull did not complete cleanly — continuing anyway.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

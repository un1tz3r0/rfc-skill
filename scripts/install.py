#!/usr/bin/env python3
"""install.py — install the rfc skill into a Claude Code config directory.

Layout: this repo (``rfc-skill/``) holds dev-meta and build tooling at its
root; the shippable skill is the ``rfc/`` subdirectory. Only ``rfc/`` is
installed, as ``<config>/skills/rfc/`` — so the command becomes ``/rfc``.

Default behavior copies ``rfc/`` to ``~/.claude/skills/rfc/`` for the current
user. Flags select symlink mode, a different user/home, or a project-local
destination.

Run via ``./install.sh`` or directly:

    python3 scripts/install.py [--symlink] [--user U | --home DIR | --project DIR]
                               [--force] [--dry-run] [--uninstall] [--name NAME]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# scripts/ lives at the repo root; the shippable skill is the rfc/ subdir.
REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "rfc"
SKILL_NAME = SKILL_DIR.name  # -> "rfc" -> /rfc

# Items inside the skill dir that should NOT travel with an install.
IGNORE_NAMES = {"__pycache__", ".git", ".gitignore", ".claude", ".DS_Store"}


def _ignore(src: str, names: list[str]) -> list[str]:
    """shutil.copytree ignore callback — drops cache/build/git noise."""
    return [n for n in names if n in IGNORE_NAMES or n.endswith(".pyc")]


def resolve_user_home(user: str) -> Path:
    """Look up a user's home directory by name. Cross-platform best-effort."""
    try:
        import pwd  # Unix only
        return Path(pwd.getpwnam(user).pw_dir)
    except ImportError:
        pass
    except KeyError:
        raise SystemExit(f"error: no such user: {user!r}")

    expanded = os.path.expanduser(f"~{user}")
    if expanded != f"~{user}" and Path(expanded).exists():
        return Path(expanded)
    if os.name == "nt":
        candidate = Path(os.environ.get("SystemDrive", "C:")) / "Users" / user
        if candidate.exists():
            return candidate
    raise SystemExit(f"error: could not resolve home for user {user!r}")


def resolve_destination_root(args) -> tuple[Path, str]:
    """Return (dir-that-contains-'skills/', label)."""
    if args.project:
        root = Path(args.project).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"error: --project path is not a directory: {root}")
        return root / ".claude", f"project {root}"
    if args.home:
        root = Path(args.home).expanduser().resolve()
        if not root.exists():
            raise SystemExit(f"error: --home path does not exist: {root}")
        return root / ".claude", f"home {root}"
    if args.user:
        root = resolve_user_home(args.user)
        return root / ".claude", f"user {args.user} ({root})"
    root = Path.home()
    return root / ".claude", f"current user ({root})"


def remove_existing(dst: Path, dry_run: bool) -> None:
    if dst.is_symlink() or dst.exists():
        kind = "symlink" if dst.is_symlink() else ("dir" if dst.is_dir() else "file")
        print(f"removing existing {kind}: {dst}")
        if dry_run:
            return
        if dst.is_symlink() or not dst.is_dir():
            dst.unlink()
        else:
            shutil.rmtree(dst)


def do_copy(src: Path, dst: Path, dry_run: bool) -> None:
    print(f"copying {src} -> {dst}")
    if dry_run:
        return
    shutil.copytree(src, dst, ignore=_ignore, symlinks=False)


def do_symlink(src: Path, dst: Path, dry_run: bool) -> None:
    print(f"symlinking {dst} -> {src}")
    if dry_run:
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, dst, target_is_directory=True)
    except OSError as e:
        raise SystemExit(
            f"error: symlink failed ({e}).\n"
            "  On Windows, enable Developer Mode or run as Administrator,\n"
            "  or re-run with --copy instead of --symlink."
        ) from e


def verify_install(dst: Path) -> None:
    must_exist = [dst / "SKILL.md", dst / "scripts" / "rfc_tool.py"]
    missing = [p for p in must_exist if not p.exists()]
    if missing:
        print("WARNING: install incomplete — missing:", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(2)
    print(f"verified: SKILL.md + engine present at {dst}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="install.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("-s", "--symlink", action="store_true",
                      help="symlink instead of copying (edits stay live)")
    mode.add_argument("-c", "--copy", action="store_true",
                      help="copy (the default; explicit override)")

    where = p.add_mutually_exclusive_group()
    where.add_argument("-u", "--user", metavar="USER",
                       help="install into another user's home (looked up by name)")
    where.add_argument("-H", "--home", metavar="DIR",
                       help="install into the given home directory")
    where.add_argument("-p", "--project", metavar="DIR",
                       help="install into DIR/.claude/skills/ (project-local)")

    p.add_argument("--name", default=SKILL_NAME,
                   help=f"override the installed folder name (default: {SKILL_NAME})")
    p.add_argument("-f", "--force", action="store_true",
                   help="overwrite an existing destination")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="print actions without performing them")
    p.add_argument("--uninstall", action="store_true",
                   help="remove the skill from the resolved destination")
    args = p.parse_args(argv)

    if not SKILL_DIR.is_dir():
        raise SystemExit(f"error: shippable skill dir not found: {SKILL_DIR}")

    parent, label = resolve_destination_root(args)
    skills_dir = parent / "skills"
    name = args.name
    if name in ("", ".", "..") or "/" in name or "\\" in name or os.sep in name:
        raise SystemExit(f"error: --name must be a single path component, got {name!r}")
    dst = skills_dir / name

    print(f"source:      {SKILL_DIR}")
    print(f"destination: {dst}  ({label})")
    print(f"mode:        {'symlink' if args.symlink else 'copy'}"
          f"{'  [dry-run]' if args.dry_run else ''}")

    if args.uninstall:
        if not (dst.exists() or dst.is_symlink()):
            print(f"nothing to uninstall at {dst}")
            return 0
        remove_existing(dst, args.dry_run)
        print("uninstalled.")
        return 0

    if dst.exists() or dst.is_symlink():
        if not args.force:
            raise SystemExit(
                f"error: destination already exists: {dst}\n"
                "  re-run with --force to overwrite, or --uninstall to remove first."
            )
        remove_existing(dst, args.dry_run)

    if not args.dry_run:
        skills_dir.mkdir(parents=True, exist_ok=True)

    if args.symlink:
        do_symlink(SKILL_DIR, dst, args.dry_run)
    else:
        do_copy(SKILL_DIR, dst, args.dry_run)

    if not args.dry_run:
        verify_install(dst)
        print("done.  Invoke the skill with /rfc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# install.sh — thin wrapper around scripts/install.py.
# All flags are forwarded; see `./install.sh --help` for full usage.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH." >&2
    echo "Install Python 3.10+ and rerun." >&2
    exit 1
fi

exec python3 "${SKILL_DIR}/scripts/install.py" "$@"

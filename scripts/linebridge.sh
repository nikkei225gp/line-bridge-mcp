#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="$root/.venv/bin/python"
[[ -x "$python" ]] || { printf '%s\n' 'Install first: bash scripts/install.sh' >&2; exit 1; }
if [[ -n "${LINEBRIDGE_DATA_DIR:-}" ]]; then
    exec "$python" -B "$root/bin/linebridge.py" --data-dir "$LINEBRIDGE_DATA_DIR" "$@"
fi
exec "$python" -B "$root/bin/linebridge.py" "$@"

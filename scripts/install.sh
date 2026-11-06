#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'This trial requires Linux x86_64 (Ubuntu 24.04 LTS).' >&2
    exit 1
fi
if [[ "$EUID" -eq 0 ]]; then
    printf '%s\n' 'Run as your normal desktop user, without sudo.' >&2
    exit 1
fi
command -v python3 >/dev/null || { printf '%s\n' 'Install python3 first.' >&2; exit 1; }
data_dir="${LINEBRIDGE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/linebridge/profiles/default}"
if [[ -f "$data_dir/config.json" ]]; then
    printf '%s\n' 'Profile exists. Use login/start or select a NEW LINEBRIDGE_DATA_DIR. No overwrite.' >&2
    exit 1
fi
cd -- "$root"
python3 -B tools/bootstrap-runtime.py --uv-only
"$root/runtime/uv/uv" sync --frozen --no-dev --no-editable --python 3.11.15
python="$root/.venv/bin/python"
"$python" -B tools/bootstrap-runtime.py
"$python" -B bin/linebridge.py --data-dir "$data_dir" init
"$python" -B bin/linebridge.py --data-dir "$data_dir" runtime \
    --deno "$root/runtime/deno/deno" --linejs "$root/runtime/linejs" \
    --deno-cache "$root/runtime/deno-cache"
printf '%s\n' 'Installed. Login, monitoring, notifications and autostart remain off; sending is on once logged in.'

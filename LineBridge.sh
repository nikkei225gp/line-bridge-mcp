#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
printf '%s\n' 'LineBridge 0.1.0b4 - Ubuntu 24.04 LTS x86_64 desktop trial'
printf '%s\n' 'Read START-HERE-LINUX.md before login. Use your own LINE account.'
while true; do
    printf '\n%s\n' '1 Install   2 QR login (window)   3 Start   4 Status   5 Stop'
    printf '%s\n' '6 Codex config   7 OpenCode V2 config   8 Doctor   9 Terminal QR   0 Exit'
    printf '%s\n' 'Exit menu does not stop the bridge. Use 5 to stop it.'
    read -r -p 'Select: ' choice || exit 0
    case "$choice" in
        0) exit 0 ;;
        1) bash "$root/scripts/install.sh" || true ;;
        2) bash "$root/scripts/linebridge.sh" login || true ;;
        3) bash "$root/scripts/linebridge.sh" start || true ;;
        4) bash "$root/scripts/linebridge.sh" status || true ;;
        5) bash "$root/scripts/linebridge.sh" stop || true ;;
        6) bash "$root/scripts/linebridge.sh" clients --format codex || true ;;
        7) bash "$root/scripts/linebridge.sh" clients --format opencode || true ;;
        8) bash "$root/scripts/linebridge.sh" doctor || true ;;
        9) LINEBRIDGE_LOGIN_UI=terminal bash "$root/scripts/linebridge.sh" login || true ;;
        *) printf '%s\n' 'Select 0 through 9.' ;;
    esac
done

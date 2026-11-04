#!/bin/sh
set -eu
command -v python3 >/dev/null 2>&1 || { echo 'Python 3 is required.' >&2; exit 1; }
exec python3 "$(dirname "$0")/local-macos.py" setup "$@"

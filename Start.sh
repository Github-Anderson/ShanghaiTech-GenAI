#!/bin/sh
set -eu
exec python3 "$(dirname "$0")/tools/local-macos.py" start "$@"

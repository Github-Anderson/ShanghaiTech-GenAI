#!/bin/sh
set -eu
# ShanghaiTech GenAI v1.1.1
exec python3 "$(dirname "$0")/tools/local-macos.py" gateway "$@"

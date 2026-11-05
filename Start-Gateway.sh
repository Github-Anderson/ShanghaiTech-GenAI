#!/bin/sh
set -eu
# ShanghaiTech GenAI v1.0.9
exec python3 "$(dirname "$0")/tools/local-macos.py" gateway "$@"

#!/bin/sh
set -eu
exec python3 "$(dirname "$0")/local-macos.py" setup --environment development "$@"

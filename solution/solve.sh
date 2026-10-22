#!/bin/sh
set -eu
TARGET_DIR=${TARGET_DIR:-/app}
cp "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/visibility.py" "$TARGET_DIR/visibility.py"

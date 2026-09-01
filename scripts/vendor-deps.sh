#!/usr/bin/env bash
# Vendor pure-Python runtime deps into py_modules/ for deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf py_modules
python3 -m pip install --target py_modules --no-deps --no-compile segno

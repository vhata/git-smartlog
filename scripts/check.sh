#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python_bin="${PYTHON:-python3}"
"$python_bin" -m compileall -q smartlog git-smartlog.py
"$python_bin" -m unittest discover -s tests -v
if [[ -e .git ]]; then
    git diff --check
fi

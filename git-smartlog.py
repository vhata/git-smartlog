#!/usr/bin/env python3
"""Legacy executable; also works when symlinked onto PATH as git-smartlog."""

from pathlib import Path
import sys

# Python otherwise places the symlink's directory on sys.path on some hosts.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smartlog.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

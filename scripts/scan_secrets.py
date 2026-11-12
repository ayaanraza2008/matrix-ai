#!/usr/bin/env python
"""Thin wrapper so the secret scan can be run from scripts/ or CI."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from matrix_ai.security.secret_scan import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Writable account data lives separately from the bundled backend sources."""

import os
from pathlib import Path


def data_root() -> Path:
    configured = os.environ.get("FRC_BACKEND_DATA_DIR")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]

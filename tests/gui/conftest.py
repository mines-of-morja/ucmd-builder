"""GUI tests (design/B2a.md §9): skipped without the extra `gui-test`, headless by default."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

NEEDED = ("PySide6", "pyvista", "pyvistaqt", "h5py", "pytestqt")
MISSING = [m for m in NEEDED if importlib.util.find_spec(m) is None]

if MISSING:
    if os.environ.get("UCMD_REQUIRE_GUI"):
        raise RuntimeError(
            f"UCMD_REQUIRE_GUI is set but {', '.join(MISSING)} are missing: "
            "pip install -e '.[gui-test]'"
        )
    collect_ignore_glob = ["test_*.py"]

# no windows on the desktop; the 3D view renders off screen through VTK
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ helpers such as test_build

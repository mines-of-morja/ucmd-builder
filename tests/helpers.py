"""Test helpers that test modules import by name (a conftest.py cannot be imported
reliably: tests/gui has its own)."""

from __future__ import annotations

import os

import pytest


def packmol_available() -> bool:
    try:
        from ucmd_builder.packing.packmol import find_packmol

        find_packmol()
        return True
    except Exception:
        return False


requires_packmol = pytest.mark.skipif(
    not packmol_available() and not os.environ.get("UCMD_REQUIRE_PACKMOL"),
    reason="Packmol not installed (pip install -e '.[packmol]'); "
    "UCMD_REQUIRE_PACKMOL=1 makes this fail",
)

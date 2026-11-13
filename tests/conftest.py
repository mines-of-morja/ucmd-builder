"""Shared test helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

WORKSPACE_SPEC = Path(__file__).resolve().parents[2] / "spec"


def spec_dir() -> Path | None:
    """The spec directory: $UCMD_SPEC_DIR, else ../spec in the workspace layout."""
    env = os.environ.get("UCMD_SPEC_DIR")
    path = Path(env) if env else WORKSPACE_SPEC
    return path if (path / "fixtures").is_dir() else None


@pytest.fixture
def spec() -> Path:
    found = spec_dir()
    if found is None:
        if os.environ.get("UCMD_REQUIRE_SPEC"):
            pytest.fail("spec fixtures not found (set UCMD_SPEC_DIR)")
        pytest.skip("spec fixtures not found (set UCMD_SPEC_DIR or use the workspace layout)")
    return found

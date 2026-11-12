"""The per-machine profile (spec/run-config.md §4, ADR 0002).

Machine-specific settings, such as the absolute paths of external programs, live in a TOML
file outside every recipe and package. The builder reads only `[executables]`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


def profile_path() -> Path | None:
    """The first existing profile in the spec's lookup order, or None."""
    candidates = []
    if os.environ.get("UCMD_MACHINE_PROFILE"):
        candidates.append(Path(os.environ["UCMD_MACHINE_PROFILE"]))
    if os.environ.get("XDG_CONFIG_HOME"):
        candidates.append(Path(os.environ["XDG_CONFIG_HOME"]) / "ucmd" / "machine.toml")
    candidates.append(Path.home() / ".config" / "ucmd" / "machine.toml")
    return next((p for p in candidates if p.is_file()), None)


def load_profile() -> dict[str, Any]:
    path = profile_path()
    if path is None:
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def executable(name: str) -> Path | None:
    """The absolute path configured for `name` under [executables], if any."""
    value = load_profile().get("executables", {}).get(name)
    return Path(value) if value else None

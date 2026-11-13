"""Test doubles for the GUI tests."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


class FakeViewer:
    """Records what the window asks the 3D view to do."""

    def __init__(self) -> None:
        self.scenes, self.highlights, self.frames, self.pick = [], [], [], None

    def show_scene(self, scene) -> None:
        self.scenes.append(scene)

    def show_positions(self, positions_nm) -> None:
        self.frames.append(positions_nm)

    def highlight(self, atoms) -> None:
        self.highlights.append(list(atoms))

    def on_pick(self, callback) -> None:
        self.pick = callback

    def screenshot(self, path) -> None:
        pass


def fake_engine(tmp_path: Path, monkeypatch) -> Path:
    """An executable `ucmd` that runs fake_ucmd.py, registered in a temporary machine profile."""
    script = Path(__file__).with_name("fake_ucmd.py")
    wrapper = tmp_path / "ucmd"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    profile = tmp_path / "machine.toml"
    profile.write_text(f'# test profile\n[executables]\nucmd = "{wrapper}"\n')
    monkeypatch.setenv("UCMD_MACHINE_PROFILE", str(profile))
    monkeypatch.delenv("FAKE_UCMD_FAIL", raising=False)
    os.environ.setdefault("FAKE_UCMD_DELAY", "0.005")
    return wrapper

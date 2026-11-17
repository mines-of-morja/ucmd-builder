"""The run preview with a fake engine (design/B2a.md §7, §9): start, progress, playback,
stop, and the error display."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fakes import FakeViewer, fake_engine
from sample_recipes import ELECTROLYTE, STAMP

from ucmd_builder.build import build
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.run_preview import RunDialog
from ucmd_builder.gui.runs import RunSettings
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("run")
    built = build(parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml"))
    return write_package(built, tmp / "small.ucsys", created_utc=STAMP)


@pytest.fixture
def window(qtbot, package: Path):
    w = MainWindow(viewer=FakeViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    assert w.open_package(package)
    return w


def test_a_run_shows_progress_and_plays_the_trajectory(
    qtbot, window, tmp_path: Path, monkeypatch
) -> None:
    fake_engine(tmp_path, monkeypatch)
    settings = RunSettings(length_ps=0.06, frame_every=10)
    run_dir = window.start_run(settings)
    assert run_dir is not None and window.is_running()
    assert run_dir.parent == window.package_path.parent / f"{window.package_path.name}.runs"
    assert (run_dir / "minimize.toml").is_file() and (run_dir / "run.toml").is_file()
    qtbot.waitUntil(lambda: not window.is_running(), timeout=60_000)
    log = window.log.toPlainText()
    assert "run finished" in log, log
    assert "converged: 3 steps (fake engine)" in log  # engine output reaches the log
    assert "cutoff is reduced to 1.10 nm" in log  # the 2.6 nm test box
    panel = window.run_panel
    assert panel.plots.points("temperature_K") == 60 // 10 + 1
    assert panel.plots.points("conserved_energy_kjmol") == 7
    assert len(panel.trajectory) == 60 // 10 + 1
    panel.slider.setValue(3)
    frame = window.viewer.frames[-1]
    assert np.allclose(frame, window.scene.positions_nm + 0.001 * 30)
    assert "frame 4/7" in panel.frame_label.text()


def test_stop_ends_the_run_gracefully(qtbot, window, tmp_path: Path, monkeypatch) -> None:
    fake_engine(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_UCMD_DELAY", "0.02")
    window.start_run(RunSettings(length_ps=100.0, minimize=False))
    qtbot.waitUntil(lambda: "step" in window.run_panel.status.text(), timeout=30_000)
    window.stop_run()
    qtbot.waitUntil(lambda: not window.is_running(), timeout=30_000)
    log = window.log.toPlainText()
    assert "stopped; the engine wrote a checkpoint" in log, log
    assert "stopped by SIGTERM" in log
    assert window.run_panel.trajectory is not None  # the frames written so far can be played


def test_an_engine_error_is_shown(qtbot, window, tmp_path: Path, monkeypatch) -> None:
    fake_engine(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_UCMD_FAIL", "1")
    window.start_run(RunSettings(length_ps=0.01))
    qtbot.waitUntil(lambda: not window.is_running(), timeout=30_000)
    log = window.log.toPlainText()
    assert "E_TEST  the fake engine fails on purpose" in log
    assert "minimizing failed (exit status 1)" in log


def test_without_an_engine_nothing_starts(window, tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "empty.toml"
    profile.write_text("threads = 2\n")
    monkeypatch.setenv("UCMD_MACHINE_PROFILE", str(profile))
    assert window.start_run(RunSettings()) is None
    assert "set [executables] ucmd" in window.log.toPlainText()


def test_the_dialog_shows_the_files_it_will_write(
    qtbot, window, tmp_path: Path, monkeypatch
) -> None:
    fake_engine(tmp_path, monkeypatch)
    dialog = RunDialog(window.package_path, window.scene, window.ask, window)
    qtbot.addWidget(dialog)
    text = dialog.preview.toPlainText()
    assert "# minimize.toml" in text and "# run.toml" in text
    assert 'kind = "nose-hoover-chain"' in text and "temperature_K = 298.15" in text
    assert "1.10 nm" in dialog.cutoff_label.text()
    dialog.thermostat.setCurrentText("csvr")
    dialog.minimize.setChecked(False)
    text = dialog.preview.toPlainText()
    assert 'kind = "csvr"' in text and "# minimize.toml" not in text
    assert dialog.settings().steps == 2000


def test_a_finished_run_opens_again(qtbot, window, tmp_path: Path, monkeypatch) -> None:
    fake_engine(tmp_path, monkeypatch)
    run_dir = window.start_run(RunSettings(length_ps=0.06, frame_every=10))
    qtbot.waitUntil(lambda: not window.is_running(), timeout=60_000)
    later = MainWindow(viewer=FakeViewer())  # e.g. the next session
    qtbot.addWidget(later)
    assert later.open_path(run_dir), later.log.toPlainText()
    assert later.package_path == run_dir / "minimized.ucsys"  # the run's starting package
    panel = later.run_panel
    assert panel.plots.points("temperature_K") == 7
    assert len(panel.trajectory) == 7 and panel.play_button.isEnabled()
    panel.slider.setValue(6)
    assert np.allclose(later.viewer.frames[-1], later.scene.positions_nm + 0.001 * 60)
    assert "7 thermo rows, 7 frames" in later.log.toPlainText()


def test_a_command_line_run_opens(qtbot, window, package: Path, tmp_path: Path) -> None:
    """A run started with `ucmd run`, with its own output names, plays in the GUI."""
    import os
    import subprocess
    import sys

    run_dir = tmp_path / "cli-run"
    run_dir.mkdir()
    relative = Path(os.path.relpath(package, run_dir)).as_posix()
    (run_dir / "run.toml").write_text(
        f'spec_version = "0.1.0"\nsystem = "{relative}"\ntitle = "from the shell"\n'
        "[run]\nsteps = 40\ntimestep_fs = 1.0\n"
        '[thermostat]\nkind = "csvr"\ntemperature_K = 310.0\ntau_ps = 0.1\n'
        '[output]\ndirectory = "results"\n'
        '[output.thermo]\nfile = "energies.tsv"\nevery = 5\n'
        '[output.trajectory]\nfile = "md.h5"\nevery = 20\n'
    )
    fake = Path(__file__).with_name("fake_ucmd.py")
    env = {"FAKE_UCMD_DELAY": "0", "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, str(fake), "run", str(run_dir / "run.toml")], check=True, env=env
    )
    assert window.open_path(run_dir / "run.toml"), window.log.toPlainText()
    panel = window.run_panel
    assert panel.plots.points("temperature_K") == 40 // 5 + 1
    assert panel.plots.target_K == 310.0
    assert len(panel.trajectory) == 3
    assert "from the shell" in panel.status.text()


def test_opening_a_run_without_its_package_is_refused(window, tmp_path: Path) -> None:
    (tmp_path / "run.toml").write_text('system = "minimized.ucsys"\n')
    assert not window.open_run(tmp_path)
    assert "did the minimization finish?" in window.log.toPlainText()

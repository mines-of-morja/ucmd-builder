"""B2d step 4: runs are read in a thread, frames when they are shown (design/B2d.md)."""

from __future__ import annotations

import time
from itertools import pairwise
from pathlib import Path

import h5py
import numpy as np
import pytest
from fakes import FakeViewer
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel
from test_molecule_file import cluster_xyz
from test_project import make_run

from ucmd_builder.build import build
from ucmd_builder.gui import loading, trajectory
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.thermo import ThermoTail
from ucmd_builder.gui.trajectory import H5Positions, read_h5md
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from sample_recipes import ELECTROLYTE, STAMP

    tmp = tmp_path_factory.mktemp("loading")
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml")
    return write_package(build(recipe), tmp / "p.ucsys", created_utc=STAMP)


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    w.background = True
    qtbot.addWidget(w)
    w.show()
    return w


def h5_file(path: Path, frames: int = 6, atoms: int = 4) -> np.ndarray:
    x = np.random.default_rng(1).normal(size=(frames, atoms, 3)).astype(np.float32)
    with h5py.File(path, "w") as fh:
        group = fh.create_group("particles/all/position")
        group["step"] = np.arange(frames) * 10
        group["time"] = np.arange(frames) * 0.01
        group["value"] = x
    return x


def h5_run(folder: Path, package: Path, title: str, frames: int = 3) -> Path:
    make_run(folder, package, title, 100)
    n = len(np.load(package / "positions.npy"))
    h5_file(folder / "out" / "t.h5", frames, n)
    with (folder / "run.toml").open("a") as fh:
        fh.write('[output.trajectory]\nfile = "t.h5"\nevery = 50\n')
    return folder


# -- frames on demand --------------------------------------------------------------------------


def test_frames_are_those_of_the_file(tmp_path: Path) -> None:
    x = h5_file(tmp_path / "t.h5")
    t = read_h5md(tmp_path / "t.h5")
    assert isinstance(t.positions_nm, H5Positions) and t.positions_nm.shape == x.shape
    for k in (3, 0, 5, 3, -1):
        assert np.array_equal(t.positions_nm[k], x[k])
    with pytest.raises(IndexError):
        t.positions_nm[6]


def test_few_frames_stay_in_memory(tmp_path: Path, monkeypatch) -> None:
    x = h5_file(tmp_path / "t.h5")
    monkeypatch.setattr(trajectory, "CACHE_BYTES", 2 * 4 * 3 * 4)  # two frames of four atoms
    positions = read_h5md(tmp_path / "t.h5").positions_nm
    for k in range(6):
        assert np.array_equal(positions[k], x[k])
    assert list(positions._cache) == [4, 5]


def test_a_changed_file_is_an_error_not_another_frame(window, tmp_path: Path) -> None:
    h5_file(tmp_path / "t.h5")
    t = read_h5md(tmp_path / "t.h5")
    with h5py.File(tmp_path / "t.h5", "a") as fh:  # restarted with other steps
        fh["particles/all/position/step"][...] = np.arange(6) * 7
    assert t.positions_nm[0] is not None  # step 0 is still step 0
    with pytest.raises(ValueError, match="has changed"):
        t.positions_nm[2]
    (tmp_path / "t.h5").unlink()
    panel = window.run_panel
    panel.trajectory = t
    emitted = []
    panel.frame.connect(lambda *a: emitted.append(a))
    panel.show_frame(4)
    assert emitted == [] and "not readable" in panel.frame_label.text()


def test_the_file_is_not_kept_open(tmp_path: Path) -> None:
    h5_file(tmp_path / "t.h5")
    t = read_h5md(tmp_path / "t.h5")
    t.positions_nm[1]
    with h5py.File(tmp_path / "t.h5", "r+") as fh:  # an engine continues the run
        fh["particles/all/position/time"][0] = 0.0


# -- the thermo table in blocks ----------------------------------------------------------------


def test_blocks_give_the_same_numbers_and_skip_broken_rows(tmp_path: Path) -> None:
    path = tmp_path / "t.tsv"
    good = [f"{i}\t{i * 0.5}\t{'nan' if i % 3 else i}" for i in range(9000)]
    broken = [*good[:4100], "1\t2", "a\tb\tc", "", *good[4100:]]
    path.write_text("# step\ttime_ps\tp\n" + "\n".join(broken) + "\n8999\t1.0")  # last: partial
    tail = ThermoTail(path)
    assert tail.poll() == 9000 and len(tail) == 9000
    assert np.array_equal(tail.column("step"), np.arange(9000))
    assert np.isnan(tail.column("p")).sum() == 6000
    with path.open("a") as fh:
        fh.write("\t5.0\n")
    assert tail.poll() == 1 and tail.column("p")[-1] == 5.0
    assert tail.poll() == 0
    path.write_text("# step\ttime_ps\tp\n7\t1.0\t2.0\n")  # rewritten by a restart
    assert tail.poll() == 1 and list(tail.column("step")) == [7.0]


# -- in a thread -------------------------------------------------------------------------------


def test_a_run_opens_in_the_background(qtbot, window, package: Path, tmp_path: Path) -> None:
    run = h5_run(tmp_path / "run", package, "in the background")
    assert window.open_path(run)
    assert window.is_loading() and window.shown == ""  # returned at once
    qtbot.waitUntil(lambda: not window.is_loading(), timeout=20_000)
    assert window.shown == "in the background" and len(window.run_panel.trajectory) == 3
    assert len(window.viewer.frames) >= 1


def test_the_window_answers_while_a_run_is_read(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    run = h5_run(tmp_path / "run", package, "slow")
    load = loading.load_run
    monkeypatch.setattr(loading, "load_run", lambda p: time.sleep(0.5) or load(p))
    beats: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    window.open_path(run)
    qtbot.waitUntil(lambda: window.shown == "slow", timeout=20_000)
    during = [b - a for a, b in pairwise(beats)][:30]
    assert len(during) == 30 and max(during) < 0.1


def test_the_last_request_wins(qtbot, window, package: Path, tmp_path: Path, monkeypatch) -> None:
    slow = h5_run(tmp_path / "slow", package, "the slow one")
    fast = h5_run(tmp_path / "fast", package, "the fast one")
    load = loading.load_run
    monkeypatch.setattr(
        loading, "load_run", lambda p: time.sleep(0.6 if Path(p).name == "slow" else 0) or load(p)
    )
    window.open_path(slow)
    window.open_path(fast)
    qtbot.waitUntil(lambda: not window.is_loading(), timeout=20_000)
    qtbot.wait(50)
    assert window.shown == "the fast one"
    # something else opened meanwhile also wins
    window.open_path(slow)
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    window.open_path(tmp_path / "c.xyz")
    qtbot.waitUntil(lambda: not window.is_loading(), timeout=20_000)
    qtbot.wait(50)
    assert window.shown.startswith("c · 41 atoms")


def test_a_run_that_cannot_be_read_is_said_and_forgotten(qtbot, window, tmp_path: Path) -> None:
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "run.toml").write_text('system = "missing.ucsys"\n')
    window.open_by_user([tmp_path / "bad"])
    qtbot.waitUntil(lambda: not window.is_loading(), timeout=20_000)
    qtbot.wait(50)
    assert "did the minimization finish?" in window.log.toPlainText()
    assert window.settings.recent == []


def test_an_unexpected_fault_reaches_the_log(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    run = h5_run(tmp_path / "run", package, "x")

    def broken(_path):
        raise ZeroDivisionError("oops")

    monkeypatch.setattr(loading, "load_run", broken)
    window.open_path(run)
    qtbot.waitUntil(lambda: not window.is_loading(), timeout=20_000)
    qtbot.wait(50)
    assert "ZeroDivisionError: oops" in window.log.toPlainText()


def test_closing_waits_for_the_thread(qtbot, window, package: Path, tmp_path: Path, monkeypatch):
    run = h5_run(tmp_path / "run", package, "x")
    load = loading.load_run
    monkeypatch.setattr(loading, "load_run", lambda p: time.sleep(0.3) or load(p))
    window.open_path(run)
    assert window.close()
    assert all(t.isFinished() for t in window._loaders)


def test_a_trajectory_of_other_atoms_is_not_played(qtbot, window, package: Path, tmp_path: Path):
    run = h5_run(tmp_path / "run", package, "other atoms")
    h5_file(run / "out" / "t.h5", 3, 5)
    window.open_path(run)
    qtbot.waitUntil(lambda: window.shown == "other atoms", timeout=20_000)
    assert "the trajectory has 5 atoms" in window.log.toPlainText()
    assert window.run_panel.trajectory is None

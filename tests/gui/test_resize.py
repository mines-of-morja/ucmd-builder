"""The window can be made small in every state, panels come back, resizing stays fast."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fakes import FakeViewer
from PySide6.QtWidgets import QDockWidget, QLabel
from test_b2c_defects import tail, thermo
from test_molecule_file import cluster_xyz
from test_notebook import RUNS, TRENDS

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.plots import MAX_POINTS, LivePlots, envelope

SMALL = (900, 600)  # fits a 1366 x 768 laptop with room for the desktop's panels


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    return w


def open_nothing(window, tmp_path: Path) -> None:
    pass


def open_plots(window, tmp_path: Path) -> None:
    window.run_panel.plots.update_from(tail(thermo(tmp_path / "t.tsv", rows=1000)))
    window.run_panel.status.setText("2026-11-24_parameter-sensitivity-1M: a long title " * 2)


def open_molecule(window, tmp_path: Path) -> None:
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    assert window.open_path(tmp_path / "c.xyz")


def open_tables(window, tmp_path: Path) -> None:
    long = "# note: " + "a long note that has to wrap " * 6 + "\n"
    (tmp_path / "runs.tsv").write_text(RUNS.replace("# note", long + "# note"))
    (tmp_path / "trends.tsv").write_text(TRENDS)
    assert window.open_path(tmp_path)


@pytest.mark.parametrize("state", [open_nothing, open_plots, open_molecule, open_tables])
def test_the_window_can_be_small(qtbot, window, tmp_path: Path, state) -> None:
    state(window, tmp_path)
    qtbot.wait(50)
    hint = window.minimumSizeHint()
    assert hint.width() <= SMALL[0] and hint.height() <= SMALL[1], (hint.width(), hint.height())
    window.resize(*SMALL)
    qtbot.wait(50)
    assert (window.width(), window.height()) == SMALL
    window.resize(1400, 900)
    qtbot.wait(50)
    assert (window.width(), window.height()) == (1400, 900)


def test_closed_panels_come_back_from_the_view_menu(window) -> None:
    (view,) = [a.menu() for a in window.menuBar().actions() if a.text() == "&View"]
    names = {d.windowTitle(): d for d in window.findChildren(QDockWidget)}
    actions = {a.text(): a for a in view.actions()}
    assert set(names) <= set(actions)
    for title, dock in names.items():
        dock.close()
        assert dock.isHidden() and not actions[title].isChecked()
        actions[title].trigger()
        assert not dock.isHidden()


def test_reset_layout_shows_every_panel_again(window) -> None:
    for dock in window.findChildren(QDockWidget):
        dock.close()
        dock.setFloating(True)
    window.reset_layout()
    for dock in window.findChildren(QDockWidget):
        assert not dock.isHidden() and not dock.isFloating()


def test_a_long_curve_is_drawn_with_few_points(qtbot, tmp_path: Path) -> None:
    plots = LivePlots()
    qtbot.addWidget(plots)
    plots.update_from(tail(thermo(tmp_path / "t.tsv", rows=20_000, every=1)))
    assert plots.points("volume_nm3") <= MAX_POINTS
    assert plots.points("pressure_bar") <= MAX_POINTS


def test_the_envelope_keeps_the_extremes_and_the_ends() -> None:
    rng = np.random.default_rng(3)
    x = np.arange(50_001) * 0.1
    y = rng.normal(size=len(x))
    y[12_345], y[40_000] = 50.0, -60.0
    ex, ey = envelope(x, y)
    assert len(ex) <= MAX_POINTS and (np.diff(ex) > 0).all()
    assert ey.max() == 50.0 and ey.min() == -60.0
    assert ex[0] == x[0] and ex[-1] == x[-1]
    assert set(zip(ex, ey, strict=True)) <= set(zip(x, y, strict=True))  # real points only
    short = np.arange(MAX_POINTS) * 1.0
    assert np.array_equal(envelope(short, short)[0], short)


def test_the_envelope_shows_a_nan_column() -> None:
    """A fault in a column must not disappear because the curve is thinned."""
    x = np.arange(10_000) * 1.0
    y = np.ones(10_000)
    y[5_000] = np.nan
    assert np.isnan(envelope(x, y)[1]).any()

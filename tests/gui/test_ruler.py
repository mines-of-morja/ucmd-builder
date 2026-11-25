"""The ruler (design/B2c.md §4): distances, angles, nearest atoms, periodic images."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fakes import FakeViewer
from test_molecule_file import cluster_xyz

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.ruler import angle_deg, distance_nm, minimum_image, nearest


def brute_force(v: np.ndarray, cell: np.ndarray) -> float:
    n = range(-6, 7)  # more than 4 nm / the smallest cell height needs
    return min(np.linalg.norm(v + np.array([i, j, k]) @ cell) for i in n for j in n for k in n)


@pytest.mark.parametrize(
    "cell",
    [
        np.diag([2.0, 3.0, 4.0]),
        np.array([[4.0, 0, 0], [1.5, 3.5, 0], [-1.2, 1.4, 3.0]]),  # skewed
    ],
)
def test_minimum_image_is_the_shortest(cell: np.ndarray) -> None:
    rng = np.random.default_rng(1)
    for v in rng.uniform(-4.0, 4.0, (100, 3)):
        assert np.linalg.norm(minimum_image(v, cell)[0]) == pytest.approx(brute_force(v, cell))


def test_distance_and_angle_without_a_cell() -> None:
    x = np.array([[0.0, 0, 0], [0.3, 0.4, 0], [0.3, 0.4, 0.7]])
    assert distance_nm(x, 0, 1) == pytest.approx(0.5, abs=1e-12)
    assert angle_deg(x, 0, 1, 2) == pytest.approx(90.0, abs=1e-9)


def test_distance_across_the_boundary() -> None:
    x = np.array([[0.05, 0, 0], [1.95, 0, 0]])
    assert distance_nm(x, 0, 1, np.diag([2.0, 2.0, 2.0])) == pytest.approx(0.1)
    assert distance_nm(x, 0, 1) == pytest.approx(1.9)


def test_nearest_is_sorted_and_leaves_the_atom_out() -> None:
    x = np.array([[0.0, 0, 0], [0.5, 0, 0], [0.2, 0, 0], [1.9, 0, 0]])
    got = nearest(x, 0, np.arange(4), np.diag([2.0, 2.0, 2.0]))
    assert [i for i, _ in got] == [3, 2, 1]
    assert [d for _, d in got] == pytest.approx([0.1, 0.2, 0.5])


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=FakeViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    return w


def test_the_panel_measures_the_shown_frame(window, tmp_path: Path) -> None:
    path = tmp_path / "path.xyz"
    path.write_text(cluster_xyz(1, 0.0) + cluster_xyz(2, 1.0))  # Li+ moves by 1 Å
    assert window.open_path(path), window.log.toPlainText()
    ruler, x = window.ruler, window.run_panel.trajectory.positions_nm
    ruler.measure.setChecked(True)
    window.viewer.pick(0)
    oxygen = [i for i, e in enumerate(window.scene.elements) if e == "O"]
    window.viewer.pick(oxygen[0])
    want = float(np.linalg.norm(x[1][0] - x[1][oxygen[0]]))
    assert float(ruler.result.text().split()[1]) == pytest.approx(want, abs=1e-6)
    assert window.viewer.highlights[-1] == [0, oxygen[0]]
    window.run_panel.slider.setValue(0)
    want = float(np.linalg.norm(x[0][0] - x[0][oxygen[0]]))
    assert float(ruler.result.text().split()[1]) == pytest.approx(want, abs=1e-6)
    assert window.viewer.highlights[-1] == [0, oxygen[0]]  # moved with the frame
    window.viewer.pick(oxygen[1])
    assert ruler.result.text().endswith("°") and len(ruler.picked) == 3
    window.viewer.pick(0)
    assert ruler.picked == [0] and "picked" in ruler.result.text()


def test_the_nearest_oxygens_of_an_ion(window, tmp_path: Path) -> None:
    path = tmp_path / "c.xyz"
    path.write_text(cluster_xyz())
    assert window.open_path(path)
    window.viewer.pick(0)
    table, x = window.ruler.table, window.scene.positions_nm
    assert window.ruler.element.currentText() == "O" and table.rowCount() == 8
    shown = [float(table.item(r, 2).text()) for r in range(8)]
    every = sorted(
        float(np.linalg.norm(x[i] - x[0])) for i, e in enumerate(window.scene.elements) if e == "O"
    )
    assert shown == pytest.approx(every[:8], abs=1e-4) and shown == sorted(shown)
    assert table.item(0, 1).text().startswith("C3H4O3 #")


def test_the_component_selection_and_clear(window, tmp_path: Path) -> None:
    path = tmp_path / "c.xyz"
    path.write_text(cluster_xyz())
    window.open_path(path)
    window.viewer.pick(3)
    assert window.viewer.highlights[-1] == [3]  # as before the ruler
    window.ruler.clear()
    assert window.viewer.highlights[-1] == [] and window.ruler.table.rowCount() == 0

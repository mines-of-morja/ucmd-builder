"""The 3D view (design/B2a.md §4, §9): the scene renders, moves and picks off screen."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv
from sample_recipes import ELECTROLYTE, STAMP

from ucmd_builder.build import build
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.scene import Scene
from ucmd_builder.gui.viewer import SceneActors, cell_wireframe
from ucmd_builder.package import system_document
from ucmd_builder.recipe import parse_recipe


@pytest.fixture(scope="module")
def scene(tmp_path_factory: pytest.TempPathFactory) -> Scene:
    tmp = tmp_path_factory.mktemp("viewer")
    built = build(parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml"))
    return Scene.from_document(system_document(built, STAMP), built.positions_nm)


def coloured(image: np.ndarray) -> int:
    """Pixels that are not white background."""
    return int((image.min(axis=2) < 240).sum())


def test_the_cell_has_twelve_edges() -> None:
    frame = cell_wireframe(np.diag([2.0, 3.0, 4.0]))
    assert frame.n_points == 8 and frame.n_lines == 12
    lengths = sorted(
        np.linalg.norm(frame.points[a] - frame.points[b])
        for a, b in frame.lines.reshape(-1, 3)[:, 1:]
    )
    assert np.allclose(lengths, [2.0] * 4 + [3.0] * 4 + [4.0] * 4)


def test_scene_renders_moves_and_picks(scene: Scene, tmp_path: Path) -> None:
    plotter = pv.Plotter(off_screen=True, window_size=(400, 400))
    actors = SceneActors(plotter)
    actors.show(scene)
    first = plotter.screenshot(tmp_path / "scene.png")
    assert coloured(first) > 5000  # atoms, bonds and cell are drawn
    oxygen_red = (first[..., 0] > 180) & (first[..., 1] < 80) & (first[..., 2] < 80)
    assert oxygen_red.sum() > 50  # Jmol red for the carbonate oxygens
    target = actors.points[42]
    assert actors.nearest_atom(target + 0.001) == 42
    actors.move(scene.positions_nm + np.array([0.3, 0.0, 0.0]))
    assert not np.allclose(actors.points, scene.display_positions())
    moved = plotter.screenshot()
    assert np.abs(moved.astype(int) - first.astype(int)).sum() > 0
    actors.highlight([0, 1, 2])
    actors.highlight([])
    actors.show_images(True)  # 26 faded copies around the cell
    assert actors._images.n_points == 26 * scene.n_atoms
    shift = actors._images.points[: scene.n_atoms] - actors.points
    assert np.allclose(shift, -scene.cell_nm.sum(axis=0))  # the (−1, −1, −1) image first
    with_images = plotter.screenshot()
    assert coloured(with_images) > coloured(moved)
    actors.move(scene.positions_nm)  # playback moves the images too
    assert np.allclose(actors._images.points[: scene.n_atoms] - actors.points, shift)
    actors.show_images(False)
    assert actors._images is None
    plotter.close()


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="the Qt OpenGL widget needs a display (CI runs this under Xvfb)",
)
def test_the_main_window_with_the_real_viewer(qtbot, scene: Scene, tmp_path: Path) -> None:
    window = MainWindow()
    window.ask = lambda _q: True
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window.show()
    qtbot.waitExposed(window)
    window.show_scene(scene)
    image = pv.read(_shot(window, tmp_path / "window.png"))
    pixels = np.asarray(image.point_data[image.point_data.keys()[0]])[:, :3]
    white = (pixels.min(axis=1) >= 240).mean()
    # a real render: mostly the white background, plus thousands of coloured atom pixels
    # (an unusable GL context gives noise, which fails the first condition)
    assert white > 0.5, f"only {white:.0%} background"
    assert int((pixels.min(axis=1) < 240).sum()) > 5000


def _shot(window: MainWindow, path: Path) -> Path:
    window.viewer.screenshot(path)
    return path

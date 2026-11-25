"""B2d step 1: the pages of the window, and the panels that follow them (design/B2d.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeViewer, fake_engine
from PySide6.QtWidgets import QLabel
from sample_recipes import ELECTROLYTE, STAMP
from test_molecule_file import cluster_xyz
from test_notebook import RUNS

from ucmd_builder.build import build
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.runs import RunSettings
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("one-window")
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml")
    return write_package(build(recipe), tmp / "p.ucsys", created_utc=STAMP)


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    w.show()
    return w


def finished_run(qtbot, window, package: Path, tmp_path: Path, monkeypatch) -> Path:
    fake_engine(tmp_path, monkeypatch)
    assert window.open_package(package)
    run_dir = window.start_run(RunSettings(length_ps=0.04, frame_every=10, minimize=False))
    qtbot.waitUntil(lambda: not window.is_running(), timeout=60_000)
    return run_dir


def test_a_run_has_a_plots_page_and_a_bar_under_the_3d_view(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    assert window.notebook.page_titles() == ["3D view"]
    assert window.run_panel.playback.isHidden()
    finished_run(qtbot, window, package, tmp_path, monkeypatch)
    assert window.notebook.page_titles() == ["3D view", "Plots"]
    playback = window.run_panel.playback
    assert not playback.isHidden() and playback.parentWidget() is window.view_page
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    assert window.open_path(tmp_path / "c.xyz")  # no run: no plots, one frame: no bar
    assert window.notebook.page_titles() == ["3D view"] and playback.isHidden()


def test_a_frame_behind_another_page_is_drawn_once_when_it_is_seen(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    finished_run(qtbot, window, package, tmp_path, monkeypatch)
    window.notebook.setCurrentWidget(window.plots_page)
    drawn = len(window.viewer.frames)
    for k in (0, 1, 2):
        window.run_panel.slider.setValue(k)
    assert len(window.viewer.frames) == drawn
    window.notebook.setCurrentWidget(window.view_page)
    assert len(window.viewer.frames) == drawn + 1
    want = window.run_panel.trajectory.positions_nm[2]
    assert (window.viewer.frames[-1] == want).all()


def test_side_panels_belong_to_the_3d_view(window, tmp_path: Path) -> None:
    window.ruler_dock.close()  # the user's choice is kept
    (tmp_path / "runs.tsv").write_text("".join(RUNS))
    assert window.open_path(tmp_path / "runs.tsv")
    assert window.properties_dock.isHidden() and window.ruler_dock.isHidden()
    window.notebook.setCurrentWidget(window.view_page)
    assert not window.properties_dock.isHidden() and window.ruler_dock.isHidden()


def test_the_components_panel_goes_with_a_recipe(window, package: Path, tmp_path: Path) -> None:
    assert not window.components_dock.isHidden()  # nothing shown yet: start building here
    assert window.open_package(package)
    assert window.components_dock.isHidden()
    recipe = tmp_path / "small.toml"
    recipe.write_text(ELECTROLYTE.replace("{method}", "insertion"))
    assert window.open_recipe(recipe)
    assert not window.components_dock.isHidden()
    assert window.open_package(package)
    assert not window.components_dock.isHidden()  # the recipe is still open
    window.new_recipe()
    assert not window.components_dock.isHidden()


def test_the_title_names_what_is_shown(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    assert window.open_package(package)
    assert window.windowTitle().startswith("p.ucsys")
    assert "new" not in window.windowTitle().lower()  # no name of an empty recipe
    finished_run(qtbot, window, package, tmp_path, monkeypatch)
    assert window.open_run(window.run_dir)
    assert window.windowTitle().startswith(window.run_panel.outputs.title)
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    window.open_path(tmp_path / "c.xyz")
    assert window.windowTitle().startswith("c · 41 atoms") and "guessed" in window.shown


def test_a_warning_opens_the_log(window, tmp_path: Path) -> None:
    window.log.add("all is well")
    assert window.log_dock.isHidden() and window.statusBar().currentMessage() == "all is well"
    assert not window.open_package(tmp_path / "missing.ucsys")
    assert not window.log_dock.isHidden()


def test_stop_is_a_toolbar_command_of_a_running_run(
    qtbot, window, package: Path, tmp_path: Path, monkeypatch
) -> None:
    fake_engine(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_UCMD_DELAY", "0.02")
    assert window.open_package(package) and not window.act_stop.isEnabled()
    window.start_run(RunSettings(length_ps=5.0, minimize=False))
    assert window.act_stop.isEnabled()
    window.act_stop.trigger()
    qtbot.waitUntil(lambda: not window.is_running(), timeout=30_000)
    assert not window.act_stop.isEnabled()

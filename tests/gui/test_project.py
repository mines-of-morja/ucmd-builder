"""B2d step 3: the project panel, and links from tables to runs (design/B2d.md)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fakes import FakeViewer
from PySide6.QtWidgets import QLabel
from sample_recipes import ELECTROLYTE, STAMP
from test_molecule_file import cluster_xyz

from ucmd_builder.build import build
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.project import find_runs, find_workspace, scan_project, study_of
from ucmd_builder.gui.project_panel import PATH
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe

TABLE = """# ucmd-table 1
# title: Runs of the study
run\tx
zeta_0.65\t0.65
base_s1\t1
elsewhere\t2
../../../secret\t3
"""
THERMO = "# step\ttime_ps\ttemperature_K\n"


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


def make_run(folder: Path, package: Path, title: str, last: int) -> None:
    (folder / "out").mkdir(parents=True)
    system = Path(os.path.relpath(package, folder)).as_posix()
    (folder / "run.toml").write_text(
        f'spec_version = "0.1.0"\nsystem = "{system}"\ntitle = "{title}"\n[run]\nsteps = 100\n'
        '[output]\n[output.thermo]\nfile = "thermo.tsv"\nevery = 50\n'
    )
    rows = "".join(f"{s}\t{s * 0.001}\t300.0\n" for s in range(0, last + 1, 50))
    (folder / "out" / "thermo.tsv").write_text(THERMO + rows)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    study = root / "studies" / "2026-11-24_scan"
    study.mkdir(parents=True)
    (study / "study.toml").write_text('[study]\nquestion = "Which one?"\n')
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=root / "r.toml")
    package = write_package(build(recipe), study / "p.ucsys", created_utc=STAMP)
    make_run(study / "runs" / "base_s1" / "T20C", package, "base at 20 °C", 100)
    make_run(study / "runs" / "zeta_0.65" / "T20C", package, "zeta 0.65 at 20 °C", 50)
    make_run(study / "runs" / "zeta_0.65" / "T40C", package, "zeta 0.65 at 40 °C", 100)
    (study / "tables").mkdir()
    (study / "tables" / "runs.tsv").write_text(TABLE)
    (study / "views").mkdir()
    (study / "views" / "link").symlink_to(study / "runs")
    other = root / "studies" / "2026-11-20_broken"
    other.mkdir()
    (other / "study.toml").write_text("not = [toml")
    (root / "studies" / "notes").mkdir()
    (root / "small.toml").write_text(ELECTROLYTE.replace("{method}", "insertion"))
    (root / "settings.toml").write_text("x = 1\n")  # no recipe
    (root / "c.xyz").write_text(cluster_xyz())
    make_run(root / "studies" / "secret", package, "not in a study's runs", 100)
    return root


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    w.show()
    return w


def test_a_workspace_is_read(workspace: Path) -> None:
    project = scan_project(workspace)
    assert [s.name for s in project.studies] == ["2026-11-24_scan", "2026-11-20_broken"]
    scan, broken = project.studies
    assert scan.question == "Which one?" and len(scan.runs) == 3 and scan.done == 2
    assert [t.name for t in scan.tables] == ["runs.tsv"]
    assert "not readable" in broken.question and broken.runs == ()
    assert {k: [p.name for p in v] for k, v in project.files.items()} == {
        "Recipes": ["small.toml"],
        "Molecules": ["c.xyz"],
    }
    # the same study from its own folder and from the folder of studies
    assert scan_project(workspace / "studies").studies[0].runs == scan.runs
    assert scan_project(scan.path).studies[0].tables == scan.tables


def test_places_are_found(workspace: Path, tmp_path: Path) -> None:
    study = workspace / "studies" / "2026-11-24_scan"
    run = study / "runs" / "base_s1" / "T20C"
    assert study_of(run / "run.toml") == study and study_of(workspace) is None
    assert find_workspace(run) == workspace and find_workspace(tmp_path) is None
    assert [r.run_dir for r in find_runs(study, "base_s1")] == [run]
    assert [r.run_dir for r in find_runs(study, "base_s1/T20C")] == [run]
    assert len(find_runs(study, "zeta_0.65")) == 2


@pytest.mark.parametrize("name", ["", " ", ".", "..", "../../secret", "/", "nothing", "../runs"])
def test_a_name_never_leads_outside_the_runs(workspace: Path, name: str) -> None:
    assert find_runs(workspace / "studies" / "2026-11-24_scan", name) == []


def test_the_panel_opens_runs_tables_and_files(window, workspace: Path) -> None:
    window.set_workspace(workspace)
    panel = window.project
    study = workspace / "studies" / "2026-11-24_scan"
    item = panel.item("study/2026-11-24_scan")
    assert item.text(0) == "11-24 scan" and item.text(1) == "2/3"
    assert item.toolTip(0) == "2026-11-24_scan\nWhich one?"
    states = {item.child(k).text(0): item.child(k).text(1) for k in range(item.childCount())}
    assert states["runs.tsv"] == "table" and states["base_s1/T20C"] == "complete"
    assert states["zeta_0.65/T20C"] in ("running? 50 %", "stopped 50 %")
    panel.tree.itemDoubleClicked.emit(panel.item(f"run/{study / 'runs/base_s1/T20C'}"), 0)
    assert window.shown == "base at 20 °C", window.log.toPlainText()
    panel.tree.itemDoubleClicked.emit(panel.item(f"table/{study / 'tables/runs.tsv'}"), 0)
    assert [p.table.title for p in window.notebook.table_pages()] == ["Runs of the study"]
    panel.tree.itemDoubleClicked.emit(panel.item(f"file/{workspace / 'c.xyz'}"), 0)
    assert window.shown.startswith("c · 41 atoms")
    before = window.log.toPlainText()
    panel.tree.itemDoubleClicked.emit(item, 0)  # a study only unfolds
    assert window.log.toPlainText() == before and item.data(0, PATH) is None


def test_refresh_shows_new_runs_and_keeps_what_is_unfolded(window, workspace: Path) -> None:
    window.set_workspace(workspace)
    panel = window.project
    panel.item("study/2026-11-24_scan").setExpanded(True)
    study = workspace / "studies" / "2026-11-24_scan"
    make_run(study / "runs" / "new" / "T20C", study / "p.ucsys", "new", 0)
    panel.refresh_button.click()
    item = panel.item("study/2026-11-24_scan")
    assert item.text(1) == "2/4" and item.isExpanded()
    assert not panel.item("study/2026-11-20_broken").isExpanded()


def test_the_filter_keeps_the_way_to_what_matches(window, workspace: Path) -> None:
    window.set_workspace(workspace)
    panel = window.project
    panel.filter.setText("scan zeta T40")
    shown = {i.text(0) for i in panel._items() if not i.isHidden()}
    assert shown == {"Studies", "11-24 scan", "zeta_0.65/T40C"}
    panel.filter.setText("")
    assert not any(i.isHidden() for i in panel._items())


def test_a_table_row_opens_its_run(window, workspace: Path) -> None:
    study = workspace / "studies" / "2026-11-24_scan"
    assert window.open_path(study / "tables" / "runs.tsv")
    assert window.project.root == workspace  # found from the file
    page = window.notebook.table_pages()[0]
    assert "Double-click a row" in page.notes.text()
    page.grid.sortItems(1)  # rows are looked up as shown, not as in the file
    rows = {page.grid.item(r, 0).text(): r for r in range(page.grid.rowCount())}
    page.grid.cellDoubleClicked.emit(rows["base_s1"], 1)
    assert window.shown == "base at 20 °C"
    assert not window.run_panel.tables_button.isHidden()
    offered = []
    window.choose_run = lambda folder, runs: offered.append(runs) or runs[1]
    page.grid.cellDoubleClicked.emit(rows["zeta_0.65"], 0)  # two temperatures: the user chooses
    assert len(offered[0]) == 2 and window.shown == "zeta 0.65 at 40 °C"
    for name in ("elsewhere", "../../../secret"):
        page.grid.cellDoubleClicked.emit(rows[name], 0)
        assert f"no run {name!r} in 2026-11-24_scan" in window.log.toPlainText()
    assert window.shown == "zeta 0.65 at 40 °C"


def test_a_run_leads_to_the_tables_of_its_study(window, workspace: Path) -> None:
    study = workspace / "studies" / "2026-11-24_scan"
    assert window.open_path(study / "runs" / "base_s1" / "T20C")
    assert window.notebook.table_pages() == []
    window.run_panel.tables_button.click()
    assert len(window.notebook.table_pages()) == 1
    assert window.open_path(workspace / "c.xyz")
    assert window.run_panel.tables_button.isHidden()


def test_a_table_outside_a_study_says_so(window, tmp_path: Path) -> None:
    (tmp_path / "t.tsv").write_text(TABLE)
    assert window.open_path(tmp_path / "t.tsv")
    window.notebook.table_pages()[0].grid.cellDoubleClicked.emit(0, 0)
    assert "does not belong to a study" in window.log.toPlainText()


def test_a_study_folder_opens_in_the_panel_with_its_tables(window, workspace: Path) -> None:
    assert window.open_path(workspace / "studies" / "2026-11-24_scan")
    assert window.project.item("study/2026-11-24_scan").isExpanded()
    assert len(window.notebook.table_pages()) == 1
    assert "3 runs, 2 complete" in window.log.toPlainText()


def test_a_workspace_that_is_gone_is_said(window, tmp_path: Path) -> None:
    window.set_workspace(tmp_path / "gone")
    assert "does not exist" in window.project.place.text()

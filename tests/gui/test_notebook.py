"""The notebook and result tables (design/B2c.md §4): pages as tabs, what is drawn, bad files."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeViewer
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from test_molecule_file import cluster_xyz

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.notebook import Notebook
from ucmd_builder.gui.table_file import TableError, is_table, read_table

RUNS = """# ucmd-table 1
# title: Sensitivity
# note: probes, not a fit
# plot: x=x y=kappa_S_per_m group=knob
knob\tx\tkappa_S_per_m\tn
base\t1\t0.50\t10
zeta\t0.867\t0.31\t2
zeta\t1.133\tnan\t9
sigma\t0.9\t0.42\t
"""
TRENDS = "# ucmd-table 1\n# title: Trends\nknob\tslope\tmoves\nzeta\t-2.5\tyes\nsigma\t0.1\tno\n"


@pytest.fixture
def tables(tmp_path: Path) -> Path:
    folder = tmp_path / "tables"
    folder.mkdir()
    (folder / "runs.tsv").write_text(RUNS)
    (folder / "trends.tsv").write_text(TRENDS)
    (folder / "thermo.tsv").write_text("# step\ttime_ps\n0\t0.0\n")  # no result table
    return folder


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    return w


def test_the_first_page_is_the_3d_view_and_stays(window, tables: Path) -> None:
    book = window.notebook
    assert book.page_titles() == ["3D view"] and book.widget(0) is window.view_page
    assert window.viewer.parentWidget() is window.view_page
    book.close_page(0)
    book.close_page(1)
    assert book.page_titles() == ["3D view"] and book.count() == 2  # the plots page is hidden
    assert window.open_path(tables)
    book.close_page(2)
    assert book.page_titles() == ["3D view", "Trends"]


def test_every_table_gets_a_page(window, tables: Path) -> None:
    assert window.open_path(tables), window.log.toPlainText()  # the thermo table is left alone
    book = window.notebook
    assert book.page_titles() == ["3D view", "Sensitivity", "Trends"]
    (runs, trends) = book.table_pages()
    assert runs.grid.rowCount() == 4 and runs.notes.text() == "probes, not a fit"
    assert runs.grid.item(1, 0).text() == "zeta" and runs.grid.item(1, 2).text() == "0.31"
    assert trends.view is None and trends.grid.item(0, 2).text() == "yes"


def test_a_missing_value_is_a_dash_and_not_a_point(window, tables: Path) -> None:
    page = window.notebook.add_table(tables / "runs.tsv")
    assert page.grid.item(2, 2).text() == "–" and page.grid.item(3, 3).text() == "–"
    drawn = {s.name(): [(p.x(), p.y()) for p in s.points()] for s in page.series}
    assert drawn == {"base": [(1.0, 0.5)], "zeta": [(0.867, 0.31)], "sigma": [(0.9, 0.42)]}


def test_other_columns_can_be_drawn(window, tables: Path) -> None:
    page = window.notebook.add_table(tables / "runs.tsv")
    page.y_box.setCurrentText("n")
    assert sum(s.count() for s in page.series) == 3  # one row has no n
    assert page.y_axis.titleText() == "n"
    page.group_box.setCurrentIndex(0)
    assert len(page.series) == 1 and not page.view.chart().legend().isVisible()
    texts = [page.x_box.itemText(k) for k in range(page.x_box.count())]
    assert texts == ["x", "kappa_S_per_m", "n"]  # text columns cannot be an axis


def test_numbers_sort_by_value(window, tables: Path) -> None:
    page = window.notebook.add_table(tables / "runs.tsv")
    page.grid.sortItems(3, Qt.SortOrder.AscendingOrder)
    assert [page.grid.item(r, 3).text() for r in range(4)] == ["2", "9", "10", "–"]


def test_the_same_file_replaces_its_page(window, tables: Path) -> None:
    book = window.notebook
    book.add_table(tables / "runs.tsv")
    book.add_table(tables / "trends.tsv")
    (tables / "runs.tsv").write_text(RUNS + "eps\t1.1\t0.6\t1\n")
    book.table_pages()[0].reload.click()
    assert book.page_titles() == ["3D view", "Sensitivity", "Trends"]
    assert book.currentWidget() is book.table_pages()[0]
    assert book.table_pages()[0].grid.rowCount() == 5


def test_a_failed_reload_keeps_the_page(window, tables: Path) -> None:
    page = window.notebook.add_table(tables / "runs.tsv")
    (tables / "runs.tsv").write_text("half a fi")
    page.reload.click()
    assert window.notebook.table_pages() == [page] and "cannot reload" in page.notes.text()
    assert page.grid.rowCount() == 4


def test_opening_a_structure_returns_to_the_3d_view(window, tables: Path, tmp_path: Path) -> None:
    window.open_path(tables / "trends.tsv")
    assert window.notebook.currentWidget() is window.notebook.table_pages()[0]
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    assert window.open_path(tmp_path / "c.xyz")
    assert (
        window.notebook.currentWidget() is window.view_page
        and len(window.notebook.table_pages()) == 1
    )


@pytest.mark.parametrize(
    "text, message",
    [
        ("a\tb\n1\t2\n", "does not start with"),
        ("# ucmd-table 1\n", "no header line"),
        ("# ucmd-table 1\na\tb\n1\n", "line 3: 1 cells, 2 columns"),
        ("# ucmd-table 1\na\ta\n1\t2\n", "same name"),
        ("# ucmd-table 1\n# plot: x=a y=c\na\tb\n1\t2\n", "'c', which is no column"),
        ("# ucmd-table 1\n# plot: x=a y=b\na\tb\n1\ttext\n", "'b' is not numeric"),
        ("# ucmd-table 1\n# plot: x=a\na\tb\n1\t2\n", "plot needs x and y"),
    ],
)
def test_a_bad_table_is_refused(window, tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "bad.tsv"
    path.write_text(text)
    with pytest.raises(TableError, match=message):
        read_table(path)
    assert not window.open_path(path)
    assert message in window.log.toPlainText() and not window.notebook.table_pages()


def test_is_table(tables: Path, tmp_path: Path) -> None:
    assert is_table(tables / "runs.tsv") and not is_table(tables / "thermo.tsv")
    assert not is_table(tmp_path / "missing.tsv")


def test_a_notebook_without_a_3d_view(qtbot, tables: Path) -> None:
    book = Notebook()
    qtbot.addWidget(book)
    book.add_table(tables / "runs.tsv")
    book.show_fixed()
    assert book.count() == 1


def test_several_paths_from_the_command_line(window, tables: Path, tmp_path: Path) -> None:
    for k in (1, 2):
        (tmp_path / f"n{k}.xyz").write_text(cluster_xyz(copies=k))
    window.open_paths([tables / "runs.tsv", tmp_path / "n1.xyz", tmp_path / "n2.xyz"])
    assert window.run_panel.structure_box.count() == 2
    assert [p.table.title for p in window.notebook.table_pages()] == ["Sensitivity"]

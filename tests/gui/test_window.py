"""The GUI preview without a display (design/B2a.md §9): document, panels, build, export."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fakes import FakeViewer
from sample_recipes import ELECTROLYTE

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.components import AddComponentDialog
from ucmd_builder.gui.composition import CompositionDialog
from ucmd_builder.gui.document import RecipeDocument
from ucmd_builder.gui.scene import read_package
from ucmd_builder.recipe import RecipeError, load_recipe, to_toml

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def small_recipe(tmp_path: Path) -> Path:
    path = tmp_path / "small.toml"
    path.write_text(ELECTROLYTE.replace("{method}", "insertion"))
    return path


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=FakeViewer())
    w.ask = lambda _question: True  # no modal dialogs without a user
    qtbot.addWidget(w)
    return w


def test_open_edit_undo_redo_save(qtbot, small_recipe: Path, tmp_path: Path) -> None:
    doc = RecipeDocument()
    doc.open(small_recipe)
    original = doc.recipe
    ec = original.component("EC")
    doc.edit(replace(original, components=_with(original, replace(ec, count=12))), "count")
    assert doc.recipe.component("EC").count == 12 and doc.modified
    doc.undo_stack.undo()
    assert doc.recipe.component("EC").count == 10
    doc.undo_stack.redo()
    saved = doc.save(tmp_path / "saved.toml")
    assert not doc.modified
    assert load_recipe(saved).component("EC").count == 12
    assert to_toml(load_recipe(saved)) == to_toml(doc.recipe)


def test_an_invalid_edit_is_refused(qtbot, small_recipe: Path) -> None:
    doc = RecipeDocument()
    doc.open(small_recipe)
    before = to_toml(doc.recipe)
    ec = doc.recipe.component("EC")
    with pytest.raises(RecipeError):
        doc.edit(replace(doc.recipe, components=_with(doc.recipe, replace(ec, count=-3))), "bad")
    assert to_toml(doc.recipe) == before and not doc.modified


def test_the_table_edits_counts_and_refuses_bad_values(window, small_recipe: Path) -> None:
    window.open_recipe(small_recipe)
    table = window.components.table
    assert table.rowCount() == 4
    row = [c.name for c in window.document.recipe.components].index("EMC")
    table.item(row, 2).setText("25")
    assert window.document.recipe.component("EMC").count == 25
    table.item(row, 2).setText("-1")
    assert window.document.recipe.component("EMC").count == 25
    assert "refused" in window.log.toPlainText()
    assert table.item(row, 2).text() == "25"  # the table shows the document again


def test_add_component_from_the_library(window) -> None:
    dialog = AddComponentDialog(window.document.recipe, window)
    dialog.set_box.setCurrentText("opls-aa-carbonates")
    dialog.choose("EC")
    dialog.count.setValue(8)
    window.components.apply(dialog.result_recipe, "add EC")
    recipe = window.document.recipe
    assert recipe.forcefield == {"source": "library", "set": "opls-aa-carbonates"}
    assert recipe.component("EC").count == 8
    assert window.components.table.rowCount() == 1


def test_composition_dialog_reproduces_lehnert_table_s1(window) -> None:
    window.open_recipe(EXAMPLES / "lp57-1M.toml")
    dialog = CompositionDialog(window.document.recipe, window)
    # the example recipe already holds Lehnert's composition; the preview is compose()
    assert "47 Li, 47 PF6, 168 EC, 332 EMC" in dialog.preview.text()
    assert "placeholder" in dialog.preview.text()
    dialog.set_weight("EC", 3)  # weights are normalized: 3 : 7 is the same composition
    dialog.set_weight("EMC", 7)
    assert "47 Li, 47 PF6, 168 EC, 332 EMC" in dialog.preview.text()
    window.components.apply(dialog.result_recipe, "composition")
    assert window.document.recipe.composition.solvent == pytest.approx({"EC": 0.3, "EMC": 0.7})


def test_build_export_and_reopen(qtbot, window, small_recipe: Path, tmp_path: Path) -> None:
    window.open_recipe(small_recipe)
    window.start_build()
    qtbot.waitUntil(lambda: not window.is_building(), timeout=120_000)
    assert window.built is not None, window.log.toPlainText()
    scene = window.viewer.scenes[-1]
    assert scene.n_atoms == window.built.atom_count
    out = window.export_package(tmp_path / "small.ucsys")
    assert out is not None and (tmp_path / "small.recipe.toml").is_file()
    assert window.open_package(out)
    again = window.viewer.scenes[-1]
    assert again.elements == scene.elements
    assert np.allclose(again.positions_nm, scene.positions_nm)
    assert read_package(out).n_atoms == scene.n_atoms


def test_cancel_stops_a_build(qtbot, window, tmp_path: Path) -> None:
    text = (
        (EXAMPLES / "lp57-1M.toml")
        .read_text()
        .replace('method = "packmol"', 'method = "insertion"')
    )
    path = tmp_path / "big.toml"
    path.write_text(text)
    window.open_recipe(path)
    window.start_build()
    qtbot.waitUntil(lambda: "packing" in window.log.toPlainText().lower(), timeout=60_000)
    window.cancel_build()
    qtbot.waitUntil(lambda: not window.is_building(), timeout=60_000)
    assert window.built is None
    assert "build failed: cancelled" in window.log.toPlainText()


def test_picking_and_selection_fill_the_properties(qtbot, window, small_recipe: Path) -> None:
    window.open_recipe(small_recipe)
    window.start_build()
    qtbot.waitUntil(lambda: not window.is_building(), timeout=120_000)
    window.viewer.pick(0)  # what a right-click on atom 0 does
    assert "Li" in window.properties.toPlainText()
    window.components.table.selectRow(2)  # EC
    assert len(window.viewer.highlights[-1]) == 10 * 10  # ten EC molecules of ten atoms


def _with(recipe, component):
    return tuple(component if c.name == component.name else c for c in recipe.components)


def test_numbers_are_read_in_the_c_locale(qtbot) -> None:
    """Qt sets the system locale; with a decimal comma, RDKit read "0.896716" as 0."""
    import locale

    from rdkit import Chem

    try:
        locale.setlocale(locale.LC_NUMERIC, "de_DE.UTF-8")  # as Qt does on a German system
    except locale.Error:
        pytest.skip("no comma locale installed to test with")
    window = MainWindow(viewer=FakeViewer())
    qtbot.addWidget(window)
    atom = Chem.MolFromXYZBlock("1\n\nC 0.896716 0.0 0.0\n").GetConformer().GetAtomPosition(0)
    assert atom.x == pytest.approx(0.896716)

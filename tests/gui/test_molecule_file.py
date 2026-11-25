"""Molecule files in the GUI (design/B2c.md §4): read, guess bonds, play, refuse bad files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fakes import FakeViewer

from ucmd_builder.forcefield.library import load_template
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.molecule_file import MoleculeFileError, read_structures


def cluster_xyz(step: object = 1, shift: float = 0.0, copies: int = 4) -> str:
    """Li+ and EC molecules of RDKit geometry, well apart from each other; Å."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("C1COC(=O)O1"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    xyz = mol.GetConformer().GetPositions()
    xyz -= xyz.mean(axis=0)
    places = np.array([[6, 0, 0], [-6, 0, 0], [0, 6, 0], [0, -6, 0]])[:copies]
    rows = [f"Li {shift:.4f} 0.0 0.0"]
    for place in places:
        for a, x in zip(mol.GetAtoms(), xyz + place, strict=True):
            rows.append(f"{a.GetSymbol()} {x[0]:.6f} {x[1]:.6f} {x[2]:.6f}")
    comment = f"Properties=species:S:1:pos:R:3 step={step}"
    return "\n".join([str(len(rows)), comment, *rows]) + "\n"


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=FakeViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    return w


def test_a_cluster_has_four_solvents_and_an_unbonded_ion(tmp_path: Path) -> None:
    (tmp_path / "c.xyz").write_text(cluster_xyz())
    (s,) = read_structures(tmp_path / "c.xyz")
    ec = load_template("opls-aa-carbonates", "EC")
    assert s.scene.n_atoms == 1 + 4 * len(ec.graph.elements) == 41
    assert len(s.scene.bonds) == 4 * len(ec.graph.bonds)
    assert s.scene.elements[0] == "Li" and not (s.scene.bonds == 0).any()
    assert s.scene.molecule.max() == 4  # Li+ and four solvents
    assert s.scene.atom(0).molecule_type == "Li" and s.scene.atom(5).molecule_type == "C3H4O3"


def test_a_lithium_next_to_oxygen_is_not_bonded(tmp_path: Path) -> None:
    (tmp_path / "c.xyz").write_text("3\n\nLi 0 0 0\nO 1.9 0 0\nC 3.1 0 0\n")
    (s,) = read_structures(tmp_path / "c.xyz")
    assert s.scene.bonds.tolist() == [[1, 2]]


def test_a_path_plays_frame_by_frame(window, tmp_path: Path) -> None:
    n = 172
    path = tmp_path / "path.xyz"
    path.write_text("".join(cluster_xyz(k + 1, 0.001 * k) for k in range(n)))
    assert window.open_path(path), window.log.toPlainText()
    panel = window.run_panel
    assert len(panel.trajectory) == n and panel.slider.value() == n - 1
    for k in (0, 57, n - 1):
        panel.slider.setValue(k)
        panel.show_frame(k)
        assert window.viewer.frames[-1][0, 0] == pytest.approx(0.0001 * k)  # nm
        assert f"frame {k + 1}/{n}, step {k + 1}" in panel.frame_label.text()
    assert not panel.structure_box.isVisible() and panel.plots.isHidden()
    assert not window.act_run.isEnabled() and not window.act_export.isEnabled()
    assert "guessed" in panel.status.text()


def test_files_with_other_atoms_are_a_list(window, tmp_path: Path) -> None:
    for k in (1, 3):
        (tmp_path / f"n{k}.xyz").write_text(cluster_xyz(copies=k))
    assert window.open_molecules([tmp_path / "n1.xyz", tmp_path / "n3.xyz"])
    panel = window.run_panel
    assert panel.structure_box.count() == 2 and not panel.structure_box.isHidden()
    assert window.scene.n_atoms == 11
    panel.structure_box.setCurrentIndex(1)
    assert window.scene.n_atoms == 31 and len(window.viewer.frames[-1]) == 31
    # one file with both: the same list
    (tmp_path / "both.xyz").write_text(cluster_xyz(copies=1) + cluster_xyz(copies=3))
    assert [s.name for s in read_structures(tmp_path / "both.xyz")] == ["both #1", "both #2"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("3\n\nO 0 0 0\nH 1 0 0\n", "3 atoms announced, 2 lines"),
        ("1\n\nO 0 zero 0\n", "line 3: not a coordinate"),
        ("1\n\nO 0 nan 0\n", "line 3: a coordinate is not finite"),
        ("1\n\nO 0 0\n", "line 3: an element and x y z"),
        ("1\n\nQq 0 0 0\n", "'Qq' is not an element"),
        ("1\n\nO 0 0 0\nleft over\n", "line 4: expected a number of atoms"),
        ("", "empty"),
        ("1\nProperties=pos:R:3\n0 0 0\n", "no species and pos"),
    ],
)
def test_a_bad_file_is_refused(window, tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "bad.xyz"
    path.write_text(text)
    with pytest.raises(MoleculeFileError, match=message):
        read_structures(path)
    assert not window.open_path(path)
    assert message in window.log.toPlainText()
    assert not window.viewer.scenes


def test_a_lattice_is_drawn_and_wraps(tmp_path: Path) -> None:
    (tmp_path / "l.xyz").write_text('1\nLattice="10 0 0 0 10 0 0 0 10"\nO 12 0 0\n')
    (s,) = read_structures(tmp_path / "l.xyz")
    assert np.allclose(s.scene.cell_nm, np.eye(3))
    assert np.allclose(s.scene.display_positions()[0], [0.2, 0, 0])


def test_extra_columns_and_column_order(tmp_path: Path) -> None:
    (tmp_path / "e.xyz").write_text(
        "1\nProperties=molecule:I:1:pos:R:3:species:S:1 step=final\n4 1.0 2.0 3.0 O\n"
    )
    (s,) = read_structures(tmp_path / "e.xyz")
    assert s.scene.elements == ("O",) and np.allclose(s.scene.positions_nm, [[0.1, 0.2, 0.3]])
    assert s.trajectory.labels == ("step final",)


def test_a_package_closes_the_molecule_file(window, tmp_path: Path) -> None:
    """Its frames must not move the atoms of another scene."""
    from sample_recipes import ELECTROLYTE, STAMP

    from ucmd_builder.build import build
    from ucmd_builder.package import write_package
    from ucmd_builder.recipe import parse_recipe

    (tmp_path / "c.xyz").write_text(cluster_xyz(1) + cluster_xyz(2, 0.1))
    assert window.open_path(tmp_path / "c.xyz")
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp_path / "r.toml")
    package = write_package(build(recipe), tmp_path / "p.ucsys", created_utc=STAMP)
    assert window.open_package(package)
    panel = window.run_panel
    assert not panel.structures and panel.trajectory is None and not panel.slider.isEnabled()
    assert not panel.plots.isHidden()

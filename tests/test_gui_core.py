"""The GUI's Qt-free core (design/B2a.md): scenes from system.json, the thermo reader."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from sample_recipes import ELECTROLYTE, STAMP

from ucmd_builder.build import build
from ucmd_builder.gui.elements import color, radius_nm
from ucmd_builder.gui.scene import Scene, read_package
from ucmd_builder.gui.thermo import ThermoTail
from ucmd_builder.package import system_document, write_package
from ucmd_builder.recipe import parse_recipe


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("scene")
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml")
    return build(recipe)


def test_scene_of_a_built_system(built) -> None:
    doc = system_document(built, STAMP)
    scene = Scene.from_document(doc, built.positions_nm)
    assert scene.n_atoms == built.atom_count
    expected_bonds = sum(
        len(doc["molecule_types"][c["molecule_type"]].get("bonds", [])) * c["count"]
        for c in doc["components"]
    )
    assert scene.bonds.shape == (expected_bonds, 2)
    # every bond stays inside one molecule
    assert np.all(scene.molecule[scene.bonds[:, 0]] == scene.molecule[scene.bonds[:, 1]])
    li = scene.atom(0)
    assert (li.element, li.molecule_type, li.molecule) == ("Li", "Li", 0)
    assert li.charge_e == pytest.approx(0.75)  # ζ = 0.75 in the recipe
    assert li.sigma_nm == pytest.approx(0.287) and li.type is not None


def test_display_keeps_molecules_whole_and_in_the_cell(built) -> None:
    doc = system_document(built, STAMP)
    scene = Scene.from_document(doc, built.positions_nm)
    length = scene.cell_nm[0, 0]
    moved = scene.positions_nm.copy()
    moved[scene.molecule == 5] += np.array([length, -2 * length, 0.0])  # one molecule leaves
    shown = scene.display_positions(moved)
    centres = (
        np.stack([np.bincount(scene.molecule, weights=shown[:, k]) for k in range(3)], axis=1)
        / np.bincount(scene.molecule)[:, None]
    )
    assert np.all((centres >= 0.0) & (centres < length))
    i, j = scene.bonds.T
    assert np.allclose(
        np.linalg.norm(shown[i] - shown[j], axis=1), np.linalg.norm(moved[i] - moved[j], axis=1)
    )


def test_a_package_reads_back_as_the_same_scene(built, tmp_path: Path) -> None:
    out = write_package(built, tmp_path / "p.ucsys", created_utc=STAMP)
    disk = read_package(out)
    memory = Scene.from_document(system_document(built, STAMP), built.positions_nm)
    assert disk.elements == memory.elements
    assert np.array_equal(disk.positions_nm, memory.positions_nm)
    assert np.array_equal(disk.bonds, memory.bonds)


def test_element_colours_and_radii() -> None:
    assert color("O") == (255, 13, 13)
    assert color("Xx") == (255, 20, 147)  # unknown elements stand out
    assert 0.03 < radius_nm("H") < radius_nm("C") < radius_nm("P") < 0.07


def test_thermo_tail_reads_only_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "thermo.tsv"
    tail = ThermoTail(path)
    assert tail.poll() == 0  # no file yet
    header = "# step\ttime_ps\ttemperature_K\tconserved_energy_kjmol\n"
    path.write_text(header + "0\t0.0\t300.0\t-5.0\n10\t0.01\t299.0\t-5.1\n20\t0.0")
    assert tail.poll() == 2
    with path.open("a") as fh:
        fh.write("2\t298.0\t-5.2\n30\t0.03\t297.0\t-5.3\n")
    assert tail.poll() == 2
    assert list(tail.column("temperature_K")) == [300.0, 299.0, 298.0, 297.0]
    assert len(tail.column("pressure_bar")) == 0  # not in this table
    path.write_text(header + "0\t0.0\t310.0\t-6.0\n")  # truncated, e.g. by a restart
    tail.poll()
    assert list(tail.column("temperature_K")) == [310.0]

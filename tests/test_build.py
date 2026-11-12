"""End to end: recipe → validated package (B1.1, B1.3, B1.5, B1.8) and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import requires_packmol

from ucmd_builder.build import BuildError, build
from ucmd_builder.cli import main
from ucmd_builder.constants import BOLTZMANN_KJMOL_PER_K
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe

ARGON = """
format = "ucmd-build"
version = "0.1"
name = "argon-small"
seed = 3

[forcefield]
source = "library"
set = "argon-rahman1964"

[[component]]
name = "Ar"
molecule = { library = "Ar" }
count = 108

[box]
density_g_cm3 = { value = 1.374, source = "A. Rahman, Phys. Rev. 136, A405 (1964)" }

[packing]
method = "insertion"
tolerance_nm = 0.25

[velocities]
temperature_K = 94.4
"""

ELECTROLYTE = """
format = "ucmd-build"
version = "0.1"
name = "lipf6-small"
seed = 4

[forcefield]
source = "library"
set = "opls-aa-carbonates"

[[component]]
name = "Li"
molecule = { smiles = "[Li+]" }
count = 3
charge_scale = 0.75

[[component]]
name = "PF6"
molecule = { smiles = "F[P-](F)(F)(F)(F)F" }
count = 3
charge_scale = 0.75

[[component]]
name = "EC"
molecule = { library = "EC" }
count = 10

[[component]]
name = "EMC"
molecule = { smiles = "CCOC(=O)OC" }
count = 20

[box]
length_nm = 2.6

[packing]
method = "{method}"
tolerance_nm = 0.2
"""

STAMP = "2026-11-12T00:00:00Z"


def build_and_write(text: str, tmp_path: Path, name: str = "out.ucsys") -> tuple[Path, dict]:
    recipe = parse_recipe(text, path=tmp_path / "build.toml")
    built = build(recipe)
    out = write_package(built, tmp_path / name, created_utc=STAMP)
    return out, json.loads((out / "system.json").read_text())


def test_argon_package_is_valid_and_velocities_are_exact(tmp_path: Path) -> None:
    out, doc = build_and_write(ARGON, tmp_path)
    assert doc["atom_count"] == 108 and doc["components"] == [{"molecule_type": "Ar", "count": 108}]
    assert doc["forcefield"]["atom_types"] == {
        "Ar.Ar": {"sigma_nm": 0.34, "epsilon_kjmol": 0.997735514178}
    }
    v = np.load(out / "velocities.npy")
    m = 39.948
    assert np.allclose((m * v).sum(axis=0), 0.0, atol=1e-10)
    t = m * (v * v).sum() / ((3 * 108 - 3) * BOLTZMANN_KJMOL_PER_K)
    assert t == pytest.approx(94.4, rel=1e-12)
    # 1.374 g/cm3 with 108 atoms of 39.948 amu
    assert doc["cell_nm"][0][0] == pytest.approx(
        (108 * m / (1.374 * 602.214076)) ** (1 / 3), rel=1e-12
    )
    prov = doc["provenance"]
    assert prov["created_by"].startswith("ucmd-builder ") and prov["created_utc"] == STAMP
    assert prov["seed"] == 3 and "Rahman" in json.dumps(prov["sources"])


def test_the_same_recipe_gives_a_byte_identical_package(tmp_path: Path) -> None:
    a, _ = build_and_write(ARGON, tmp_path, "a.ucsys")
    b, _ = build_and_write(ARGON, tmp_path, "b.ucsys")
    for name in ("system.json", "positions.npy", "velocities.npy"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_electrolyte_with_insertion(tmp_path: Path) -> None:
    _, doc = build_and_write(ELECTROLYTE.replace("{method}", "insertion"), tmp_path)
    types = doc["forcefield"]["atom_types"]
    assert "EC.opls_800" in types and "EMC.opls_800" in types  # prefixed: no collision
    assert types["EC.opls_801"] != types["EMC.opls_801"]  # carbonyl O in EC, ester O in EMC
    charges = {
        name: sum(a["charge_e"] for a in mt["atoms"]) for name, mt in doc["molecule_types"].items()
    }
    assert charges["Li"] == pytest.approx(0.75) and charges["PF6"] == pytest.approx(-0.75)
    assert doc["molecule_types"]["Li"]["formal_charge_e"] == 1
    assert doc["molecule_types"]["PF6"]["formal_charge_e"] == -1
    prov = doc["provenance"]
    assert [t["component"] for t in prov["transforms"]] == ["Li", "PF6"]
    assert {s["component"]: s["source_id"] for s in prov["forcefield_sources"]}["EMC"].startswith(
        "library:opls-aa-carbonates/EMC"
    )
    assert any("LigParGen" in s["citation"] or "Dodda" in s["citation"] for s in prov["sources"])
    assert any("Jensen" in s["citation"] for s in prov["sources"])


@requires_packmol
def test_electrolyte_with_packmol(tmp_path: Path) -> None:
    _, doc = build_and_write(ELECTROLYTE.replace("{method}", "packmol"), tmp_path)
    assert doc["provenance"]["packing"]["program"].startswith("Packmol ")
    assert doc["atom_count"] == 3 * 1 + 3 * 7 + 10 * 10 + 20 * 15


def test_mixing_conventions_is_refused(tmp_path: Path) -> None:
    text = ARGON.replace("count = 108", "count = 10").replace(
        "[box]",
        '[[component]]\nname = "water"\nmolecule = { library = "water", set = "water-spcfw" }\n'
        'forcefield = { source = "library", set = "water-spcfw" }\ncount = 5\n\n[box]',
    )
    with pytest.raises(BuildError, match="conventions differ"):
        build(parse_recipe(text, path=tmp_path / "b.toml"))


def test_existing_output_is_protected(tmp_path: Path) -> None:
    out, _ = build_and_write(ARGON, tmp_path)
    built = build(parse_recipe(ARGON, path=tmp_path / "b.toml"))
    with pytest.raises(BuildError, match="exists"):
        write_package(built, out)
    write_package(built, out, overwrite=True, created_utc=STAMP)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.txt").write_text("keep me")
    with pytest.raises(BuildError, match="not a system package"):
        write_package(built, tmp_path / "other", overwrite=True)


@pytest.mark.slow
@requires_packmol
def test_lehnert_1M_box(tmp_path: Path) -> None:
    """The full 1 M LiPF6 EC:EMC 3:7 box of Lehnert 2025 Table S1 (7036 atoms; ~20 s)."""
    text = (Path(__file__).parents[1] / "examples" / "lp57-1M.toml").read_text()
    _, doc = build_and_write(text, tmp_path)
    assert doc["atom_count"] == 7036
    assert [c["count"] for c in doc["components"]] == [47, 47, 168, 332]


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recipe = tmp_path / "argon.toml"
    recipe.write_text(ARGON)
    assert main(["build", str(recipe), "-o", str(tmp_path / "cli.ucsys"), "-q"]) == 0
    assert "108 atoms" in capsys.readouterr().out
    assert main(["check", str(tmp_path / "cli.ucsys")]) == 0
    assert main(["build", str(recipe), "-o", str(tmp_path / "cli.ucsys"), "-q"]) == 1  # exists
    assert "exists" in capsys.readouterr().err
    assert main(["sources"]) == 0 and "gromacs-top" in capsys.readouterr().out
    assert main(["library"]) == 0 and "PF6" in capsys.readouterr().out


def test_xyz_export(tmp_path: Path) -> None:
    out, doc = build_and_write(ARGON, tmp_path)
    assert main(["xyz", str(out), "-o", str(tmp_path / "a.xyz")]) == 0
    lines = (tmp_path / "a.xyz").read_text().splitlines()
    assert lines[0] == "108" and len(lines) == 110
    L = doc["cell_nm"][0][0] * 10
    assert f'Lattice="{L:.8f} 0.00000000 0.00000000' in lines[1] and 'pbc="T T T"' in lines[1]
    x = np.load(out / "positions.npy")
    first = lines[2].split()
    assert first[0] == "Ar" and float(first[1]) == pytest.approx(x[0, 0] * 10, abs=1e-6)


def test_a_charged_system_is_a_recipe_error(tmp_path: Path) -> None:
    text = ELECTROLYTE.replace("{method}", "insertion").replace(
        'name = "PF6"\nmolecule = { smiles = "F[P-](F)(F)(F)(F)F" }\ncount = 3',
        'name = "PF6"\nmolecule = { smiles = "F[P-](F)(F)(F)(F)F" }\ncount = 2',
    )
    assert "count = 2" in text
    with pytest.raises(BuildError, match=r"net charge of \+0\.75 e"):
        build(parse_recipe(text, path=tmp_path / "b.toml"))

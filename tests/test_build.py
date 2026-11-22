"""End to end: recipe → validated package (B1.1, B1.3, B1.5, B1.8) and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from helpers import requires_packmol
from sample_recipes import ARGON, ELECTROLYTE, STAMP

from ucmd_builder.build import BuildError, build
from ucmd_builder.cli import main
from ucmd_builder.constants import BOLTZMANN_KJMOL_PER_K
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe


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


# Spec 0.1.2 (ADR 0015): LJ pair overrides. Test numbers, not a force field.
OVERRIDE = """
[[pair_override]]
types = ["{li}", "EC.opls_801"]
sigma_nm = 0.21
epsilon_kjmol = 0.9
citation = "test value"
"""


def test_pair_override_is_written_validated_and_recorded(tmp_path: Path) -> None:
    text = ELECTROLYTE.replace("{method}", "insertion")
    _, plain = build_and_write(text, tmp_path, "plain.ucsys")
    assert plain["spec_version"] == "0.1.0" and "pair_overrides" not in plain["forcefield"]
    (li,) = [t for t in plain["forcefield"]["atom_types"] if t.startswith("Li.")]
    _, doc = build_and_write(text + OVERRIDE.format(li=li), tmp_path, "override.ucsys")
    assert doc["spec_version"] == "0.1.2"
    assert doc["forcefield"]["pair_overrides"] == [
        {"types": [li, "EC.opls_801"], "sigma_nm": 0.21, "epsilon_kjmol": 0.9,
         "description": "test value"}
    ]  # fmt: skip
    assert doc["forcefield"]["atom_types"] == plain["forcefield"]["atom_types"]
    assert doc["provenance"]["transforms"][-1] == {
        "kind": "pair_override",
        "types": [li, "EC.opls_801"],
        "sigma_nm": 0.21,
        "epsilon_kjmol": 0.9,
        "citation": "test value",
    }
    assert {"what": f"LJ pair override {li} – EC.opls_801", "citation": "test value"} in doc[
        "provenance"
    ]["sources"]


def test_pair_override_with_an_unknown_type_fails_before_packing(tmp_path: Path) -> None:
    text = ELECTROLYTE.replace("{method}", "insertion") + OVERRIDE.format(li="Li.nope")
    with pytest.raises(BuildError, match=r"unknown atom type 'Li\.nope'"):
        build(parse_recipe(text, path=tmp_path / "build.toml"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ('citation = "test value"', "citation"),  # removed: required
        ('types = ["A", "B", "C"]', "two atom-type names"),
        ("epsilon_kjmol = -0.1", "≥ 0"),
    ],
)
def test_pair_override_recipe_errors(change: str, message: str) -> None:
    from ucmd_builder.recipe import RecipeError

    block = OVERRIDE.format(li="Li.Li")
    if change.startswith("citation"):
        block = block.replace(change + "\n", "")
    else:
        key = change.split(" = ")[0]
        line = next(x for x in block.splitlines() if x.startswith(key + " = "))
        block = block.replace(line, change)
    with pytest.raises(RecipeError, match=message):
        parse_recipe(ELECTROLYTE.replace("{method}", "insertion") + block)


def test_pair_override_survives_the_recipe_round_trip() -> None:
    from ucmd_builder.recipe import to_toml

    text = ELECTROLYTE.replace("{method}", "insertion") + OVERRIDE.format(li="Li.Li")
    text += OVERRIDE.format(li="Li.Li").replace("EC.opls_801", "EMC.opls_802")
    recipe = parse_recipe(text)
    assert len(recipe.pair_overrides) == 2
    assert parse_recipe(to_toml(recipe)) == recipe
    with pytest.raises(ValueError, match="repeats"):
        parse_recipe(
            text
            + OVERRIDE.format(li="Li.Li").replace(
                '"Li.Li", "EC.opls_801"', '"EC.opls_801", "Li.Li"'
            )
        )

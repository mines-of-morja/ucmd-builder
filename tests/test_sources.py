"""B1.3: force-field sources — library tables, the GROMACS importer, the registry, the test kit.

The expected numbers below are copied from the sources named in each template (ADR 0009):
LigParGen 3.0.1 output for EC and EMC, Jensen & Jorgensen (2006) for Li+, the CL&P database
il.ff for PF6-, Wu/Tepper/Voth (2006) for SPC/Fw and Rahman (1964) for argon.
"""

from __future__ import annotations

import json
import math
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from ucmd_builder.forcefield import registry
from ucmd_builder.forcefield.api import SourceError
from ucmd_builder.forcefield.gromacs import GromacsTopSource, read_gro
from ucmd_builder.forcefield.library import LibrarySource, list_sets, load_set, load_template
from ucmd_builder.forcefield.testkit import check_parameters, check_source
from ucmd_builder.molecule import from_smiles

LIB = LibrarySource()
ALL_TEMPLATES = [(s, t) for s in list_sets() for t in load_set(s).template_names()]


def by_atoms(items):
    return {i.atoms: i for i in items}


# ---------------------------------------------------------------------------------------------
# Source tables


def test_ec_values_as_generated_by_ligpargen() -> None:
    p = load_template("opls-aa-carbonates", "EC").parameters
    names = [a.name for a in p.atoms]
    assert names == ["C00", "O01", "O02", "C03", "C04", "O05", "H06", "H07", "H08", "H09"]
    assert p.atoms[0].charge_e == 0.578745 and p.atoms[1].charge_e == -0.39258
    assert abs(p.net_charge_e) < 1e-6
    assert p.lj["opls_801"].sigma_nm == 0.296 and p.lj["opls_801"].epsilon_kjmol == 0.87864
    bond = by_atoms(p.bonds)[(1, 0)]
    assert bond.params == {"r0_nm": 0.1229, "k_kjmol_nm2": 476976.0}
    angle = by_atoms(p.angles)[(2, 0, 1)]
    assert angle.params == {"theta0_deg": 123.4, "k_kjmol_rad2": 694.544}
    rb = by_atoms(p.dihedrals)[(3, 2, 0, 1)]
    assert [rb.params[f"c{i}_kjmol"] for i in range(6)] == [21.43882, 0.0, -21.43882, 0.0, 0.0, 0.0]
    (imp,) = p.impropers
    assert imp.atoms == (1, 0, 2, 5) and imp.params == {
        "k_kjmol": 43.932,
        "n": 2,
        "phi0_deg": 180.0,
    }
    assert p.conventions.combination_rule == "geometric"
    assert (p.conventions.scale_14_lj, p.conventions.scale_14_coulomb) == (0.5, 0.5)


def test_emc_values_as_generated_by_ligpargen() -> None:
    p = load_template("opls-aa-carbonates", "EMC").parameters
    charges = {a.name: a.charge_e for a in p.atoms}
    assert charges["C00"] == 0.588387 and charges["O02"] == -0.530852  # carbonyl C and O
    assert abs(p.net_charge_e) < 1e-6
    assert len(p.atoms) == 15 and len(p.bonds) == 14 and len(p.impropers) == 1


def test_li_jensen_jorgensen_2006_and_both_distributed_copies() -> None:
    p = load_template("opls-aa-carbonates", "Li").parameters
    lj = p.lj["Li"]
    # 2.87 Å and 0.0005 kcal/mol (JJ06), which equal BOSS 5.1's opls_406 in GROMACS units.
    assert lj.sigma_nm == 0.287
    assert lj.epsilon_kjmol == 0.002092
    assert p.atoms[0].charge_e == 1.0 and p.atoms[0].mass_amu == 6.941


def test_pf6_clandp_values_and_cis_only_angles() -> None:
    p = load_template("opls-aa-carbonates", "PF6").parameters
    assert p.lj["P"].sigma_nm == 0.374 and p.lj["P"].epsilon_kjmol == 0.8368
    assert p.lj["FP"].sigma_nm == 0.312 and p.lj["FP"].epsilon_kjmol == 0.2552
    assert [a.charge_e for a in p.atoms] == [1.34] + [-0.39] * 6
    assert p.net_charge_e == pytest.approx(-1.0, abs=1e-12)
    assert {b.params["r0_nm"] for b in p.bonds} == {0.1606}
    assert {b.params["k_kjmol_nm2"] for b in p.bonds} == {310000.0}  # 3100 kJ mol-1 Å-2, ½k form
    assert len(p.angles) == 12 and {a.params["theta0_deg"] for a in p.angles} == {90.0}
    assert {a.params["k_kjmol_rad2"] for a in p.angles} == {1165.0}
    xyz = p.coordinates_nm
    for a in p.angles:
        i, j, k = a.atoms
        u, v = xyz[i] - xyz[j], xyz[k] - xyz[j]
        assert math.degrees(
            math.acos(u @ v / np.linalg.norm(u) / np.linalg.norm(v))
        ) == pytest.approx(90.0)


def test_spcfw_and_argon_equal_the_spec_fixtures(spec: Path) -> None:
    water = json.loads((spec / "fixtures/valid/water-2/system.json").read_text())
    ref = (
        water["molecule_types"]["SPCFW"]
        if "SPCFW" in water["molecule_types"]
        else next(iter(water["molecule_types"].values()))
    )
    p = load_template("water-spcfw", "water").parameters
    assert [a.charge_e for a in p.atoms] == [a["charge_e"] for a in ref["atoms"]]
    assert [a.mass_amu for a in p.atoms] == [a["mass_amu"] for a in ref["atoms"]]
    for ours, theirs in zip(p.bonds, ref["bonds"], strict=True):
        assert ours.params == pytest.approx(theirs["params"], rel=1e-12)
    assert p.angles[0].params == pytest.approx(ref["angles"][0]["params"], rel=1e-12)
    types = water["forcefield"]["atom_types"]
    assert p.lj["OW"].sigma_nm == pytest.approx(types["OW"]["sigma_nm"], rel=1e-12)
    assert p.lj["OW"].epsilon_kjmol == pytest.approx(types["OW"]["epsilon_kjmol"], rel=1e-12)
    argon = json.loads((spec / "fixtures/valid/argon-864/system.json").read_text())
    ar = load_template("argon-rahman1964", "Ar").parameters
    assert ar.lj["Ar"].sigma_nm == pytest.approx(
        argon["forcefield"]["atom_types"]["Ar"]["sigma_nm"], rel=1e-12
    )
    assert ar.lj["Ar"].epsilon_kjmol == pytest.approx(
        argon["forcefield"]["atom_types"]["Ar"]["epsilon_kjmol"], rel=1e-12
    )


@pytest.mark.parametrize(("set_name", "template"), ALL_TEMPLATES)
def test_every_template_passes_the_source_test_kit(set_name: str, template: str) -> None:
    t = load_template(set_name, template)
    (report,) = check_source(LIB, [t.graph], {"set": set_name}, base_dir=Path("."))
    assert report.ok, str(report)
    assert t.parameters.citations, "every template cites its source"


@pytest.mark.parametrize(("set_name", "template"), ALL_TEMPLATES)
def test_smiles_route_gives_the_template_parameters(set_name: str, template: str) -> None:
    t = load_template(set_name, template)
    mol = from_smiles(t.graph.smiles, template, seed=2)
    p = LIB.parameterize(mol, {"set": set_name}, base_dir=Path("."))
    assert check_parameters(mol, p).ok
    assert sorted((a.type, a.charge_e) for a in p.atoms) == sorted(
        (a.type, a.charge_e) for a in t.parameters.atoms
    )
    assert p.net_charge_e == pytest.approx(t.parameters.net_charge_e, abs=1e-9)
    assert len(p.angles) == len(t.parameters.angles) and len(p.dihedrals) == len(
        t.parameters.dihedrals
    )
    # The template geometry travels with the parameters (cis-only PF6 angles stay at 90°).
    xyz = p.coordinates_nm
    for a in p.angles:
        i, j, k = a.atoms
        u, v = xyz[i] - xyz[j], xyz[k] - xyz[j]
        theta = math.degrees(
            math.acos(np.clip(u @ v / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))
        )
        assert abs(theta - a.params["theta0_deg"]) < 25.0, (template, a.atoms, theta)


def test_library_errors_are_actionable() -> None:
    with pytest.raises(SourceError, match="option `set` is required"):
        LIB.parameterize(from_smiles("O", "w"), {}, base_dir=Path("."))
    with pytest.raises(SourceError, match="no template"):
        LIB.parameterize(
            from_smiles("CCO", "ethanol"), {"set": "opls-aa-carbonates"}, base_dir=Path(".")
        )
    with pytest.raises(SourceError, match="unknown option"):
        LIB.parameterize(
            from_smiles("O", "w"), {"set": "water-spcfw", "colour": 1}, base_dir=Path(".")
        )


# ---------------------------------------------------------------------------------------------
# gromacs-top


def ligpargen_style_itp(set_name: str, template: str) -> str:
    """A topology in LigParGen's layout, written from a library template (spec units = GROMACS)."""
    t = load_template(set_name, template)
    p = t.parameters
    lines = [
        ";---------------------------------------------",
        "[ defaults ]",
        "; nbfunc        comb-rule       gen-pairs       fudgeLJ fudgeQQ",
        "    1               3              yes            0.5     0.5",
        "",
        "[ atomtypes ]",
    ]
    for name in dict.fromkeys(a.type for a in p.atoms):
        atom = next(a for a in p.atoms if a.type == name)
        lj = p.lj[name]
        lines.append(
            f"  {name}    X   {atom.mass_amu:.4f}     0.000    A    "
            f"{lj.sigma_nm:.5E}   {lj.epsilon_kjmol:.5E}"
        )
    lines += [
        "",
        "[ moleculetype ]",
        "; Name               nrexcl",
        "  LIG                  3",
        "",
        "[ atoms ]",
    ]
    for k, a in enumerate(p.atoms, start=1):
        lines.append(
            f"  {k:5d}   {a.type}      1    LIG   {a.name}      1   "
            f"{a.charge_e:.6f}    {a.mass_amu:.4f}"
        )
    lines += ["", "[ bonds ]"]
    for b in p.bonds:
        i, j = (x + 1 for x in b.atoms)
        lines.append(f"  {i} {j} 1  {b.params['r0_nm']:.4f}  {b.params['k_kjmol_nm2']:.3f}")
    lines += ["", "[angles]"]  # LigParGen writes this header without spaces
    for a in p.angles:
        i, j, k = (x + 1 for x in a.atoms)
        lines.append(
            f"  {i} {j} {k} 1  {a.params['theta0_deg']:.3f}  {a.params['k_kjmol_rad2']:.3f}"
        )
    lines += ["[ dihedrals ]", ";  PROPER DIHEDRAL ANGLES"]
    for d in p.dihedrals:
        i, j, k, m = (x + 1 for x in d.atoms)
        c = "  ".join(f"{d.params[f'c{n}_kjmol']:.5f}" for n in range(6))
        lines.append(f"  {i} {j} {k} {m} 3  {c}")
    lines += ["[ dihedrals ]", ";IMPROPER DIHEDRAL ANGLES"]
    for d in p.impropers:
        i, j, k, m = (x + 1 for x in d.atoms)
        lines.append(
            f"  {i} {j} {k} {m} 4  {d.params['phi0_deg']:.3f}  "
            f"{d.params['k_kjmol']:.3f}  {d.params['n']}"
        )
    lines += ["[ pairs ]"]
    for i, j in t.graph.one_four_pairs():
        lines.append(f"  {i + 1} {j + 1} 1")
    return "\n".join(lines) + "\n"


CITE = [{"what": "test topology", "citation": "written by the test from the library template"}]


@pytest.mark.parametrize("template", ["EC", "EMC", "PF6", "Li"])
def test_gromacs_import_equals_the_library(tmp_path: Path, template: str) -> None:
    (tmp_path / "m.itp").write_text(ligpargen_style_itp("opls-aa-carbonates", template))
    mol = from_smiles(load_template("opls-aa-carbonates", template).graph.smiles, template)
    lib = LIB.parameterize(mol, {"set": "opls-aa-carbonates"}, base_dir=tmp_path)
    gmx = GromacsTopSource().parameterize(
        mol, {"file": "m.itp", "citations": CITE}, base_dir=tmp_path
    )
    assert [(a.type, a.charge_e, a.mass_amu) for a in gmx.atoms] == [
        (a.type, a.charge_e, a.mass_amu) for a in lib.atoms
    ]
    for name in ("bonds", "angles", "dihedrals", "impropers"):
        ours = {i.atoms: dict(i.params) for i in getattr(gmx, name)}
        theirs = {i.atoms: dict(i.params) for i in getattr(lib, name)}
        assert ours.keys() == theirs.keys(), name
        for key in ours:
            assert ours[key] == pytest.approx(theirs[key], rel=1e-5), (name, key)
    assert gmx.conventions.same_physics(lib.conventions)
    assert check_parameters(mol, gmx).ok


def write(tmp_path: Path, text: str, name: str = "t.itp") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return path


WATER = """\
    [ defaults ]
    1 2 yes 0.5 0.5
    [ atomtypes ]
    ; name  at.num  mass     charge  ptype  sigma      epsilon
    OW      8       15.9994  0.0     A      0.3165492  0.6502994552
    HW      1       1.008    0.0     A      0.0        0.0
    [ moleculetype ]
    SOL 3
    [ atoms ]
    1 OW 1 SOL OW  1 -0.82 15.9994
    2 HW 1 SOL HW1 1  0.41  1.008
    3 HW 1 SOL HW2 1  0.41  1.008
    #ifdef POSRES
    [ position_restraints ]
    1 1 1000 1000 1000
    #endif
    [ bonds ]
    1 2 1 0.1012 443153.3808
    1 3 1 0.1012 443153.3808
    [ angles ]
    2 1 3 1 113.24 317.5656
"""


def test_gromacs_water_with_atomic_numbers_and_posres_block(tmp_path: Path) -> None:
    write(tmp_path, WATER)
    mol = from_smiles("O", "water")
    p = GromacsTopSource().parameterize(
        mol, {"file": "t.itp", "citations": CITE}, base_dir=tmp_path
    )
    ref = LIB.parameterize(mol, {"set": "water-spcfw"}, base_dir=tmp_path)
    assert p.conventions.combination_rule == "lorentz-berthelot"
    assert [a.charge_e for a in p.atoms] == [a.charge_e for a in ref.atoms]
    assert p.bonds[0].params == pytest.approx(ref.bonds[0].params, rel=1e-12)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("1 2 yes 0.5 0.5", "1 1 yes 0.5 0.5"), "comb-rule 1"),
        (("1 2 yes 0.5 0.5", "1 2 no 0.5 0.5"), "gen-pairs"),
        (("    [ angles ]", "    [ pairtypes ]\n    OW HW 1 0.1 0.1\n    [ angles ]"), "pairtypes"),
        (("    [ angles ]", "    [ exclusions ]\n    1 2\n    [ angles ]"), "exclusions"),
        (("    #ifdef POSRES", "    #ifdef FLEXIBLE"), "FLEXIBLE|preprocessor|#endif"),
        (("SOL 3", "SOL 2"), "nrexcl"),
        (("    1 2 1 0.1012 443153.3808\n", "    1 2 1\n"), "explicit harmonic bonds"),
    ],
)
def test_gromacs_refuses_what_spec_cannot_express(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    assert change[0] in WATER
    write(tmp_path, WATER.replace(*change))
    with pytest.raises(SourceError, match=message):
        GromacsTopSource().parameterize(
            from_smiles("O", "w"), {"file": "t.itp", "citations": CITE}, base_dir=tmp_path
        )


def test_gromacs_needs_citations_and_checks_pairs(tmp_path: Path) -> None:
    write(tmp_path, WATER)
    with pytest.raises(SourceError, match="citations"):
        GromacsTopSource().parameterize(from_smiles("O", "w"), {"file": "t.itp"}, base_dir=tmp_path)
    text = ligpargen_style_itp("opls-aa-carbonates", "EC")
    lines = text.splitlines()
    lines.pop()  # drop one 1-4 pair
    (tmp_path / "ec.itp").write_text("\n".join(lines) + "\n")
    with pytest.raises(SourceError, match=r"\[ pairs \] differs"):
        GromacsTopSource().parameterize(
            from_smiles("O=C1OCCO1", "EC"), {"file": "ec.itp", "citations": CITE}, base_dir=tmp_path
        )


def test_gromacs_include_and_conventions_preset(tmp_path: Path) -> None:
    body = ligpargen_style_itp("opls-aa-carbonates", "Li").split("[ atomtypes ]", 1)[1]
    write(tmp_path, "[ atomtypes ]" + body, "li.itp")
    write(tmp_path, '#include "li.itp"\n', "top.top")
    mol = from_smiles("[Li+]", "Li")
    with pytest.raises(SourceError, match="no \\[ defaults \\]"):
        GromacsTopSource().parameterize(
            mol, {"file": "top.top", "citations": CITE}, base_dir=tmp_path
        )
    p = GromacsTopSource().parameterize(
        mol, {"file": "top.top", "citations": CITE, "conventions": "opls"}, base_dir=tmp_path
    )
    assert p.lj[p.atoms[0].type].sigma_nm == pytest.approx(0.287)


@pytest.mark.parametrize("decimals", [3, 6])
def test_read_gro_with_any_precision(tmp_path: Path, decimals: int) -> None:
    w = decimals + 5
    rows = [(1.5, -0.25, 3.125), (-12.5, 0.0, 0.5)]
    text = ["title", " 2"]
    for k, (x, y, z) in enumerate(rows, start=1):
        text.append(
            f"{1:5d}{'LIG':<5}{'A' + str(k):>5}{k:5d}"
            + "".join(f"{v:{w}.{decimals}f}" for v in (x, y, z))
        )
    text.append("   1.0 1.0 1.0")
    (tmp_path / "c.gro").write_text("\n".join(text) + "\n")
    assert np.allclose(read_gro(tmp_path / "c.gro"), rows)


# ---------------------------------------------------------------------------------------------
# Registry: the plug-in path


def test_builtin_sources_are_registered_as_plugins() -> None:
    found = registry.available()
    assert {"library", "gromacs-top"} <= set(found)
    assert found["library"].distribution == "ucmd-builder"
    assert isinstance(registry.get("library"), LibrarySource)
    with pytest.raises(SourceError, match="unknown force-field source"):
        registry.get("no-such-source")


PLUGIN = """
from ucmd_builder.forcefield.api import AtomParams, Citation, Conventions, LJ, Parameterized

class ArgonOnly:
    name = "argon-only"
    version = "0.1"

    def parameterize(self, molecule, options, *, base_dir):
        return Parameterized(
            atoms=tuple(AtomParams(e, "Ar", 0.0, 39.948) for e in molecule.elements),
            lj={"Ar": LJ(0.34, 0.99)}, bonds=(), angles=(), dihedrals=(), impropers=(),
            constraints=(), conventions=Conventions("geometric", 1.0, 1.0, "test"),
            citations=(Citation("test", "a test plug-in"),), source_id="argon-only",
        )
"""


def test_a_third_party_source_plugs_in_without_builder_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "plugin_mod.py").write_text(PLUGIN)
    dist = tmp_path / "ucmd_test_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: ucmd-test-plugin\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text(
        "[ucmd_builder.forcefield_sources]\nargon-only = plugin_mod:ArgonOnly\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert registry.available()["argon-only"].distribution == "ucmd-test-plugin"
    source = registry.get("argon-only")
    (report,) = check_source(source, [from_smiles("[Ar]", "Ar")], {}, base_dir=tmp_path)
    assert report.ok, str(report)
    sys.modules.pop("plugin_mod", None)


def test_duplicate_source_names_are_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "evil-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: evil\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text(
        "[ucmd_builder.forcefield_sources]\nlibrary = evil:Other\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(SourceError, match="registered twice"):
        registry.available()

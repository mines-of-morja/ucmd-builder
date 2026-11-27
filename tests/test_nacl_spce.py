"""The nacl-jc-spce set (A1.7 target 2): values as printed, cross-checked against a second source.

Primary: Berendsen, Grigera, Straatsma (1987) for SPC/E; Joung & Cheatham (2008), Table 5 (SPC/E
column), for Na+ and Cl-. Second source: Mester & Panagiotopoulos (2015), SI Tables I and II, which
print the same models as sigma and epsilon (kJ/mol, nm) to 4-7 digits.
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import numpy as np
import pytest
from helpers import requires_packmol

from ucmd_builder.forcefield.api import SourceError
from ucmd_builder.forcefield.library import _lj, load_template
from ucmd_builder.forcefield.units import Units


def test_ions_equal_mester_si_table_1() -> None:
    # Mester & Panagiotopoulos 2015, SI Table I, column "JC (SPC/E)": epsilon kJ/mol, sigma nm
    for name, eps, sigma in (("Na", 1.475463, 0.21595), ("Cl", 0.053493, 0.48305)):
        lj = load_template("nacl-jc-spce", name).parameters.lj[name]
        assert lj.sigma_nm == pytest.approx(sigma, abs=0.5e-5)
        # Joung's kcal/mol times 4.184 is 6.6e-6 (Na) and 1.1e-5 (Cl) below Mester's print; their
        # conversion factor was about 4.18403-4.18405. Both print the same model.
        assert lj.epsilon_kjmol == pytest.approx(eps, rel=2e-5)


def test_spce_equals_mester_si_table_2_and_berendsen_geometry() -> None:
    p = load_template("nacl-jc-spce", "water").parameters
    assert p.lj["OW"].sigma_nm == pytest.approx(0.3166, abs=0.5e-4)
    assert p.lj["OW"].epsilon_kjmol == pytest.approx(0.6502, abs=0.5e-4)
    assert (p.lj["HW"].sigma_nm, p.lj["HW"].epsilon_kjmol) == (0.0, 0.0)
    assert [a.charge_e for a in p.atoms] == [-0.8476, 0.4238, 0.4238]
    assert sum(a.charge_e for a in p.atoms) == pytest.approx(0.0, abs=1e-15)
    lengths = {c.atoms: c.length_nm for c in p.constraints}
    assert lengths[(0, 1)] == lengths[(0, 2)] == pytest.approx(0.1, rel=1e-12)
    tetrahedral = math.acos(-1.0 / 3.0)
    assert lengths[(1, 2)] == pytest.approx(0.2 * math.sin(tetrahedral / 2), rel=1e-12)
    # the bonds only carry connectivity: no energy, length equal to the constraint
    assert [b.params["k_kjmol_nm2"] for b in p.bonds] == [0.0, 0.0]
    xyz = p.coordinates_nm
    for (i, j), d in lengths.items():
        assert np.linalg.norm(xyz[i] - xyz[j]) == pytest.approx(d, rel=1e-12)


def test_lennard_jones_forms() -> None:
    kj = Units.parse({"energy": "kJ/mol", "length": "nm"}, "t")
    # A and B from sigma and epsilon: 4 eps sigma^6 = A^6, 4 eps sigma^12 = B^12
    sigma, eps = 0.3, 0.5
    a = (4 * eps * sigma**6) ** (1 / 6)
    b = (4 * eps * sigma**12) ** (1 / 12)
    lj = _lj({"A": a, "B": b}, kj, "t")
    assert (lj.sigma_nm, lj.epsilon_kjmol) == (
        pytest.approx(sigma, rel=1e-11),
        pytest.approx(eps, rel=1e-11),
    )
    # Rmin/2 in Å and epsilon in kcal/mol
    lj = _lj(
        {"rmin_half": 1.0, "epsilon": 1.0},
        Units.parse({"energy": "kcal/mol", "length": "angstrom"}, "t"),
        "t",
    )
    assert lj.sigma_nm == pytest.approx(0.2 / 2 ** (1 / 6), rel=1e-11)
    assert lj.epsilon_kjmol == pytest.approx(4.184, rel=1e-12)
    # A in (kcal/mol)^(1/6) Å converts with both units
    lj = _lj({"A": 10 * a / 4.184 ** (1 / 6), "B": 10 * b / 4.184 ** (1 / 12)},
             Units.parse({"energy": "kcal/mol", "length": "angstrom"}, "t"), "t")  # fmt: skip
    assert (lj.sigma_nm, lj.epsilon_kjmol) == (
        pytest.approx(sigma, rel=1e-11),
        pytest.approx(eps, rel=1e-11),
    )
    for bad in (
        {"sigma": 1.0},
        {"A": 1.0, "epsilon": 1.0},
        {"rmin_half": 1.0, "sigma": 1.0, "epsilon": 1.0},
    ):
        with pytest.raises(SourceError, match="Lennard-Jones keys"):
            _lj(bad, kj, "t")
    with pytest.raises(SourceError, match="must be > 0"):
        _lj({"A": 0.0, "B": 1.0}, kj, "t")


NACL = textwrap.dedent(
    """\
    format = "ucmd-build"
    version = "0.1"
    name = "nacl-test"
    seed = 7

    [forcefield]
    source = "library"
    set = "nacl-jc-spce"

    [[component]]
    name = "Na"
    molecule = { library = "Na" }
    count = 2

    [[component]]
    name = "Cl"
    molecule = { library = "Cl" }
    count = 2

    [[component]]
    name = "water"
    molecule = { library = "water" }
    count = 60

    [box]
    density_g_cm3 = { value = 1.0, source = "test" }

    [packing]
    method = "packmol"
    tolerance_nm = 0.2
    """
)


@requires_packmol
def test_a_small_nacl_box_builds_and_validates(tmp_path: Path) -> None:
    from sample_recipes import STAMP

    from ucmd_builder.build import build
    from ucmd_builder.package import write_package
    from ucmd_builder.recipe import parse_recipe

    out = write_package(
        build(parse_recipe(NACL, path=tmp_path / "r.toml")), tmp_path / "n.ucsys", created_utc=STAMP
    )
    import json

    doc = json.loads((out / "system.json").read_text())
    assert doc["atom_count"] == 2 + 2 + 3 * 60
    water = doc["molecule_types"]["water"]
    assert len(water["constraints"]) == 3
    assert doc["forcefield"]["combination_rule"] == "lorentz-berthelot"
    types = doc["molecule_types"]
    charge = sum(
        a["charge_e"] * c["count"]
        for c in doc["components"]
        for a in types[c["molecule_type"]]["atoms"]
    )
    assert charge == pytest.approx(0.0, abs=1e-12)
    cites = json.dumps(doc["provenance"]["sources"])
    assert "Joung" in cites and "Berendsen" in cites

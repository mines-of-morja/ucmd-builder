"""B1.1 recipes and B1.4 the composition calculator."""

from __future__ import annotations

import pytest

from ucmd_builder.composition import box_length_nm, compose, largest_remainder, mole_fractions
from ucmd_builder.constants import G_PER_CM3_IN_AMU_PER_NM3, MOL_PER_L_IN_PER_NM3
from ucmd_builder.recipe import Composition, Density, RecipeError, parse_recipe, to_toml

# Masses of the parameterized molecules (sums of the template atom masses, ADR 0009).
MASSES = {"Li": 6.941, "PF6": 30.974 + 6 * 18.998, "EC": 88.062, "EMC": 104.105}

LP57 = """
format = "ucmd-build"
version = "0.1"
name = "lp57"
seed = 1

[forcefield]
source = "library"
set = "opls-aa-carbonates"

[composition]
salt = ["Li", "PF6"]
solvent = { EC = 0.3, EMC = 0.7 }
molarity_mol_L = 1.0
solvent_molecules = 500
density_g_cm3 = { value = 1.2, source = "placeholder (PLAN.md B1.4)" }

[[component]]
name = "Li"
molecule = { smiles = "[Li+]" }
charge_scale = 0.75

[[component]]
name = "PF6"
molecule = { smiles = "F[P-](F)(F)(F)(F)F" }
charge_scale = 0.75

[[component]]
name = "EC"
molecule = { library = "EC" }

[[component]]
name = "EMC"
molecule = { library = "EMC" }
"""


def test_recipe_parses_and_round_trips() -> None:
    r = parse_recipe(LP57)
    assert [c.name for c in r.components] == ["Li", "PF6", "EC", "EMC"]
    assert r.composition is not None and r.composition.density.is_placeholder
    again = parse_recipe(to_toml(r))
    assert again == r
    assert parse_recipe(to_toml(again)) == again


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('format = "ucmd-build"', 'format = "other"', "format"),
        ("seed = 1", "seed = -1", "seed"),
        ("seed = 1", "seed = 1\ncolour = 2", "unknown key"),
        (
            'molecule = { library = "EC" }',
            'molecule = { library = "EC", smiles = "C" }',
            "exactly one",
        ),
        ("charge_scale = 0.75", "charge_scale = 1.5", r"\(0, 1\]"),
        ("EMC = 0.7", "EMC = 0.6", "sum to 1"),
        (
            'density_g_cm3 = { value = 1.2, source = "placeholder (PLAN.md B1.4)" }',
            "",
            "needs density",
        ),
        ('name = "EC"\nmolecule', 'name = "EC"\ncount = 3\nmolecule', "remove `count`"),
    ],
)
def test_recipe_errors_name_the_problem(old: str, new: str, message: str) -> None:
    assert old in LP57
    with pytest.raises(RecipeError, match=message):
        parse_recipe(LP57.replace(old, new, 1))


def test_largest_remainder_keeps_the_total() -> None:
    assert largest_remainder(500, {"a": 1, "b": 1, "c": 1}) in (
        {"a": 167, "b": 167, "c": 166},
        {"a": 167, "b": 166, "c": 167},
        {"a": 166, "b": 167, "c": 167},
    )
    assert sum(largest_remainder(1001, {"x": 0.3, "y": 0.7}).values()) == 1001


def composition(**kw: object) -> Composition:
    base = {
        "salt": ("Li", "PF6"),
        "solvent": {"EC": 0.3, "EMC": 0.7},
        "basis": "mass",
        "molarity_mol_L": 1.0,
        "solvent_molecules": 500,
        "density": Density(1.2, "placeholder (PLAN.md B1.4)"),
    }
    base.update(kw)
    return Composition(**base)  # type: ignore[arg-type]


def test_lehnert_table_s1_solvent_counts() -> None:
    # Lehnert 2025 SI Table S1: 168 EC + 332 EMC (500 solvent molecules; 0.1 M doubles them).
    r = compose(composition(), MASSES)
    assert (r.counts["EC"], r.counts["EMC"]) == (168, 332)
    r = compose(composition(solvent_molecules=1000, molarity_mol_L=0.1), MASSES)
    assert (r.counts["EC"], r.counts["EMC"]) == (336, 664)


def test_molarity_ion_pairs_and_box_from_density() -> None:
    # With the PLAN.md B1.4 placeholder density 1.2 g/cm3: 47 ion pairs, as in Lehnert's 1 M row.
    r = compose(composition(), MASSES)
    assert r.counts["Li"] == r.counts["PF6"] == 47
    total = 47 * (MASSES["Li"] + MASSES["PF6"]) + 168 * MASSES["EC"] + 332 * MASSES["EMC"]
    assert r.total_mass_amu == pytest.approx(total)
    # Independent hand calculation of the box and the achieved molarity.
    volume = total / (1.2 * G_PER_CM3_IN_AMU_PER_NM3)
    assert r.box_length_nm == pytest.approx(volume ** (1 / 3), rel=1e-12)
    assert r.molarity_mol_L == pytest.approx(47 / (volume * MOL_PER_L_IN_PER_NM3), rel=1e-12)
    assert r.molarity_mol_L == pytest.approx(1.0, abs=0.01)
    assert r.solvent_fractions["EC"] == pytest.approx(0.3, abs=1e-3)


def test_size_by_ion_pairs_and_molality() -> None:
    r = compose(
        composition(ion_pairs=50, solvent_molecules=None, molarity_mol_L=None, molality_mol_kg=1.0),
        MASSES,
    )
    solvent_mass = r.counts["EC"] * MASSES["EC"] + r.counts["EMC"] * MASSES["EMC"]
    assert r.counts["Li"] == 50
    assert 50 / (solvent_mass / 1000) == pytest.approx(1.0, rel=2e-3)
    assert r.molality_mol_kg == pytest.approx(50 / (solvent_mass / 1000))


def test_volume_fractions_with_pure_solvent_densities() -> None:
    # Lehnert SI Table S2 (manufacturers' densities): EC 1.321, EMC 1.006 g/ml.
    c = composition(basis="volume", solvent_densities_g_cm3={"EC": 1.321, "EMC": 1.006})
    x = mole_fractions(c, MASSES)
    n_ec, n_emc = 0.3 * 1.321 / MASSES["EC"], 0.7 * 1.006 / MASSES["EMC"]
    assert x["EC"] == pytest.approx(n_ec / (n_ec + n_emc))
    r = compose(c, MASSES)
    assert r.solvent_fractions["EC"] == pytest.approx(0.3, abs=2e-3)


def test_box_length_helper() -> None:
    assert box_length_nm(G_PER_CM3_IN_AMU_PER_NM3 * 8.0, 1.0) == pytest.approx(2.0)

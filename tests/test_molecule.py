"""B1.2: molecule graphs and readers; constants (ADR 0001)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from ucmd_builder import constants
from ucmd_builder.forcefield.matching import automorphisms, find_mapping
from ucmd_builder.molecule import MoleculeError, MoleculeGraph, from_file, from_smiles


def test_derived_constants_match_spec_units_table() -> None:
    # spec/units.md §2, "Derived values, for checking implementations"
    assert math.isclose(constants.BOLTZMANN_KJMOL_PER_K, 0.00831446261815324, rel_tol=1e-15)
    assert constants.G_PER_CM3_IN_AMU_PER_NM3 == pytest.approx(602.214076, rel=1e-15)
    assert constants.MOL_PER_L_IN_PER_NM3 == pytest.approx(0.602214076, rel=1e-15)


@pytest.mark.parametrize(
    ("smiles", "formula", "z", "n_bonds", "n_angles", "n_dihedrals", "n_14"),
    [
        # Counted by hand from the structures; EC is a five-membered ring.
        ("O=C1OCCO1", "C3H4O3", 0, 10, 17, 19, 14),
        ("CCOC(=O)OC", "C4H8O3", 0, 14, 23, 19, 19),
        ("F[P-](F)(F)(F)(F)F", "F6P", -1, 6, 15, 0, 0),
        ("[Li+]", "Li", 1, 0, 0, 0, 0),
        ("O", "H2O", 0, 2, 1, 0, 0),
    ],
)
def test_graphs_from_smiles(
    smiles: str, formula: str, z: int, n_bonds: int, n_angles: int, n_dihedrals: int, n_14: int
) -> None:
    g = from_smiles(smiles, "m", seed=1)
    assert g.formula() == formula
    assert g.formal_charge == z
    assert len(g.bonds) == n_bonds
    assert len(g.angles()) == n_angles
    assert len(g.proper_dihedrals()) == n_dihedrals
    assert len(g.one_four_pairs()) == n_14
    assert g.coordinates_nm is not None and g.coordinates_nm.shape == (g.n_atoms, 3)


def test_embedding_is_deterministic_per_seed() -> None:
    a = from_smiles("CCOC(=O)OC", "EMC", seed=5)
    b = from_smiles("CCOC(=O)OC", "EMC", seed=5)
    assert np.array_equal(a.coordinates_nm, b.coordinates_nm)


def test_ring_pairs_count_by_shortest_path() -> None:
    # In EC, C(=O) and the CH2 carbons are 2 bonds apart one way and 3 the other: excluded.
    ec = from_smiles("O=C1OCCO1", "EC")
    carbonyl_c = 1
    ring_c = [i for i, e in enumerate(ec.elements) if e == "C" and i != carbonyl_c]
    d = ec.distances()
    assert all(d[carbonyl_c, j] == 2 for j in ring_c)
    assert all((carbonyl_c, j) not in ec.one_four_pairs() for j in ring_c)


def test_reordering_and_validation() -> None:
    g = MoleculeGraph.make("w", ["O", "H", "H"], [(1, 0), (0, 2)])
    assert g.bonds == ((0, 1), (0, 2))
    r = g.with_order([2, 0, 1])
    assert r.elements == ("H", "O", "H") and r.bonds == ((0, 1), (1, 2))
    with pytest.raises(MoleculeError):
        MoleculeGraph.make("bad", ["O", "H"], [(0, 1), (1, 0)])
    with pytest.raises(MoleculeError):
        MoleculeGraph("bad", ("O",), ((0, 1),), (0,))


def test_files_round_trip(tmp_path: Path) -> None:
    from rdkit import Chem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCOC(=O)OC"))
    from rdkit.Chem import AllChem

    AllChem.EmbedMolecule(mol, randomSeed=3)
    Chem.MolToMolFile(mol, str(tmp_path / "emc.mol"))
    Chem.MolToXYZFile(mol, str(tmp_path / "emc.xyz"))
    Chem.MolToPDBFile(mol, str(tmp_path / "emc.pdb"))
    ref = from_file(tmp_path / "emc.mol")
    for name in ("emc.xyz", "emc.pdb"):
        other = from_file(tmp_path / name)
        assert other.elements == ref.elements
        assert other.bonds == ref.bonds, name
        assert np.allclose(other.coordinates_nm, ref.coordinates_nm, atol=1e-4)
    with pytest.raises(MoleculeError):
        from_file(tmp_path / "x.cif")


def test_isomorphism_maps_atoms_and_respects_elements() -> None:
    a = MoleculeGraph.make("a", ["O", "H", "H"], [(0, 1), (0, 2)])
    b = MoleculeGraph.make("b", ["H", "O", "H"], [(0, 1), (1, 2)])
    m = find_mapping(a.elements, a.bonds, b.elements, b.bonds)
    assert m is not None and m[0] == 1
    c = MoleculeGraph.make("c", ["S", "H", "H"], [(0, 1), (0, 2)])
    assert find_mapping(a.elements, a.bonds, c.elements, c.bonds) is None
    assert len(automorphisms(a.elements, a.bonds)) == 2

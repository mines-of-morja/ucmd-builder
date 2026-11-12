"""Graph isomorphism between element-labelled bond graphs (design/B1.md §4.4).

Templates and imported topologies are mapped onto the recipe's molecule by matching
elements and connectivity. Bond orders and formal charges play no role in the match.
RDKit's substructure search does the work: both graphs become RDKit molecules with
single bonds and no charges, and with equal atom and bond counts a substructure match is
an isomorphism.
"""

from __future__ import annotations

from collections.abc import Sequence


def _graph_mol(elements: Sequence[str], bonds: Sequence[tuple[int, int]]) -> object:
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    rw = Chem.RWMol()
    for element in elements:
        atom = Chem.Atom(table.GetAtomicNumber(element))
        atom.SetNoImplicit(True)
        rw.AddAtom(atom)
    for i, j in bonds:
        rw.AddBond(int(i), int(j), Chem.BondType.SINGLE)
    mol = rw.GetMol()
    mol.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(mol)
    return mol


def isomorphisms(
    query_elements: Sequence[str],
    query_bonds: Sequence[tuple[int, int]],
    target_elements: Sequence[str],
    target_bonds: Sequence[tuple[int, int]],
    max_matches: int = 1,
) -> list[tuple[int, ...]]:
    """Maps m with m[q] = target index of query atom q; empty if the graphs differ.

    The result is deterministic for given inputs (RDKit's search order).
    """
    if len(query_elements) != len(target_elements) or len(query_bonds) != len(target_bonds):
        return []
    if sorted(query_elements) != sorted(target_elements):
        return []
    if len(query_elements) == 1:
        return [(0,)]
    query = _graph_mol(query_elements, query_bonds)
    target = _graph_mol(target_elements, target_bonds)
    matches = target.GetSubstructMatches(  # type: ignore[attr-defined]
        query, uniquify=False, useChirality=False, maxMatches=max_matches
    )
    return [tuple(int(i) for i in m) for m in matches]


def find_mapping(
    query_elements: Sequence[str],
    query_bonds: Sequence[tuple[int, int]],
    target_elements: Sequence[str],
    target_bonds: Sequence[tuple[int, int]],
) -> tuple[int, ...] | None:
    """The first isomorphism query → target, or None."""
    found = isomorphisms(query_elements, query_bonds, target_elements, target_bonds, 1)
    return found[0] if found else None


def automorphisms(
    elements: Sequence[str], bonds: Sequence[tuple[int, int]], limit: int = 100_000
) -> list[tuple[int, ...]]:
    """All symmetries of a graph, up to `limit`."""
    return isomorphisms(elements, bonds, elements, bonds, limit)

"""Molecule graphs: elements, bonds and formal charges in canonical atom order (PLAN.md B1.2).

A `MoleculeGraph` is what the builder knows about a molecule before parameterization: its
elements in canonical order (the order of the input), its bonds, its formal charges and,
optionally, a geometry. Readers turn SMILES and structure files into graphs via RDKit.
Angles, proper dihedrals and 1-4 pairs follow from the bond graph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .constants import ANGSTROM_IN_NM


class MoleculeError(ValueError):
    """A molecule could not be read or is inconsistent."""


@dataclass(frozen=True, eq=False)
class MoleculeGraph:
    """Elements, bonds and formal charges of one molecule, in canonical atom order.

    `bonds` holds index pairs with i < j, sorted and without duplicates. `coordinates_nm`
    is an optional (n, 3) geometry in nm.
    """

    name: str
    elements: tuple[str, ...]
    bonds: tuple[tuple[int, int], ...]
    formal_charges: tuple[int, ...]
    coordinates_nm: np.ndarray | None = field(default=None, repr=False)
    smiles: str | None = None

    def __post_init__(self) -> None:
        n = len(self.elements)
        if n == 0:
            raise MoleculeError(f"{self.name}: a molecule needs at least one atom")
        if len(self.formal_charges) != n:
            raise MoleculeError(
                f"{self.name}: {len(self.formal_charges)} formal charges for {n} atoms"
            )
        seen: set[tuple[int, int]] = set()
        for i, j in self.bonds:
            if not (0 <= i < j < n):
                raise MoleculeError(
                    f"{self.name}: bond ({i}, {j}) is not a sorted pair of atom indices"
                )
            if (i, j) in seen:
                raise MoleculeError(f"{self.name}: duplicate bond ({i}, {j})")
            seen.add((i, j))
        if list(self.bonds) != sorted(self.bonds):
            raise MoleculeError(f"{self.name}: bonds must be sorted")
        if self.coordinates_nm is not None:
            xyz = np.asarray(self.coordinates_nm, dtype=np.float64)
            if xyz.shape != (n, 3) or not np.all(np.isfinite(xyz)):
                raise MoleculeError(f"{self.name}: coordinates must be a finite ({n}, 3) array")

    @staticmethod
    def make(
        name: str,
        elements: list[str] | tuple[str, ...],
        bonds: list[tuple[int, int]] | list[list[int]],
        formal_charges: list[int] | tuple[int, ...] | None = None,
        coordinates_nm: np.ndarray | None = None,
        smiles: str | None = None,
    ) -> MoleculeGraph:
        """Build a graph from bonds in any order and orientation."""
        normalized = sorted({(min(int(a), int(b)), max(int(a), int(b))) for a, b in bonds})
        if len(normalized) != len(bonds):
            raise MoleculeError(f"{name}: duplicate bonds")
        charges = tuple(formal_charges) if formal_charges is not None else (0,) * len(elements)
        xyz = None if coordinates_nm is None else np.array(coordinates_nm, dtype=np.float64)
        return MoleculeGraph(name, tuple(elements), tuple(normalized), charges, xyz, smiles)

    @property
    def n_atoms(self) -> int:
        return len(self.elements)

    @property
    def formal_charge(self) -> int:
        """The ionic charge number z of the molecule."""
        return sum(self.formal_charges)

    def formula(self) -> str:
        """Hill formula, e.g. C3H4O3."""
        counts: dict[str, int] = {}
        for element in self.elements:
            counts[element] = counts.get(element, 0) + 1
        if "C" in counts:
            order = [e for e in ("C", "H") if e in counts] + sorted(
                e for e in counts if e not in ("C", "H")
            )
        else:
            order = sorted(counts)
        return "".join(f"{e}{counts[e] if counts[e] > 1 else ''}" for e in order)

    def neighbors(self) -> list[list[int]]:
        result: list[list[int]] = [[] for _ in self.elements]
        for i, j in self.bonds:
            result[i].append(j)
            result[j].append(i)
        return [sorted(n) for n in result]

    def distances(self) -> np.ndarray:
        """Shortest bond-path lengths between all atom pairs; -1 where not connected."""
        n = self.n_atoms
        neighbors = self.neighbors()
        dist = np.full((n, n), -1, dtype=np.int64)
        for start in range(n):
            dist[start, start] = 0
            queue = deque([start])
            while queue:
                a = queue.popleft()
                for b in neighbors[a]:
                    if dist[start, b] < 0:
                        dist[start, b] = dist[start, a] + 1
                        queue.append(b)
        return dist

    def angles(self) -> list[tuple[int, int, int]]:
        """All bonded triples (i, j, k) with centre j and i < k."""
        result = []
        for j, nbrs in enumerate(self.neighbors()):
            for a in range(len(nbrs)):
                for b in range(a + 1, len(nbrs)):
                    result.append((nbrs[a], j, nbrs[b]))
        return sorted(result, key=lambda t: (t[1], t[0], t[2]))

    def proper_dihedrals(self) -> list[tuple[int, int, int, int]]:
        """All bonded quadruples i-j-k-l (distinct atoms) around each bond j-k, one orientation."""
        neighbors = self.neighbors()
        result = []
        for j, k in self.bonds:
            for i in neighbors[j]:
                if i == k:
                    continue
                for m in neighbors[k]:
                    if m in (j, i):
                        continue
                    result.append((i, j, k, m))
        return sorted(result)

    def one_four_pairs(self) -> list[tuple[int, int]]:
        """Pairs (i < j) whose shortest bond path has length 3 (spec system-package.md §5.2)."""
        dist = self.distances()
        n = self.n_atoms
        return [(i, j) for i in range(n) for j in range(i + 1, n) if dist[i, j] == 3]

    def is_connected(self) -> bool:
        return bool(np.all(self.distances()[0] >= 0))

    def with_order(
        self, order: list[int] | tuple[int, ...], name: str | None = None
    ) -> MoleculeGraph:
        """The same molecule with atoms reordered: new atom k is old atom order[k]."""
        inverse = {old: new for new, old in enumerate(order)}
        bonds = [(inverse[i], inverse[j]) for i, j in self.bonds]
        xyz = None if self.coordinates_nm is None else self.coordinates_nm[list(order)]
        return MoleculeGraph.make(
            name or self.name,
            [self.elements[k] for k in order],
            bonds,
            [self.formal_charges[k] for k in order],
            xyz,
            self.smiles,
        )


# ---------------------------------------------------------------------------------------------
# Readers (RDKit)


def from_rdkit(mol: object, name: str, smiles: str | None = None) -> MoleculeGraph:
    """Graph from an RDKit molecule with explicit hydrogens; uses its first conformer, if any."""
    from rdkit import Chem

    assert isinstance(mol, Chem.Mol)
    elements = [atom.GetSymbol() for atom in mol.GetAtoms()]
    bonds = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]
    charges = [atom.GetFormalCharge() for atom in mol.GetAtoms()]
    xyz = None
    if mol.GetNumConformers() > 0:
        xyz = np.array(mol.GetConformer().GetPositions(), dtype=np.float64) * ANGSTROM_IN_NM
    return MoleculeGraph.make(name, elements, bonds, charges, xyz, smiles)


def from_smiles(smiles: str, name: str, *, seed: int = 1) -> MoleculeGraph:
    """Graph with explicit hydrogens from SMILES; a 3D geometry from ETKDG if embedding works.

    Atom order: the SMILES atoms in order, then the added hydrogens. The geometry is
    deterministic for a given seed and RDKit version.
    """
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise MoleculeError(f"{name}: RDKit cannot parse SMILES {smiles!r}")
    mol = Chem.AddHs(mol)
    if mol.GetNumAtoms() == 1:
        conf = Chem.Conformer(1)
        conf.SetAtomPosition(0, (0.0, 0.0, 0.0))
        mol.AddConformer(conf, assignId=True)
    else:
        params = AllChem.ETKDGv3()
        params.randomSeed = int(seed) % 2**31
        # Silence RDKit's UFF typing notes during embedding (e.g. for hypervalent P in PF6-).
        RDLogger.DisableLog("rdApp.*")
        try:
            embedded = AllChem.EmbedMolecule(mol, params) == 0
        finally:
            RDLogger.EnableLog("rdApp.error")
            RDLogger.EnableLog("rdApp.warning")
        if not embedded:
            mol.RemoveAllConformers()
    return from_rdkit(mol, name, smiles=Chem.MolToSmiles(Chem.RemoveHs(mol)))


def from_file(path: Path, name: str | None = None, *, charge: int = 0) -> MoleculeGraph:
    """Graph from a MOL/SDF, PDB or XYZ file (hydrogens kept as given).

    XYZ files carry no bonds: connectivity is perceived from the geometry, and `charge` is the
    total charge used for that perception.
    """
    from rdkit import Chem

    path = Path(path)
    label = name or path.stem
    suffix = path.suffix.lower()
    if suffix in (".mol", ".sdf"):
        mol = Chem.MolFromMolFile(str(path), removeHs=False)
    elif suffix == ".pdb":
        mol = Chem.MolFromPDBFile(str(path), removeHs=False)
    elif suffix == ".xyz":
        from rdkit.Chem import rdDetermineBonds

        mol = Chem.MolFromXYZFile(str(path))
        if mol is not None:
            rdDetermineBonds.DetermineConnectivity(mol, charge=charge)
    else:
        raise MoleculeError(f"{path}: unsupported structure format {suffix!r} (MOL, SDF, PDB, XYZ)")
    if mol is None:
        raise MoleculeError(f"{path}: RDKit cannot read this file")
    return from_rdkit(mol, label)

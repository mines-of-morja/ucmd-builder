"""What the 3D view shows, read from a system.json document (design/B2a.md §4, §6).

A package on disk and a system built in memory both reduce to a system.json document (the
builder's `system_document`) plus positions, so the viewer has one input. Positions stay
unwrapped, as in the package. For display only, each molecule is shifted by a lattice vector
so that its centre lies in the cell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AtomInfo:
    """One atom for the properties panel; parameters as in the package (nm, e, kJ/mol)."""

    index: int
    element: str
    name: str
    type: str | None
    charge_e: float | None
    sigma_nm: float | None
    epsilon_kjmol: float | None
    molecule_type: str
    molecule: int


@dataclass(frozen=True, eq=False)
class Scene:
    """Atoms, bonds and cell of a system, in canonical atom order."""

    name: str
    elements: tuple[str, ...]
    positions_nm: np.ndarray
    bonds: np.ndarray
    cell_nm: np.ndarray | None
    molecule: np.ndarray
    doc: dict[str, Any] = field(repr=False)
    _types: tuple[str, ...] = field(repr=False, default=())
    _local: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0, dtype=int))

    @classmethod
    def from_document(cls, doc: dict[str, Any], positions_nm: np.ndarray) -> Scene:
        elements: list[str] = []
        bonds: list[np.ndarray] = []
        molecule: list[np.ndarray] = []
        types: list[str] = []
        local: list[np.ndarray] = []
        start = 0
        index = 0
        for component in doc["components"]:
            name = component["molecule_type"]
            mtype = doc["molecule_types"][name]
            atoms = mtype["atoms"]
            pairs = np.array([b["atoms"] for b in mtype.get("bonds", [])], dtype=int).reshape(-1, 2)
            for _ in range(component["count"]):
                elements += [a["element"] or "X" for a in atoms]
                bonds.append(pairs + start)
                molecule.append(np.full(len(atoms), index))
                types.append(name)
                local.append(np.arange(len(atoms)))
                start += len(atoms)
                index += 1
        positions = np.asarray(positions_nm, dtype=float).reshape(-1, 3)
        if len(positions) != len(elements):
            raise ValueError(f"{len(elements)} atoms in system.json, {len(positions)} positions")
        cell = np.asarray(doc["cell_nm"], dtype=float) if doc.get("cell_nm") else None
        molecule_index = np.concatenate(molecule) if molecule else np.empty(0, dtype=int)
        return cls(
            name=doc.get("name", ""),
            elements=tuple(elements),
            positions_nm=positions,
            bonds=np.concatenate(bonds) if bonds else np.empty((0, 2), dtype=int),
            cell_nm=cell,
            molecule=molecule_index,
            doc=doc,
            _types=tuple(types),
            _local=np.concatenate(local) if local else np.empty(0, dtype=int),
        )

    @property
    def n_atoms(self) -> int:
        return len(self.elements)

    def atoms_of(self, molecule_type: str) -> np.ndarray:
        """Indices of all atoms in molecules of this type (a component of the recipe)."""
        if not self._types:
            return np.empty(0, dtype=int)
        is_type = np.array([t == molecule_type for t in self._types])
        return np.flatnonzero(is_type[self.molecule])

    def atom(self, i: int) -> AtomInfo:
        """Element, type, charge and Lennard-Jones parameters of atom `i`."""
        mol = int(self.molecule[i])
        mtype = self._types[mol]
        template = self.doc["molecule_types"][mtype]["atoms"][int(self._local[i])]
        lj = (self.doc.get("forcefield") or {}).get("atom_types", {}).get(template.get("type"))
        return AtomInfo(
            index=i,
            element=self.elements[i],
            name=template.get("name", ""),
            type=template.get("type"),
            charge_e=template.get("charge_e"),
            sigma_nm=lj["sigma_nm"] if lj else None,
            epsilon_kjmol=lj["epsilon_kjmol"] if lj else None,
            molecule_type=mtype,
            molecule=mol,
        )

    def display_positions(self, positions_nm: np.ndarray | None = None) -> np.ndarray:
        """Positions with every molecule shifted by a lattice vector into the cell (display)."""
        x = self.positions_nm if positions_nm is None else np.asarray(positions_nm, dtype=float)
        if self.cell_nm is None or len(x) == 0:
            return x.copy()
        counts = np.bincount(self.molecule)
        centres = np.stack(
            [np.bincount(self.molecule, weights=x[:, k]) / counts for k in range(3)], axis=1
        )
        fractional = centres @ np.linalg.inv(self.cell_nm)
        shift = np.floor(fractional) @ self.cell_nm
        return x - shift[self.molecule]


def read_package(path: Path | str) -> Scene:
    """The scene of a system package on disk (read-only)."""
    path = Path(path)
    doc = json.loads((path / "system.json").read_text(encoding="utf-8"))
    return Scene.from_document(doc, np.load(path / "positions.npy"))

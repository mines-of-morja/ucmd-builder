"""Molecule files for viewing (design/B2c.md §3.1): plain or extended XYZ, one or many frames.

No package and no cell are needed. Frames that follow each other with the same atoms are one
`Structure` (a path, such as an optimization); other atoms start the next one. Positions are Å in
the file and nm here (a unit boundary).

**Bonds are guessed from distances, for display only**: a pair is drawn bonded below
`BOND_FACTOR` × the sum of RDKit's covalent radii. Alkali and alkaline-earth ions are drawn
without bonds. Nothing guessed here can be run or exported.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..constants import ANGSTROM_IN_NM
from .scene import Scene
from .trajectory import Trajectory, lattice_nm

BOND_FACTOR = 1.25
IONS = frozenset("Li Na K Rb Cs Be Mg Ca Sr Ba".split())
PROPERTIES = re.compile(r"Properties=(\S+)")
LABEL = re.compile(r'\bstep=("[^"]*"|\S+)')
CHUNK = 500  # atoms per block of the distance matrix


class MoleculeFileError(ValueError):
    """The file is not a readable XYZ file."""


@dataclass(frozen=True, eq=False)
class Structure:
    """Frames of the same atoms, with the bonds guessed from the last one."""

    name: str
    scene: Scene
    trajectory: Trajectory


def _columns(comment: str) -> tuple[int, int]:
    """The columns of the element and of x in a frame with this comment line."""
    m = PROPERTIES.search(comment)
    if m is None:
        return 0, 1
    fields = m[1].split(":")
    if len(fields) % 3:
        raise MoleculeFileError(f"Properties={m[1]!r} is not a list of name:type:count")
    column, where = 0, {}
    for name, _kind, count in zip(fields[::3], fields[1::3], fields[2::3], strict=True):
        where[name] = column
        if not count.isdigit():
            raise MoleculeFileError(f"Properties={m[1]!r}: {count!r} is not a count")
        column += int(count)
    if "species" not in where or "pos" not in where:
        raise MoleculeFileError(f"Properties={m[1]!r} has no species and pos")
    return where["species"], where["pos"]


def _elements() -> frozenset[str]:
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    return frozenset(table.GetElementSymbol(z) for z in range(1, 119))


def read_frames(path: Path | str) -> list[tuple[tuple[str, ...], np.ndarray, str]]:
    """(elements, positions in nm, comment) of every frame; an error names its line."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as e:
        raise MoleculeFileError(f"{path.name} is not a text file") from e
    while lines and not lines[-1].strip():
        lines.pop()
    known = _elements()
    frames = []
    at = 0
    while at < len(lines):
        try:
            n = int(lines[at])
        except ValueError as e:
            raise MoleculeFileError(
                f"{path.name}, line {at + 1}: expected a number of atoms, got {lines[at][:40]!r}"
            ) from e
        rows = lines[at + 2 : at + 2 + n]
        if n < 1 or len(rows) < n:
            raise MoleculeFileError(
                f"{path.name}, line {at + 1}: {n} atoms announced, {len(rows)} lines follow"
            )
        comment = lines[at + 1]
        species, pos = _columns(comment)
        elements, xyz = [], np.empty((n, 3))
        for i, row in enumerate(rows):
            where = f"{path.name}, line {at + 3 + i}"
            parts = row.split()
            if len(parts) < max(species + 1, pos + 3):
                raise MoleculeFileError(f"{where}: an element and x y z are needed: {row[:40]!r}")
            element = parts[species].capitalize()
            if element not in known:
                raise MoleculeFileError(f"{where}: {parts[species]!r} is not an element")
            try:
                xyz[i] = [float(v) for v in parts[pos : pos + 3]]
            except ValueError as e:
                raise MoleculeFileError(f"{where}: not a coordinate in {row[:40]!r}") from e
            if not np.isfinite(xyz[i]).all():
                raise MoleculeFileError(f"{where}: a coordinate is not finite")
            elements.append(element)
        frames.append((tuple(elements), xyz * ANGSTROM_IN_NM, comment))
        at += n + 2
    if not frames:
        raise MoleculeFileError(f"{path.name} is empty")
    return frames


def guess_bonds(elements: tuple[str, ...], positions_nm: np.ndarray) -> np.ndarray:
    """Index pairs drawn as bonds (display only; see the module docstring)."""
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    radius = np.array(
        [0.0 if e in IONS else table.GetRcovalent(e) * ANGSTROM_IN_NM for e in elements]
    )
    can = radius > 0.0
    pairs = []
    for start in range(0, len(elements), CHUNK):
        block = slice(start, start + CHUNK)
        d = np.linalg.norm(positions_nm[block, None] - positions_nm[None], axis=2)
        near = (d < BOND_FACTOR * (radius[block, None] + radius[None])) & can[block, None] & can
        i, j = np.nonzero(near)
        i += start
        pairs.append(np.column_stack([i, j])[i < j])
    return np.concatenate(pairs)


def connected(n: int, bonds: np.ndarray) -> np.ndarray:
    """The molecule of each atom: connected sets, numbered by their first atom."""
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in bonds.tolist():
        a, b = root(i), root(j)
        parent[max(a, b)] = min(a, b)
    number: dict[int, int] = {}
    return np.array([number.setdefault(root(i), len(number)) for i in range(n)])


def formula(elements: list[str]) -> str:
    """Hill order: C, H, then the rest by name."""
    count = Counter(elements)
    order = [e for e in ("C", "H") if "C" in count and e in count]
    order += sorted(e for e in count if e not in order)
    return "".join(e + (str(count[e]) if count[e] > 1 else "") for e in order)


def scene_from_xyz(
    name: str, elements: tuple[str, ...], positions_nm: np.ndarray, cell_nm: np.ndarray | None
) -> Scene:
    bonds = guess_bonds(elements, positions_nm)
    molecule = connected(len(elements), bonds)
    names = []
    for k in range(int(molecule.max()) + 1):
        names.append(formula([elements[i] for i in np.flatnonzero(molecule == k)]))
    return Scene(
        name=name,
        elements=elements,
        positions_nm=positions_nm,
        bonds=bonds,
        cell_nm=cell_nm,
        molecule=molecule,
        doc={},
        _types=tuple(names),
    )


def _label(comment: str, k: int) -> str:
    m = LABEL.search(comment)
    return f"step {m[1].strip(chr(34))}" if m else f"frame {k + 1}"


def read_structures(path: Path | str) -> list[Structure]:
    """The structures of a molecule file, in file order."""
    path = Path(path)
    frames = read_frames(path)
    groups: list[list[int]] = []
    for k, (elements, _xyz, _comment) in enumerate(frames):
        if groups and frames[groups[-1][0]][0] == elements:
            groups[-1].append(k)
        else:
            groups.append([k])
    structures = []
    for g, members in enumerate(groups):
        name = path.stem if len(groups) == 1 else f"{path.stem} #{g + 1}"
        elements, last, _comment = frames[members[-1]]
        try:
            cells = [lattice_nm(frames[k][2]) for k in members]
        except ValueError as e:
            raise MoleculeFileError(f"{path.name}: {e}") from e
        if any(c is None for c in cells) and any(c is not None for c in cells):
            raise MoleculeFileError(f"{path.name}: only some frames have a Lattice")
        structures.append(
            Structure(
                name=name,
                scene=scene_from_xyz(name, elements, last, cells[-1]),
                trajectory=Trajectory(
                    steps=np.arange(len(members)),
                    times_ps=np.full(len(members), np.nan),
                    positions_nm=np.stack([frames[k][1] for k in members]),
                    cells_nm=None if cells[-1] is None else np.stack(cells),
                    labels=tuple(_label(frames[k][2], i) for i, k in enumerate(members)),
                ),
            )
        )
    return structures

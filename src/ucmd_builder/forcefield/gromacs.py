"""The built-in source `gromacs-top`: GROMACS `.top`/`.itp` importer (design/B1.md §4.5).

GROMACS topologies are the most widely shared force-field exchange format. LigParGen, ATB,
ACPYPE (GAFF/GAFF2), OpenFF Interchange and CHARMM-GUI all write them, so this importer
connects all of them at once. GROMACS units are spec units (nm, kJ/mol, degrees, ½k harmonic
forms), so the conversion is the identity. What spec v0.1 cannot express is refused with a
clear message instead of being approximated.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ..molecule import MoleculeGraph
from .api import (
    LJ,
    PRESETS,
    AtomParams,
    Citation,
    Constraint,
    Conventions,
    Interaction,
    Parameterized,
    SourceError,
    check_options,
)
from .matching import find_mapping

PTYPES = {"A", "S", "D", "V"}

# Standard atomic weights (CIAAW, abridged), used only to recognize elements from the masses
# in GROMACS files that have no atomic-number column. A wrong guess cannot slip through: the
# graph match against the recipe molecule then fails.
_REFERENCE_MASSES = {
    "H": 1.008,
    "Li": 6.94,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "Na": 22.990,
    "Mg": 24.305,
    "Si": 28.085,
    "P": 30.974,
    "S": 32.06,
    "Cl": 35.45,
    "K": 39.098,
    "Ar": 39.95,
    "Ca": 40.078,
    "Br": 79.904,
    "I": 126.90,
}
_SYMBOLS_BY_Z = {
    1: "H",
    3: "Li",
    5: "B",
    6: "C",
    7: "N",
    8: "O",
    9: "F",
    11: "Na",
    12: "Mg",
    14: "Si",
    15: "P",
    16: "S",
    17: "Cl",
    18: "Ar",
    19: "K",
    20: "Ca",
    35: "Br",
    53: "I",
}

SUPPORTED = {
    "defaults",
    "atomtypes",
    "moleculetype",
    "atoms",
    "bonds",
    "angles",
    "dihedrals",
    "pairs",
    "constraints",
    "settles",
    "system",
    "molecules",
}


def element_from_mass(mass: float, where: str) -> str:
    best = min(_REFERENCE_MASSES, key=lambda e: abs(_REFERENCE_MASSES[e] - mass))
    if abs(_REFERENCE_MASSES[best] - mass) > 0.1:
        raise SourceError(f"{where}: cannot recognize the element of mass {mass}")
    return best


@dataclass
class AtomType:
    name: str
    mass: float
    charge: float
    sigma_nm: float
    epsilon_kjmol: float
    atomic_number: int | None


@dataclass
class MoleculeType:
    name: str
    nrexcl: int
    atoms: list[tuple[str, str, float, float | None]] = field(default_factory=list)
    """(type, atom name, charge, mass or None)"""
    bonds: list[Interaction] = field(default_factory=list)
    angles: list[Interaction] = field(default_factory=list)
    dihedrals: list[Interaction] = field(default_factory=list)
    impropers: list[Interaction] = field(default_factory=list)
    pairs: list[tuple[int, int]] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    connections: list[tuple[int, int]] = field(default_factory=list)
    """Bonds of the graph: [ bonds ] plus connecting constraints and settles."""


@dataclass
class Topology:
    defaults: Conventions | None
    atomtypes: dict[str, AtomType]
    molecules: dict[str, MoleculeType]
    sha256: str


# ---------------------------------------------------------------------------------------------
# Reading


def _read_lines(path: Path, seen: set[Path]) -> list[tuple[str, str, int]]:
    """Comment-free lines (text, file, line number) with `#include` resolved."""
    path = path.resolve()
    if path in seen:
        raise SourceError(f"{path}: recursive #include")
    if not path.is_file():
        raise SourceError(f"{path}: file not found")
    seen = seen | {path}
    out: list[tuple[str, str, int]] = []
    skipping = 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            words = line.split()
            directive = words[0]
            if directive == "#ifdef" and len(words) == 2 and words[1].startswith("POSRES"):
                skipping += 1
                continue
            if directive == "#endif" and skipping:
                skipping -= 1
                continue
            if skipping:
                continue
            if directive == "#include" and len(words) == 2:
                target = path.parent / words[1].strip('"<>')
                out.extend(_read_lines(target, seen))
                continue
            raise SourceError(
                f"{path}:{number}: preprocessor directive {directive!r} is not supported "
                "(only #include of local files and #ifdef POSRES blocks)"
            )
        if not skipping:
            out.append((line, str(path), number))
    if skipping:
        raise SourceError(f"{path}: unterminated #ifdef")
    return out


def _floats(words: list[str], where: str) -> list[float]:
    try:
        return [float(w) for w in words]
    except ValueError:
        raise SourceError(f"{where}: expected numbers, got {' '.join(words)!r}") from None


def read_topology(path: Path) -> Topology:
    """Parse a GROMACS topology (with local includes) into its parts."""
    lines = _read_lines(Path(path), set())
    digest = hashlib.sha256()
    for text, _, _ in lines:
        digest.update(text.encode() + b"\n")
    defaults: Conventions | None = None
    atomtypes: dict[str, AtomType] = {}
    molecules: dict[str, MoleculeType] = {}
    section = ""
    current: MoleculeType | None = None
    for text, file, number in lines:
        where = f"{file}:{number}"
        header = re.fullmatch(r"\[\s*([A-Za-z_]+)\s*\]", text)
        if header:
            section = header.group(1).lower()
            if section not in SUPPORTED:
                raise SourceError(
                    f"{where}: [ {section} ] is not supported by spec v0.1 (supported: "
                    f"{', '.join(sorted(SUPPORTED))})"
                )
            continue
        words = text.split()
        if section == "defaults":
            defaults = _parse_defaults(words, where)
        elif section == "atomtypes":
            atomtype = _parse_atomtype(words, where, defaults)
            atomtypes[atomtype.name] = atomtype
        elif section == "moleculetype":
            if len(words) != 2:
                raise SourceError(f"{where}: [ moleculetype ] needs `name nrexcl`")
            current = MoleculeType(words[0], int(words[1]))
            molecules[current.name] = current
        elif section in ("system", "molecules"):
            continue
        else:
            if current is None:
                raise SourceError(f"{where}: [ {section} ] before any [ moleculetype ]")
            _parse_molecule_line(section, words, current, where)
    return Topology(defaults, atomtypes, molecules, digest.hexdigest())


def _parse_defaults(words: list[str], where: str) -> Conventions:
    if len(words) < 5:
        raise SourceError(f"{where}: [ defaults ] needs nbfunc comb-rule gen-pairs fudgeLJ fudgeQQ")
    nbfunc, comb_rule, gen_pairs = int(words[0]), int(words[1]), words[2].lower()
    if nbfunc != 1:
        raise SourceError(f"{where}: nbfunc {nbfunc} (Buckingham) is not supported")
    rules = {2: "lorentz-berthelot", 3: "geometric"}
    if comb_rule not in rules:
        raise SourceError(f"{where}: comb-rule {comb_rule} (C6/C12) is not supported; use 2 or 3")
    if gen_pairs != "yes":
        raise SourceError(f"{where}: gen-pairs must be yes (pair parameters cannot be expressed)")
    family = "OPLS-AA" if comb_rule == 3 else ""
    return Conventions(rules[comb_rule], float(words[3]), float(words[4]), family)


def _parse_atomtype(words: list[str], where: str, defaults: Conventions | None) -> AtomType:
    p = next((i for i, w in enumerate(words) if w in PTYPES), None)
    if p is None or p < 3 or len(words) < p + 3:
        raise SourceError(f"{where}: cannot read [ atomtypes ] line {' '.join(words)!r}")
    if words[p] != "A":
        raise SourceError(
            f"{where}: particle type {words[p]!r} (virtual or shell) is not supported"
        )
    mass, charge = _floats(words[p - 2 : p], where)
    sigma, epsilon = _floats(words[p + 1 : p + 3], where)
    head = words[: p - 2]
    atomic_number = int(head[-1]) if len(head) > 1 and head[-1].isdigit() else None
    return AtomType(head[0], mass, charge, sigma, epsilon, atomic_number)


def _parse_molecule_line(section: str, words: list[str], mol: MoleculeType, where: str) -> None:
    def idx(values: list[str]) -> tuple[int, ...]:
        n = len(mol.atoms)
        out = tuple(int(v) - 1 for v in values)
        if any(not 0 <= i < n for i in out):
            raise SourceError(f"{where}: atom index out of range")
        return out

    if section == "atoms":
        if len(words) < 6:
            raise SourceError(
                f"{where}: [ atoms ] needs nr type resnr residue atom cgnr [charge [mass]]"
            )
        if int(words[0]) != len(mol.atoms) + 1:
            raise SourceError(f"{where}: atoms must be numbered 1, 2, … in order")
        charge = float(words[6]) if len(words) > 6 else float("nan")
        mass = float(words[7]) if len(words) > 7 else None
        mol.atoms.append((words[1], words[4], charge, mass))
    elif section == "bonds":
        atoms, funct, values = idx(words[:2]), int(words[2]), words[3:]
        if funct != 1 or len(values) < 2:
            raise SourceError(
                f"{where}: only explicit harmonic bonds (funct 1 with b0 kb) are supported"
            )
        b0, kb = _floats(values[:2], where)
        mol.bonds.append(Interaction(atoms, "harmonic", {"r0_nm": b0, "k_kjmol_nm2": kb}))
        mol.connections.append((atoms[0], atoms[1]))
    elif section == "angles":
        atoms, funct, values = idx(words[:3]), int(words[3]), words[4:]
        if funct != 1 or len(values) < 2:
            raise SourceError(
                f"{where}: only explicit harmonic angles (funct 1 with θ0 k) are supported"
            )
        theta0, k = _floats(values[:2], where)
        mol.angles.append(Interaction(atoms, "harmonic", {"theta0_deg": theta0, "k_kjmol_rad2": k}))
    elif section == "dihedrals":
        atoms, funct, values = idx(words[:4]), int(words[4]), _floats(words[5:], where)
        if funct in (1, 9, 4) and len(values) >= 3:
            target = mol.impropers if funct == 4 else mol.dihedrals
            target.append(
                Interaction(
                    atoms,
                    "periodic",
                    {"k_kjmol": values[1], "n": int(values[2]), "phi0_deg": values[0]},
                )
            )
        elif funct == 3 and len(values) >= 6:
            params = {f"c{i}_kjmol": values[i] for i in range(6)}
            mol.dihedrals.append(Interaction(atoms, "ryckaert-bellemans", params))
        elif funct == 5 and len(values) >= 4:
            params = {f"v{i + 1}_kjmol": values[i] for i in range(4)}
            mol.dihedrals.append(Interaction(atoms, "opls-fourier", params))
        elif funct == 2 and len(values) >= 2:
            params = {"xi0_deg": values[0], "k_kjmol_rad2": values[1]}
            mol.impropers.append(Interaction(atoms, "harmonic", params))
        else:
            raise SourceError(
                f"{where}: dihedral funct {funct} with these parameters is not supported "
                "(1, 2, 3, 4, 5, 9 with explicit values)"
            )
    elif section == "pairs":
        atoms, funct = idx(words[:2]), int(words[2])
        if funct != 1 or len(words) > 3:
            raise SourceError(
                f"{where}: pairs with explicit parameters cannot be expressed in spec v0.1; "
                "the 1-4 interactions follow from the bond graph and the fudge factors"
            )
        mol.pairs.append((min(atoms), max(atoms)))
    elif section == "constraints":
        atoms, funct = idx(words[:2]), int(words[2])
        (b0,) = _floats(words[3:4], where)
        mol.constraints.append(Constraint((atoms[0], atoms[1]), b0))
        if funct == 1:
            mol.connections.append((atoms[0], atoms[1]))
    elif section == "settles":
        (o,) = idx(words[:1])
        doh, dhh = _floats(words[2:4], where)
        h1, h2 = o + 1, o + 2
        mol.constraints += [
            Constraint((o, h1), doh),
            Constraint((o, h2), doh),
            Constraint((h1, h2), dhh),
        ]
        mol.connections += [(o, h1), (o, h2)]


def read_gro(path: Path) -> np.ndarray:
    """Coordinates (nm) from a .gro file, in file order."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    n = int(lines[1].split()[0])
    xyz = []
    for line in lines[2 : 2 + n]:
        # 5+5+5+5 fixed characters, then x, y, z. As in GROMACS, the field width follows from the
        # distance between the first two decimal points (8 for %8.3f, 11 for six decimals).
        rest = line[20:]
        first = rest.index(".")
        width = rest.index(".", first + 1) - first
        xyz.append([float(rest[i * width : (i + 1) * width]) for i in range(3)])
    return np.array(xyz, dtype=np.float64)


# ---------------------------------------------------------------------------------------------
# The source


class GromacsTopSource:
    """Parameters from a GROMACS topology, mapped onto the recipe molecule by graph matching."""

    name: ClassVar[str] = "gromacs-top"
    version: ClassVar[str] = "1"
    OPTIONS: ClassVar[set[str]] = {"file", "molecule", "conventions", "coordinates", "citations"}

    def parameterize(
        self, molecule: MoleculeGraph, options: Mapping[str, Any], *, base_dir: Path
    ) -> Parameterized:
        check_options(self.name, options, self.OPTIONS)
        if "file" not in options:
            raise SourceError("gromacs-top: option `file` (a .top or .itp file) is required")
        citations = options.get("citations")
        if not citations:
            raise SourceError(
                "gromacs-top: option `citations` is required: a list of "
                "{what, citation[, doi, url]} "
                "naming where the parameters come from (CLAUDE.md: every parameter cites a source)"
            )
        path = (base_dir / str(options["file"])).resolve()
        topology = read_topology(path)
        conventions = self._conventions(topology, options, path)
        mol = self._select(topology, options, path)
        if mol.nrexcl != 3:
            raise SourceError(
                f"{path}: nrexcl {mol.nrexcl}; spec v0.1 excludes exactly 1-2 and 1-3 pairs "
                "(nrexcl 3)"
            )
        atoms, lj, elements = self._atoms(topology, mol, path)
        graph = MoleculeGraph.make(mol.name, elements, mol.connections)
        pairs = sorted(set(mol.pairs))
        expected = graph.one_four_pairs()
        if pairs != expected:
            missing = sorted(set(expected) - set(pairs))[:5]
            extra = sorted(set(pairs) - set(expected))[:5]
            raise SourceError(
                f"{path}: [ pairs ] differs from the 1-4 pairs of the bond graph "
                f"(missing {missing}, "
                f"extra {extra}); spec v0.1 scales exactly the pairs at bond distance 3"
            )
        coordinates = None
        if "coordinates" in options:
            coordinates = read_gro(base_dir / str(options["coordinates"]))
            if coordinates.shape != (len(atoms), 3):
                raise SourceError(
                    f"{options['coordinates']}: {len(coordinates)} atoms, topology has {len(atoms)}"
                )
        parameterized = Parameterized(
            atoms=tuple(atoms),
            lj=lj,
            bonds=tuple(mol.bonds),
            angles=tuple(mol.angles),
            dihedrals=tuple(mol.dihedrals),
            impropers=tuple(mol.impropers),
            constraints=tuple(mol.constraints),
            conventions=conventions,
            citations=tuple(_citation(c) for c in citations),
            source_id=f"gromacs-top:{path.name}#{mol.name}@sha256:{topology.sha256[:16]}",
            coordinates_nm=coordinates,
            record={
                "source": "gromacs-top",
                "file": str(options["file"]),
                "molecule": mol.name,
                "sha256": topology.sha256,
            },
        )
        mapping = find_mapping(graph.elements, graph.bonds, molecule.elements, molecule.bonds)
        if mapping is None:
            raise SourceError(
                f"{path}: molecule {mol.name} ({graph.formula()}) does not match {molecule.name} "
                f"({molecule.formula()}) by elements and bonds"
            )
        order = [0] * len(mapping)
        for t, k in enumerate(mapping):
            order[k] = t
        return parameterized.reordered(order)

    @staticmethod
    def _conventions(topology: Topology, options: Mapping[str, Any], path: Path) -> Conventions:
        if topology.defaults is not None:
            if "conventions" in options:
                raise SourceError(f"{path} has [ defaults ]; do not also set `conventions`")
            return topology.defaults
        preset = options.get("conventions")
        if isinstance(preset, str) and preset in PRESETS:
            return PRESETS[preset]
        if isinstance(preset, Mapping):
            return Conventions(
                str(preset["combination_rule"]),
                float(preset["scale_14_lj"]),
                float(preset["scale_14_coulomb"]),
                str(preset.get("family", "")),
            )
        raise SourceError(
            f"{path} has no [ defaults ]; set `conventions` to a preset ({', '.join(PRESETS)}) "
            "or to {combination_rule, scale_14_lj, scale_14_coulomb}"
        )

    @staticmethod
    def _select(topology: Topology, options: Mapping[str, Any], path: Path) -> MoleculeType:
        if "molecule" in options:
            name = str(options["molecule"])
            if name not in topology.molecules:
                raise SourceError(
                    f"{path}: no [ moleculetype ] {name!r} (have {list(topology.molecules)})"
                )
            return topology.molecules[name]
        if len(topology.molecules) != 1:
            raise SourceError(
                f"{path}: {len(topology.molecules)} molecule types; choose one with `molecule`"
            )
        return next(iter(topology.molecules.values()))

    @staticmethod
    def _atoms(
        topology: Topology, mol: MoleculeType, path: Path
    ) -> tuple[list[AtomParams], dict[str, LJ], list[str]]:
        atoms, lj, elements = [], {}, []
        for k, (type_name, atom_name, charge, mass) in enumerate(mol.atoms):
            if type_name not in topology.atomtypes:
                raise SourceError(f"{path}: atom {k + 1} has unknown type {type_name!r}")
            t = topology.atomtypes[type_name]
            q = t.charge if np.isnan(charge) else charge
            m = t.mass if mass is None else mass
            atoms.append(AtomParams(atom_name, type_name, q, m))
            lj[type_name] = LJ(t.sigma_nm, t.epsilon_kjmol)
            if t.atomic_number is not None and t.atomic_number in _SYMBOLS_BY_Z:
                elements.append(_SYMBOLS_BY_Z[t.atomic_number])
            else:
                elements.append(element_from_mass(m, f"{path}: atom {k + 1}"))
        return atoms, lj, elements


def _citation(item: Any) -> Citation:
    if not isinstance(item, Mapping) or "what" not in item or "citation" not in item:
        raise SourceError("gromacs-top: each citation needs `what` and `citation`")
    return Citation(str(item["what"]), str(item["citation"]), item.get("doi"), item.get("url"))

"""Checks that every force-field source must pass (design/B1.md §4.6).

The builder counterpart of the engine's provider test kit: plug-in authors call
`check_source` in their own tests. Each check returns findings instead of raising at the
first problem, so a report shows everything at once.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..molecule import MoleculeGraph
from .api import (
    ANGLE_FORMS,
    BOND_FORMS,
    COMBINATION_RULES,
    DIHEDRAL_FORMS,
    IMPROPER_FORMS,
    PARAMS,
    ForceFieldSource,
    Parameterized,
)

# Sanity ranges that catch unit mix-ups (Å vs nm, kcal vs kJ, K vs ½k), not physics limits.
SIGMA_NM = (0.05, 0.6)
EPSILON_KJMOL = (0.0, 5.0)
BOND_R0_NM = (0.08, 0.3)
BOND_K = (1e4, 1e6)
ANGLE_K = (10.0, 2000.0)
CHARGE_TOLERANCE_E = 1e-6


@dataclass
class Report:
    molecule: str
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"{self.molecule}: {'OK' if self.ok else 'FAILED'}"]
        lines += [f"  error: {e}" for e in self.errors]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def check_parameters(molecule: MoleculeGraph, p: Parameterized) -> Report:
    """Static checks of one result against its molecule (checks 1–6)."""
    r = Report(molecule.name)
    n = molecule.n_atoms
    # 1. atoms
    if len(p.atoms) != n:
        r.errors.append(f"{len(p.atoms)} atoms in the result, {n} in the molecule")
        return r
    for k, atom in enumerate(p.atoms):
        if atom.type not in p.lj:
            r.errors.append(f"atom {k} has type {atom.type!r} without Lennard-Jones parameters")
        if not math.isfinite(atom.charge_e):
            r.errors.append(f"atom {k} has no finite charge")
        if not atom.mass_amu > 0:
            r.errors.append(f"atom {k} has mass {atom.mass_amu}")
    # 2. bonded terms against the graph
    bonds = {tuple(sorted(b.atoms)) for b in p.bonds}
    graph_bonds = set(molecule.bonds)
    constrained = {tuple(sorted(c.atoms)) for c in p.constraints}
    if graph_bonds - bonds - constrained:
        r.errors.append(f"bonds without parameters: {sorted(graph_bonds - bonds - constrained)}")
    if bonds - graph_bonds:
        r.errors.append(
            f"parameters for bonds that are not in the graph: {sorted(bonds - graph_bonds)}"
        )
    angles = {(a.atoms[1], *sorted((a.atoms[0], a.atoms[2]))) for a in p.angles}
    graph_angles = {(j, i, k) for i, j, k in molecule.angles()}
    if angles - graph_angles:
        r.errors.append(f"angles that are not in the graph: {sorted(angles - graph_angles)}")
    if graph_angles - angles:
        r.notes.append(f"{len(graph_angles - angles)} angle(s) of the graph have no term")

    def canon(d: tuple[int, ...]) -> tuple[int, ...]:
        return min(d, d[::-1])

    dihedrals = {canon(d.atoms) for d in p.dihedrals}
    graph_dihedrals = {canon(d) for d in molecule.proper_dihedrals()}
    if dihedrals - graph_dihedrals:
        r.errors.append(
            f"dihedrals that are not in the graph: {sorted(dihedrals - graph_dihedrals)}"
        )
    if graph_dihedrals - dihedrals:
        r.notes.append(f"{len(graph_dihedrals - dihedrals)} dihedral(s) of the graph have no term")
    neighbors = molecule.neighbors()
    for imp in p.impropers:
        centres = [a for a in imp.atoms if set(imp.atoms) - {a} <= set(neighbors[a])]
        if len(set(imp.atoms)) != 4 or not centres:
            r.errors.append(f"improper {imp.atoms} has no atom bonded to the other three")
    # forms and parameter names
    for kind, items, forms in (
        ("bond", p.bonds, BOND_FORMS),
        ("angle", p.angles, ANGLE_FORMS),
        ("dihedral", p.dihedrals, DIHEDRAL_FORMS),
        ("improper", p.impropers, IMPROPER_FORMS),
    ):
        for item in items:
            if item.form not in forms:
                r.errors.append(f"{kind} {item.atoms}: form {item.form!r} not in {forms}")
                continue
            expected = set(PARAMS[f"{kind}/{item.form}"])
            if set(item.params) != expected:
                r.errors.append(
                    f"{kind} {item.atoms}: params {sorted(item.params)} ≠ {sorted(expected)}"
                )
            elif not all(math.isfinite(float(v)) for v in item.params.values()):
                r.errors.append(f"{kind} {item.atoms}: non-finite parameter")
    # 3. net charge
    if abs(p.net_charge_e - molecule.formal_charge) > CHARGE_TOLERANCE_E:
        r.errors.append(
            f"net charge {p.net_charge_e:.8f} e ≠ formal charge {molecule.formal_charge} "
            "(before transforms)"
        )
    # 4. conventions
    c = p.conventions
    if c.combination_rule not in COMBINATION_RULES:
        r.errors.append(f"combination rule {c.combination_rule!r}")
    # 5. sanity ranges
    for name, lj in p.lj.items():
        if lj.sigma_nm == 0.0 and lj.epsilon_kjmol == 0.0:
            continue
        if not SIGMA_NM[0] <= lj.sigma_nm <= SIGMA_NM[1]:
            r.errors.append(f"type {name}: σ = {lj.sigma_nm} nm outside {SIGMA_NM} (units?)")
        if not EPSILON_KJMOL[0] <= lj.epsilon_kjmol <= EPSILON_KJMOL[1]:
            r.errors.append(f"type {name}: ε = {lj.epsilon_kjmol} kJ/mol outside {EPSILON_KJMOL}")
    for b in p.bonds:
        r0, k = b.params["r0_nm"], b.params["k_kjmol_nm2"]
        if not BOND_R0_NM[0] <= r0 <= BOND_R0_NM[1]:
            r.errors.append(f"bond {b.atoms}: r0 = {r0} nm outside {BOND_R0_NM} (units?)")
        if not BOND_K[0] <= k <= BOND_K[1]:
            r.errors.append(f"bond {b.atoms}: k = {k} kJ/mol/nm² outside {BOND_K} (units or ½k?)")
    for a in p.angles:
        theta0, k = a.params["theta0_deg"], a.params["k_kjmol_rad2"]
        if not 60.0 < theta0 <= 180.0:
            r.errors.append(f"angle {a.atoms}: θ0 = {theta0}° outside (60, 180]")
        if not ANGLE_K[0] <= k <= ANGLE_K[1]:
            r.errors.append(
                f"angle {a.atoms}: k = {k} kJ/mol/rad² outside {ANGLE_K} (units or ½k?)"
            )
    # 6. citations
    if not p.citations:
        r.errors.append("no citation")
    return r


def check_source(
    source: ForceFieldSource,
    molecules: list[MoleculeGraph],
    options: Mapping[str, Any],
    *,
    base_dir: Path,
) -> list[Report]:
    """Run every check, including determinism (7), on each molecule.

    Check 8 (the package passes the spec validator) runs in the builder's package tests.
    """
    reports = []
    for molecule in molecules:
        first = source.parameterize(molecule, options, base_dir=base_dir)
        report = check_parameters(molecule, first)
        second = source.parameterize(molecule, options, base_dir=base_dir)
        if _fingerprint(first) != _fingerprint(second):
            report.errors.append("two calls gave different results (not deterministic)")
        reports.append(report)
    return reports


def _fingerprint(p: Parameterized) -> str:
    parts = [repr(p.atoms), repr(sorted(p.lj.items()))]
    for items in (p.bonds, p.angles, p.dihedrals, p.impropers):
        parts.append(repr([(i.atoms, i.form, sorted(i.params.items())) for i in items]))
    parts.append(repr(p.constraints))
    parts.append(repr(None if p.coordinates_nm is None else p.coordinates_nm.tolist()))
    return "\n".join(parts)

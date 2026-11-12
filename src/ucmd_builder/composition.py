"""Composition calculator: molecule counts and box size for a salt solution (PLAN.md B1.4).

Masses are those of the parameterized molecules, i.e. the masses that go into the package.
Counts are rounded to integers, and the result reports the concentrations actually achieved.

Units: masses in amu (= g/mol, ADR 0003), densities in g/cm³, lengths in nm.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .constants import G_PER_CM3_IN_AMU_PER_NM3, MOL_PER_L_IN_PER_NM3
from .recipe import Composition, RecipeError


@dataclass(frozen=True)
class CompositionResult:
    counts: dict[str, int]
    total_mass_amu: float
    box_length_nm: float | None
    molarity_mol_L: float | None
    molality_mol_kg: float
    solvent_fractions: dict[str, float]
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "total_mass_amu": self.total_mass_amu,
            "box_length_nm": self.box_length_nm,
            "achieved_molarity_mol_L": self.molarity_mol_L,
            "achieved_molality_mol_kg": self.molality_mol_kg,
            "achieved_solvent_fractions": dict(self.solvent_fractions),
            "notes": list(self.notes),
        }


def largest_remainder(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """Integers proportional to `weights` that sum exactly to `total`."""
    s = sum(weights.values())
    exact = {k: total * w / s for k, w in weights.items()}
    counts = {k: int(v) for k, v in exact.items()}
    rest = total - sum(counts.values())
    order = sorted(exact, key=lambda k: (-(exact[k] - counts[k]), k))
    for k in order[:rest]:
        counts[k] += 1
    return counts


def mole_fractions(comp: Composition, masses: Mapping[str, float]) -> dict[str, float]:
    """Solvent mole fractions from mass or volume fractions."""
    if comp.basis == "mass":
        moles = {k: w / masses[k] for k, w in comp.solvent.items()}
    else:
        assert comp.solvent_densities_g_cm3 is not None
        moles = {
            k: phi * comp.solvent_densities_g_cm3[k] / masses[k] for k, phi in comp.solvent.items()
        }
    total = sum(moles.values())
    return {k: v / total for k, v in moles.items()}


def box_length_nm(total_mass_amu: float, density_g_cm3: float) -> float:
    volume_nm3 = total_mass_amu / (density_g_cm3 * G_PER_CM3_IN_AMU_PER_NM3)
    return volume_nm3 ** (1.0 / 3.0)


def compose(comp: Composition, masses: Mapping[str, float]) -> CompositionResult:
    """Counts of salt and solvent molecules for the requested composition and size."""
    cation, anion = comp.salt
    for name in (cation, anion, *comp.solvent):
        if name not in masses:
            raise RecipeError(f"[composition]: no mass for component {name}")
    salt_mass = masses[cation] + masses[anion]
    x = mole_fractions(comp, masses)
    mean_solvent_mass = sum(x[k] * masses[k] for k in x)
    density = comp.density.value_g_cm3 if comp.density else None
    rho = density * G_PER_CM3_IN_AMU_PER_NM3 if density else None
    notes: list[str] = []

    if comp.solvent_molecules is not None:
        solvent = largest_remainder(comp.solvent_molecules, x)
        solvent_mass = sum(n * masses[k] for k, n in solvent.items())
        if comp.molality_mol_kg is not None:
            exact_pairs = comp.molality_mol_kg * solvent_mass / 1000.0
        else:
            assert comp.molarity_mol_L is not None and rho is not None
            c = comp.molarity_mol_L * MOL_PER_L_IN_PER_NM3
            if c * salt_mass >= rho:
                raise RecipeError("[composition]: the molarity is impossible at this density")
            exact_pairs = c * solvent_mass / (rho - c * salt_mass)
        pairs = round(exact_pairs)
        if pairs < 1:
            raise RecipeError(f"[composition]: {exact_pairs:.3f} ion pairs; enlarge the system")
        notes.append(f"ion pairs rounded from {exact_pairs:.4f} to {pairs}")
    else:
        assert comp.ion_pairs is not None
        pairs = comp.ion_pairs
        if comp.molality_mol_kg is not None:
            solvent_mass_target = 1000.0 * pairs / comp.molality_mol_kg
        else:
            assert comp.molarity_mol_L is not None and rho is not None
            c = comp.molarity_mol_L * MOL_PER_L_IN_PER_NM3
            solvent_mass_target = pairs * rho / c - pairs * salt_mass
        exact_solvent = solvent_mass_target / mean_solvent_mass
        total = round(exact_solvent)
        if total < 1:
            raise RecipeError("[composition]: no solvent molecules at this concentration")
        solvent = largest_remainder(total, x)
        notes.append(f"solvent molecules rounded from {exact_solvent:.3f} to {total}")

    counts = {cation: pairs, anion: pairs, **solvent}
    solvent_mass = sum(n * masses[k] for k, n in solvent.items())
    total_mass = solvent_mass + pairs * salt_mass
    length = box_length_nm(total_mass, density) if density else None
    molarity = None
    if length is not None:
        molarity = pairs / (length**3 * MOL_PER_L_IN_PER_NM3)
    molality = pairs / (solvent_mass / 1000.0)
    if comp.basis == "mass":
        achieved = {k: n * masses[k] / solvent_mass for k, n in solvent.items()}
    else:
        assert comp.solvent_densities_g_cm3 is not None
        vols = {k: n * masses[k] / comp.solvent_densities_g_cm3[k] for k, n in solvent.items()}
        achieved = {k: v / sum(vols.values()) for k, v in vols.items()}
    return CompositionResult(counts, total_mass, length, molarity, molality, achieved, notes)

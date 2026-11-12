"""The force-field source interface (ADR 0008, design/B1.md §4.1).

A source turns a `MoleculeGraph` into explicit classical parameters in spec units, in the
molecule's canonical atom order, together with the force-field conventions and citations.
Sources are plug-ins discovered by name (`registry.py`); the builder never needs to know
which generator made the parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

import numpy as np

from ..molecule import MoleculeGraph

COMBINATION_RULES = ("geometric", "lorentz-berthelot")
BOND_FORMS = ("harmonic",)
ANGLE_FORMS = ("harmonic",)
DIHEDRAL_FORMS = ("periodic", "opls-fourier", "ryckaert-bellemans")
IMPROPER_FORMS = ("periodic", "harmonic")

# Parameter names per functional form, in spec units (spec/system-package.md §5.3).
PARAMS: dict[str, tuple[str, ...]] = {
    "bond/harmonic": ("r0_nm", "k_kjmol_nm2"),
    "angle/harmonic": ("theta0_deg", "k_kjmol_rad2"),
    "dihedral/periodic": ("k_kjmol", "n", "phi0_deg"),
    "dihedral/opls-fourier": ("v1_kjmol", "v2_kjmol", "v3_kjmol", "v4_kjmol"),
    "dihedral/ryckaert-bellemans": tuple(f"c{i}_kjmol" for i in range(6)),
    "improper/periodic": ("k_kjmol", "n", "phi0_deg"),
    "improper/harmonic": ("xi0_deg", "k_kjmol_rad2"),
}


class SourceError(ValueError):
    """A source cannot parameterize a molecule; the message says why and what to do."""


@dataclass(frozen=True)
class Conventions:
    """Force-field conventions that a whole package shares (spec §5)."""

    combination_rule: str
    scale_14_lj: float
    scale_14_coulomb: float
    family: str = ""

    def __post_init__(self) -> None:
        if self.combination_rule not in COMBINATION_RULES:
            raise SourceError(f"unknown combination rule {self.combination_rule!r}")
        for value in (self.scale_14_lj, self.scale_14_coulomb):
            if not 0.0 <= value <= 1.0:
                raise SourceError(f"1-4 scale factors must lie in [0, 1], got {value}")

    def same_physics(self, other: Conventions) -> bool:
        """True if both describe the same interactions (the family name is informative)."""
        return (self.combination_rule, self.scale_14_lj, self.scale_14_coulomb) == (
            other.combination_rule,
            other.scale_14_lj,
            other.scale_14_coulomb,
        )


PRESETS: dict[str, Conventions] = {
    "opls": Conventions("geometric", 0.5, 0.5, "OPLS-AA"),
}


@dataclass(frozen=True)
class Citation:
    """One entry of `provenance.sources` in the package."""

    what: str
    citation: str
    doi: str | None = None
    url: str | None = None

    def to_json(self) -> dict[str, str]:
        out = {"what": self.what, "citation": self.citation}
        if self.doi:
            out["doi"] = self.doi
        if self.url:
            out["url"] = self.url
        return out


@dataclass(frozen=True)
class LJ:
    """Lennard-Jones parameters of one atom type."""

    sigma_nm: float
    epsilon_kjmol: float


@dataclass(frozen=True)
class AtomParams:
    name: str
    type: str
    charge_e: float
    mass_amu: float


@dataclass(frozen=True)
class Interaction:
    """A bonded term: atom indices, spec form and parameters in spec units."""

    atoms: tuple[int, ...]
    form: str
    params: Mapping[str, float]

    def remapped(self, new_index: Mapping[int, int]) -> Interaction:
        return Interaction(tuple(new_index[a] for a in self.atoms), self.form, dict(self.params))


@dataclass(frozen=True)
class Constraint:
    atoms: tuple[int, int]
    length_nm: float


@dataclass(frozen=True, eq=False)
class Parameterized:
    """The result of a source, in the molecule's canonical atom order."""

    atoms: tuple[AtomParams, ...]
    lj: Mapping[str, LJ]
    bonds: tuple[Interaction, ...]
    angles: tuple[Interaction, ...]
    dihedrals: tuple[Interaction, ...]
    impropers: tuple[Interaction, ...]
    constraints: tuple[Constraint, ...]
    conventions: Conventions
    citations: tuple[Citation, ...]
    source_id: str
    coordinates_nm: np.ndarray | None = field(default=None, repr=False)
    description: str = ""
    record: Mapping[str, Any] = field(default_factory=dict)

    @property
    def net_charge_e(self) -> float:
        return float(sum(a.charge_e for a in self.atoms))

    def with_charges_scaled(self, factor: float) -> Parameterized:
        atoms = tuple(replace(a, charge_e=a.charge_e * factor) for a in self.atoms)
        return replace(self, atoms=atoms)

    def reordered(self, order: list[int] | tuple[int, ...]) -> Parameterized:
        """The same parameters with atoms reordered: new atom k is old atom order[k]."""
        new_index = {old: new for new, old in enumerate(order)}

        def remap(items: tuple[Interaction, ...]) -> tuple[Interaction, ...]:
            return tuple(i.remapped(new_index) for i in items)

        return replace(
            self,
            atoms=tuple(self.atoms[k] for k in order),
            bonds=remap(self.bonds),
            angles=remap(self.angles),
            dihedrals=remap(self.dihedrals),
            impropers=remap(self.impropers),
            constraints=tuple(
                Constraint((new_index[c.atoms[0]], new_index[c.atoms[1]]), c.length_nm)
                for c in self.constraints
            ),
            coordinates_nm=None
            if self.coordinates_nm is None
            else np.asarray(self.coordinates_nm)[list(order)].copy(),
        )


@runtime_checkable
class ForceFieldSource(Protocol):
    """A plug-in that parameterizes molecules (ADR 0008).

    `parameterize` must be deterministic and offline. `base_dir` resolves relative file paths
    in `options` (the directory of the recipe).
    """

    name: ClassVar[str]
    version: ClassVar[str]

    def parameterize(
        self, molecule: MoleculeGraph, options: Mapping[str, Any], *, base_dir: Path
    ) -> Parameterized: ...


def check_options(source: str, options: Mapping[str, Any], allowed: set[str]) -> None:
    """Reject unknown option keys, so that typos fail loudly."""
    unknown = sorted(set(options) - allowed - {"source"})
    if unknown:
        raise SourceError(
            f"{source}: unknown option(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )

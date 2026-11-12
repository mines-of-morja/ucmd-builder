"""Conversions of force-field values from the units of their source to spec units.

Library files store values as printed in the source and declare the units and the harmonic
convention used there (design/B1.md §4.4). This module is the single place where they are
converted. Angles stay in degrees; force constants of angles are per rad².

Converted values are rounded to 12 significant digits, so that a printed 3100 kJ mol⁻¹ Å⁻²
becomes 310000 kJ mol⁻¹ nm⁻² rather than 309999.99999999994. The change is at most
5·10⁻¹³ relative, far below every tolerance in the project.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..constants import ANGSTROM_IN_NM, BOLTZMANN_KJMOL_PER_K, KCAL_IN_KJ
from .api import SourceError

ENERGY = {"kJ/mol": 1.0, "kcal/mol": KCAL_IN_KJ}
LENGTH = {"nm": 1.0, "angstrom": ANGSTROM_IN_NM}
HARMONIC = {"k/2": 1.0, "k": 2.0}
"""`k/2`: E = ½k(x − x₀)² (the spec form); `k`: E = K(x − x₀)², so k = 2K."""
SIGNIFICANT_DIGITS = 12


def clean(value: float) -> float:
    """Round a converted value to 12 significant digits (see the module docstring)."""
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")


@dataclass(frozen=True)
class Units:
    energy: float
    length: float
    harmonic: float
    label: str

    @staticmethod
    def parse(table: Mapping[str, str], where: str) -> Units:
        unknown = set(table) - {"energy", "length", "harmonic"}
        if unknown:
            raise SourceError(f"{where}: unknown [units] key(s) {sorted(unknown)}")
        try:
            energy = ENERGY[table.get("energy", "kJ/mol")]
            length = LENGTH[table.get("length", "nm")]
            harmonic = HARMONIC[table.get("harmonic", "k/2")]
        except KeyError as e:
            raise SourceError(
                f"{where}: unsupported unit {e.args[0]!r}; energy {sorted(ENERGY)}, "
                f"length {sorted(LENGTH)}, harmonic {sorted(HARMONIC)}"
            ) from None
        label = ", ".join(f"{k} {v}" for k, v in sorted(table.items()))
        return Units(energy, length, harmonic, label)

    def length_nm(self, value: float) -> float:
        return clean(float(value) * self.length)

    def energy_kjmol(self, value: float) -> float:
        return clean(float(value) * self.energy)

    def bond_k(self, value: float) -> float:
        """Bond force constant → kJ mol⁻¹ nm⁻² in the ½k form."""
        return clean(float(value) * self.harmonic * self.energy / self.length**2)

    def angle_k(self, value: float) -> float:
        """Angle or harmonic-improper force constant → kJ mol⁻¹ rad⁻² in the ½k form."""
        return clean(float(value) * self.harmonic * self.energy)

    @staticmethod
    def kelvin_to_kjmol(value: float) -> float:
        """An energy given as ε/k_B in kelvin."""
        return clean(float(value) * BOLTZMANN_KJMOL_PER_K)

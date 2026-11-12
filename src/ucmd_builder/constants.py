"""Physical constants and unit conversions of the builder (spec/units.md §2, ADR 0001).

Every constant derives from the CODATA 2018 values below. No other module may contain rounded
literals. Conversions at the import boundary (kcal/mol, Å, energies in kelvin, densities) take
their factors from here.
"""

from typing import Final

# CODATA 2018 (exact SI values).
AVOGADRO_PER_MOL: Final = 6.02214076e23
BOLTZMANN_J_PER_K: Final = 1.380649e-23

# Derived values, in spec units (nm, ps, amu, kJ/mol, e, K).
BOLTZMANN_KJMOL_PER_K: Final = BOLTZMANN_J_PER_K * AVOGADRO_PER_MOL / 1000.0
"""k_B·N_A in kJ mol⁻¹ K⁻¹ (spec/units.md: 0.00831446261815324)."""

KCAL_IN_KJ: Final = 4.184
"""Thermochemical calorie: 1 kcal = 4.184 kJ (exact)."""

ANGSTROM_IN_NM: Final = 0.1
"""1 Å = 0.1 nm (exact)."""

G_PER_CM3_IN_AMU_PER_NM3: Final = AVOGADRO_PER_MOL * 1e-21
"""1 g/cm³ in amu/nm³; the amu is 1 g/mol in this unit system (ADR 0003)."""

MOL_PER_L_IN_PER_NM3: Final = AVOGADRO_PER_MOL * 1e-24
"""1 mol/L as a number density in nm⁻³."""

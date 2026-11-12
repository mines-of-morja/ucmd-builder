"""Initial conditions (PLAN.md B1.7): Maxwell–Boltzmann velocities.

Velocities are drawn per atom from the Maxwell–Boltzmann distribution, the centre-of-mass
momentum is removed, and the result is rescaled to exactly the target temperature with
N_dof = 3N − 3, as the engine does (design/E1.md). Units: amu, K → nm/ps, using
1 kJ/mol = 1 amu nm² ps⁻².
"""

from __future__ import annotations

import numpy as np

from .constants import BOLTZMANN_KJMOL_PER_K


def maxwell_boltzmann(masses_amu: np.ndarray, temperature_K: float, seed: int) -> np.ndarray:
    masses = np.asarray(masses_amu, dtype=np.float64)
    n = len(masses)
    rng = np.random.Generator(np.random.PCG64(seed))
    kt = BOLTZMANN_KJMOL_PER_K * temperature_K
    v = rng.normal(size=(n, 3)) * np.sqrt(kt / masses)[:, None]
    if n > 1:
        v -= (masses[:, None] * v).sum(axis=0) / masses.sum()
        n_dof = 3 * n - 3
        current = float((masses[:, None] * v * v).sum()) / (n_dof * BOLTZMANN_KJMOL_PER_K)
        v *= np.sqrt(temperature_K / current)
    return v

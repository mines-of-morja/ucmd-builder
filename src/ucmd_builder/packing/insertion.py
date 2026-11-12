"""Built-in random-insertion packer (design/B1.md §7).

Rigid copies of each structure are placed one after the other at random positions and
orientations. A copy is accepted only if none of its atoms comes closer than the tolerance to
an atom of another molecule (minimum image). The packer is deterministic per seed (NumPy
PCG64). It is meant for tests and dilute systems; liquids at real densities need Packmol.
"""

from __future__ import annotations

import numpy as np

from . import Cancel, Cancelled, PackingError, PackResult, Progress, Structure
from .checks import CellGrid


def random_rotation(rng: np.random.Generator) -> np.ndarray:
    """A uniformly distributed rotation matrix (from a random unit quaternion)."""
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def insert(
    structures: list[Structure],
    box_nm: float,
    tolerance_nm: float,
    seed: int,
    *,
    max_attempts: int = 2000,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> PackResult:
    rng = np.random.Generator(np.random.PCG64(seed))
    grid = CellGrid(box_nm, tolerance_nm)
    placed: list[np.ndarray] = []
    molecule = 0
    total = sum(s.count for s in structures)
    for s in structures:
        local = s.coordinates_nm - s.coordinates_nm.mean(axis=0)
        for copy in range(s.count):
            if cancel is not None and cancel():
                raise Cancelled("packing cancelled")
            for _ in range(max_attempts):
                xyz = local @ random_rotation(rng).T + rng.uniform(0.0, box_nm, size=3)
                if all(grid.nearest(p, exclude_owner=molecule)[1] < 0 for p in xyz):
                    break
            else:
                raise PackingError(
                    f"could not place copy {copy + 1} of {s.name} after {max_attempts} attempts; "
                    "lower the density or the tolerance, or use Packmol"
                )
            for p in xyz:
                grid.add(p, molecule)
            placed.append(xyz)
            molecule += 1
            if progress is not None and molecule % 50 == 0:
                progress(f"inserted {molecule}/{total} molecules")
    positions = np.vstack(placed) if placed else np.zeros((0, 3))
    return PackResult(positions, "insertion", "ucmd-builder insertion packer", seed, np.inf)

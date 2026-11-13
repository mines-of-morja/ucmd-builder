"""Trajectory frames for playback (design/B2a.md §7), read from H5MD after the run ends.

Reads `/particles/all/position` of spec/trajectory-h5md.md: step, time (ps) and value (nm).
An HDF5 file is not read while the engine still writes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np


@dataclass(frozen=True, eq=False)
class Trajectory:
    steps: np.ndarray
    times_ps: np.ndarray
    positions_nm: np.ndarray  # (frames, atoms, 3)

    def __len__(self) -> int:
        return len(self.steps)


def read_trajectory(path: Path | str) -> Trajectory:
    with h5py.File(path, "r") as fh:
        group = fh["particles/all/position"]
        return Trajectory(
            steps=np.asarray(group["step"][()], dtype=np.int64),
            times_ps=np.asarray(group["time"][()], dtype=float),
            positions_nm=np.asarray(group["value"][()], dtype=float),
        )

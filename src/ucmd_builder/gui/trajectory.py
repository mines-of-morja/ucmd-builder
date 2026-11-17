"""Trajectory frames for playback (design/B2a.md §7).

Two formats, chosen by the file name the run writes (`[output.trajectory] file`):

- **H5MD** (`.h5`, spec/trajectory-h5md.md): `/particles/all/position` with step, time (ps)
  and value (nm). Read once the run has ended: an HDF5 file is not read while the engine still
  writes it.
- **Extended XYZ** (`.xyz`): the engine's viewer trajectory, in Å, one frame flushed at a time.
  `XyzTail` reads complete frames while the run goes, so a run can be watched live.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..constants import ANGSTROM_IN_NM


@dataclass(frozen=True, eq=False)
class Trajectory:
    steps: np.ndarray
    times_ps: np.ndarray
    positions_nm: np.ndarray  # (frames, atoms, 3)

    def __len__(self) -> int:
        return len(self.steps)


def read_h5md(path: Path | str) -> Trajectory:
    import h5py

    with h5py.File(path, "r") as fh:
        group = fh["particles/all/position"]
        return Trajectory(
            steps=np.asarray(group["step"][()], dtype=np.int64),
            times_ps=np.asarray(group["time"][()], dtype=float),
            positions_nm=np.asarray(group["value"][()], dtype=float),
        )


def is_live_format(path: Path | str) -> bool:
    """Whether frames of this trajectory can be read while the run writes it."""
    return Path(path).suffix == ".xyz"


class XyzTail:
    """The complete frames of a growing extended-XYZ file; `poll()` adds the new ones.

    A frame is taken only when all its lines have arrived (count line, comment line, one line per
    atom), so a frame the engine is still writing is read at the next poll.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._offset = 0
        self._pending: list[str] = []  # complete lines of an unfinished frame
        self._partial = b""  # an unfinished line
        self.steps: list[int] = []
        self.times_ps: list[float] = []
        self.frames: list[np.ndarray] = []

    def poll(self) -> int:
        """Read what was appended; return the number of new frames."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return 0
        if size < self._offset:  # rewritten, e.g. by a restart: read again from the start
            self.__init__(self.path)
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        self._offset += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()
        self._pending += [line.decode("utf-8") for line in lines]
        added = 0
        while self._pending:
            try:
                n = int(self._pending[0])
            except ValueError as e:
                raise ValueError(f"{self.path}: not an XYZ frame: {self._pending[0]!r}") from e
            if len(self._pending) < n + 2:
                break
            comment, atoms = self._pending[1], self._pending[2 : n + 2]
            self._pending = self._pending[n + 2 :]
            fields = dict(
                w.split("=", 1) for w in comment.split() if w.startswith(("step=", "time_ps="))
            )
            self.steps.append(int(fields.get("step", len(self.steps))))
            self.times_ps.append(float(fields.get("time_ps", "nan")))
            xyz = np.array([line.split()[1:4] for line in atoms], dtype=float)
            self.frames.append(xyz * ANGSTROM_IN_NM)
            added += 1
        return added

    def trajectory(self) -> Trajectory:
        if not self.frames:
            return Trajectory(np.empty(0, np.int64), np.empty(0), np.empty((0, 0, 3)))
        return Trajectory(
            steps=np.array(self.steps, dtype=np.int64),
            times_ps=np.array(self.times_ps),
            positions_nm=np.stack(self.frames),
        )


def read_trajectory(path: Path | str) -> Trajectory:
    """All frames of a finished trajectory, H5MD or extended XYZ by its suffix."""
    if is_live_format(path):
        tail = XyzTail(path)
        tail.poll()
        return tail.trajectory()
    return read_h5md(path)

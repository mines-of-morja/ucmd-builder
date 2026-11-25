"""Trajectory frames for playback (design/B2a.md §7).

Two formats, chosen by the file name the run writes (`[output.trajectory] file`):

- **H5MD** (`.h5`, spec/trajectory-h5md.md): `/particles/all/position` with step, time (ps)
  and value (nm), and the cell of every frame (`box/edges`; NPT changes it). Read once the run
  has ended: an HDF5 file is not read while the engine still writes it.
- **Extended XYZ** (`.xyz`): the engine's viewer trajectory, in Å, one frame flushed at a time.
  `XyzTail` reads complete frames while the run goes, so a run can be watched live.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..constants import ANGSTROM_IN_NM

CACHE_BYTES = 64 * 1024 * 1024  # frames kept by H5Positions


class H5Positions:
    """The positions of an H5MD file, one frame read when it is asked for.

    The file is opened for the moment of reading and without an HDF5 lock, so a looking GUI never
    keeps an engine from continuing the run. If the file no longer has the step it had when it
    was opened (the run was restarted), a frame is an error, never another frame.
    """

    def __init__(self, path: Path, steps: np.ndarray, shape: tuple[int, ...]) -> None:
        self.path, self.steps, self.shape = path, steps, shape
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._room = max(1, CACHE_BYTES // max(1, shape[1] * 3 * 4))

    def __len__(self) -> int:
        return self.shape[0]

    def __getitem__(self, k: int) -> np.ndarray:
        import h5py

        k = range(len(self))[k]  # negative indices, IndexError
        if k in self._cache:
            self._cache.move_to_end(k)
            return self._cache[k]
        with h5py.File(self.path, "r", locking=False) as fh:
            group = fh["particles/all/position"]
            if k >= len(group["step"]) or int(group["step"][k]) != int(self.steps[k]):
                raise ValueError(f"{self.path.name} has changed; open the run again")
            frame = np.asarray(group["value"][k], dtype=np.float32)
        if frame.shape != self.shape[1:]:
            raise ValueError(f"{self.path.name} has changed; open the run again")
        self._cache[k] = frame
        while len(self._cache) > self._room:
            self._cache.popitem(last=False)
        return frame


@dataclass(frozen=True, eq=False)
class Trajectory:
    steps: np.ndarray
    times_ps: np.ndarray
    positions_nm: np.ndarray | H5Positions  # (frames, atoms, 3); [k] is one frame
    cells_nm: np.ndarray | None = None  # (frames, 3, 3), rows a, b, c; None: the package's cell
    labels: tuple[str, ...] | None = None  # instead of the time, for frames without one

    def __len__(self) -> int:
        return len(self.steps)

    def cell(self, k: int) -> np.ndarray | None:
        return None if self.cells_nm is None else self.cells_nm[k]


def read_h5md(path: Path | str) -> Trajectory:
    import h5py

    path = Path(path)
    with h5py.File(path, "r", locking=False) as fh:
        group = fh["particles/all/position"]
        steps = np.asarray(group["step"][()], dtype=np.int64)
        return Trajectory(
            cells_nm=_cells(fh, steps),
            steps=steps,
            times_ps=np.asarray(group["time"][()], dtype=float),
            positions_nm=H5Positions(path, steps, group["value"].shape),
        )


def _cells(fh, steps: np.ndarray) -> np.ndarray | None:
    """The cell of every frame; None if the file has none or is not periodic (all zeros)."""
    box = fh.get("particles/all/box/edges")
    if box is None:
        return None
    cells = np.asarray(box["value"][()], dtype=float)
    if not cells.any():
        return None
    if not np.array_equal(np.asarray(box["step"][()]), steps):
        raise ValueError("the steps of box/edges and position differ")
    return cells


LATTICE = re.compile(r'Lattice="([^"]*)"')


def lattice_nm(comment: str) -> np.ndarray | None:
    """The cell of an extended-XYZ comment line (rows a, b, c), in nm; None without `Lattice`."""
    m = LATTICE.search(comment)
    if m is None:
        return None
    cell = np.array([float(v) for v in m[1].split()])
    if cell.shape != (9,) or not np.isfinite(cell).all():
        raise ValueError(f"Lattice needs nine numbers, not {m[1]!r}")
    return cell.reshape(3, 3) * ANGSTROM_IN_NM


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
        self.cells: list[np.ndarray | None] = []

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
            self.cells.append(lattice_nm(comment))
            self.steps.append(int(fields.get("step", len(self.steps))))
            self.times_ps.append(float(fields.get("time_ps", "nan")))
            xyz = np.array([line.split()[1:4] for line in atoms], dtype=float)
            self.frames.append(xyz * ANGSTROM_IN_NM)
            added += 1
        return added

    def trajectory(self) -> Trajectory:
        if not self.frames:
            return Trajectory(np.empty(0, np.int64), np.empty(0), np.empty((0, 0, 3)))
        cells = [c for c in self.cells if c is not None]
        return Trajectory(
            steps=np.array(self.steps, dtype=np.int64),
            times_ps=np.array(self.times_ps),
            positions_nm=np.stack(self.frames),
            cells_nm=np.stack(cells) if len(cells) == len(self.frames) else None,
        )


def read_trajectory(path: Path | str) -> Trajectory:
    """All frames of a finished trajectory, H5MD or extended XYZ by its suffix."""
    if is_live_format(path):
        tail = XyzTail(path)
        tail.poll()
        return tail.trajectory()
    return read_h5md(path)

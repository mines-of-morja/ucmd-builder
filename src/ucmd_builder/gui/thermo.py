"""Incremental reader of a growing thermo table (spec/run-config.md, design/B2a.md §7).

The engine writes one tab-separated line per observed step and flushes it. The reader keeps
its file offset and parses only complete new lines; columns are looked up by the names of
spec/run-config.md, never by position.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

BLOCK = 4000  # lines parsed at once: other threads get their turn in between


class ThermoTail:
    """The rows of `path` read so far; `poll()` adds the lines written since the last call."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.names: list[str] = []
        self._blocks: list[np.ndarray] = []
        self._offset = 0
        self._partial = b""

    def poll(self) -> int:
        """Read new complete lines; return how many rows were added."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return 0
        if size < self._offset:  # truncated, e.g. by a restart: read again from the start
            self.names, self._blocks, self._offset, self._partial = [], [], 0, b""
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        self._offset += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()  # empty if the data ended with a newline
        added, rows = 0, []
        for line in lines:
            if line.startswith(b"#"):
                added += self._add(rows)
                rows = []
                self.names = line.decode("utf-8").lstrip("# ").split("\t")
            else:
                rows.append(line)
                if len(rows) == BLOCK:
                    added += self._add(rows)
                    rows = []
        return added + self._add(rows)

    def _add(self, lines: list[bytes]) -> int:
        """The rows among these lines: the right number of columns, all of them numbers."""
        if not lines or not self.names:
            return 0
        width = len(self.names)
        if all(line.count(b"\t") == width - 1 for line in lines):
            try:
                block = np.array(b"\t".join(lines).split(b"\t"), dtype=float).reshape(-1, width)
            except ValueError:
                block = self._row_by_row(lines, width)
        else:
            block = self._row_by_row(lines, width)
        if len(block):
            self._blocks.append(block)
        return len(block)

    @staticmethod
    def _row_by_row(lines: list[bytes], width: int) -> np.ndarray:
        rows = []
        for line in lines:
            parts = line.split(b"\t")
            if len(parts) == width:
                try:
                    rows.append([float(p) for p in parts])
                except ValueError:
                    continue
        return np.array(rows).reshape(-1, width)

    def __len__(self) -> int:
        return sum(len(b) for b in self._blocks)

    def column(self, name: str) -> np.ndarray:
        """All values of a column so far (empty if the column does not exist)."""
        if name not in self.names or not self._blocks:
            return np.empty(0)
        if len(self._blocks) > 1:
            self._blocks = [np.concatenate(self._blocks)]
        return self._blocks[0][:, self.names.index(name)]


# Columns that an engine may leave without a value ("nan") in some rows: the OpenMM bridge
# evaluates the pressure less often than it writes the table. In any other column a nan is a
# fault and is shown as one.
SPARSE_COLUMNS = ("pressure_bar",)

SUMMARY_COLUMNS = ("temperature_K", "pressure_bar", "potential_kjmol", "total_kjmol", "volume_nm3")


def summary(tail: ThermoTail, from_fraction: float = 0.5) -> dict:
    """Means and standard deviations over the last half of the table, and the drift of H.

    The drift is the least-squares slope of the conserved energy over the same window, in
    kJ/mol/ps. A display aid for watching a run; analysis belongs to ucmd-analysis.
    A sparse column is averaged over the rows that have a value; `rows` says how many.
    """
    t = tail.column("time_ps")
    if len(t) == 0:
        return {}
    window = t >= t[-1] * from_fraction
    out: dict = {"window_ps": (float(t[window][0]), float(t[-1])), "rows": int(window.sum())}
    for name in SUMMARY_COLUMNS:
        values = tail.column(name)
        if len(values) == len(t):
            w = values[window]
            if name in SPARSE_COLUMNS:
                w = w[~np.isnan(w)]
            if not len(w):
                continue
            sd = float(w.std(ddof=1)) if len(w) > 1 else 0.0
            out[name] = {"mean": float(w.mean()), "sd": sd, "rows": len(w)}
    h = tail.column("conserved_energy_kjmol")
    if len(h) == len(t) and window.sum() > 1 and t[window][-1] > t[window][0]:
        out["drift_kjmol_per_ps"] = float(np.polyfit(t[window], h[window], 1)[0])
    return out

"""Incremental reader of a growing thermo table (spec/run-config.md, design/B2a.md §7).

The engine writes one tab-separated line per observed step and flushes it. The reader keeps
its file offset and parses only complete new lines; columns are looked up by the names of
spec/run-config.md, never by position.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class ThermoTail:
    """The rows of `path` read so far; `poll()` adds the lines written since the last call."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.names: list[str] = []
        self._rows: list[list[float]] = []
        self._offset = 0
        self._partial = b""

    def poll(self) -> int:
        """Read new complete lines; return how many rows were added."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return 0
        if size < self._offset:  # truncated, e.g. by a restart: read again from the start
            self.names, self._rows, self._offset, self._partial = [], [], 0, b""
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        self._offset += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()  # empty if the data ended with a newline
        added = 0
        for raw in lines:
            line = raw.decode("utf-8")
            if line.startswith("#"):
                self.names = line.lstrip("# ").split("\t")
                continue
            parts = line.split("\t")
            if not self.names or len(parts) != len(self.names):
                continue
            try:
                self._rows.append([float(p) for p in parts])
            except ValueError:
                continue
            added += 1
        return added

    def __len__(self) -> int:
        return len(self._rows)

    def column(self, name: str) -> np.ndarray:
        """All values of a column so far (empty if the column does not exist)."""
        if name not in self.names:
            return np.empty(0)
        k = self.names.index(name)
        return np.array([row[k] for row in self._rows])

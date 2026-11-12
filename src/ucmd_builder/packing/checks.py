"""Geometry checks under periodic boundary conditions (cubic box)."""

from __future__ import annotations

import numpy as np


class CellGrid:
    """A cell list for a cubic periodic box; cells are at least `cutoff` wide."""

    def __init__(self, box_nm: float, cutoff_nm: float) -> None:
        self.box = float(box_nm)
        self.cutoff = float(cutoff_nm)
        self.n = max(1, int(self.box // self.cutoff))
        self.width = self.box / self.n
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        self.points: list[np.ndarray] = []
        self.owner: list[int] = []

    def _cell(self, p: np.ndarray) -> tuple[int, int, int]:
        c = np.floor(np.mod(p, self.box) / self.width).astype(int) % self.n
        return (int(c[0]), int(c[1]), int(c[2]))

    def _neighbour_cells(self, cell: tuple[int, int, int]) -> set[tuple[int, int, int]]:
        r = range(-1, 2) if self.n >= 3 else range(self.n)
        if self.n >= 3:
            return {
                ((cell[0] + i) % self.n, (cell[1] + j) % self.n, (cell[2] + k) % self.n)
                for i in r
                for j in r
                for k in r
            }
        return {(i, j, k) for i in r for j in r for k in r}

    def add(self, point: np.ndarray, owner: int) -> None:
        self.cells.setdefault(self._cell(point), []).append(len(self.points))
        self.points.append(np.asarray(point, dtype=np.float64))
        self.owner.append(owner)

    def nearest(self, point: np.ndarray, exclude_owner: int | None = None) -> tuple[float, int]:
        """Distance to and index of the nearest stored point within the cutoff (inf, -1 if none)."""
        best, best_i = np.inf, -1
        for cell in self._neighbour_cells(self._cell(point)):
            for i in self.cells.get(cell, ()):
                if exclude_owner is not None and self.owner[i] == exclude_owner:
                    continue
                d = self.points[i] - point
                d -= self.box * np.round(d / self.box)
                r = float(np.sqrt(d @ d))
                if r < best:
                    best, best_i = r, i
        return (best, best_i) if best <= self.cutoff else (np.inf, -1)


def min_intermolecular_distance(
    positions_nm: np.ndarray, molecule_index: np.ndarray, box_nm: float, cutoff_nm: float
) -> tuple[float, int, int]:
    """Smallest distance between atoms of different molecules (minimum image).

    Returns (distance, i, j). If no pair is closer than `cutoff_nm`, returns (inf, -1, -1).
    """
    grid = CellGrid(box_nm, cutoff_nm)
    best, pair = np.inf, (-1, -1)
    for i, p in enumerate(positions_nm):
        d, j = grid.nearest(p, exclude_owner=int(molecule_index[i]))
        if d < best:
            best, pair = d, (j, i)
        grid.add(p, int(molecule_index[i]))
    return best, pair[0], pair[1]

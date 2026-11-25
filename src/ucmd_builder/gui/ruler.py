"""Distances and angles between picked atoms (design/B2c.md §3.2): a ruler, not an analysis.

Two picked atoms give a distance, three an angle (at the second). A table lists the atoms of one
element nearest to the last picked atom, sorted, without any cutoff. In a periodic cell the
minimum image is taken, in the cell of the shown frame. Nothing is written to a file.
"""

from __future__ import annotations

import itertools

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..constants import ANGSTROM_IN_NM
from .scene import Scene

NEAREST = 8
SHIFTS = np.array(list(itertools.product((-1, 0, 1), repeat=3)), dtype=float)


def minimum_image(vectors_nm: np.ndarray, cell_nm: np.ndarray | None) -> np.ndarray:
    """The shortest periodic image of every vector (n, 3); unchanged without a cell."""
    v = np.atleast_2d(np.asarray(vectors_nm, dtype=float))
    if cell_nm is None:
        return v
    fractional = v @ np.linalg.inv(cell_nm)
    v = v - np.round(fractional) @ cell_nm
    images = v[:, None, :] + (SHIFTS @ cell_nm)[None]  # a skewed cell: check the neighbours too
    best = np.argmin((images**2).sum(axis=2), axis=1)
    return images[np.arange(len(v)), best]


def distance_nm(x: np.ndarray, i: int, j: int, cell_nm: np.ndarray | None = None) -> float:
    return float(np.linalg.norm(minimum_image(x[j] - x[i], cell_nm)[0]))


def angle_deg(x: np.ndarray, i: int, j: int, k: int, cell_nm: np.ndarray | None = None) -> float:
    """The angle at j, in degrees."""
    a, b = minimum_image(np.stack([x[i] - x[j], x[k] - x[j]]), cell_nm)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0.0:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(a @ b / norm, -1.0, 1.0))))


def nearest(
    x: np.ndarray,
    i: int,
    candidates: np.ndarray,
    cell_nm: np.ndarray | None = None,
    count: int = NEAREST,
) -> list[tuple[int, float]]:
    """(atom, distance in nm) of the `count` candidates nearest to atom i, sorted."""
    candidates = candidates[candidates != i]
    if not len(candidates):
        return []
    d = np.linalg.norm(minimum_image(x[candidates] - x[i], cell_nm), axis=1)
    order = np.argsort(d, kind="stable")[:count]
    return [(int(candidates[k]), float(d[k])) for k in order]


class RulerPanel(QWidget):
    """The picked atoms, the distance or angle between them, and the nearest atoms."""

    changed = Signal(object)  # the picked atoms, to highlight

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scene: Scene | None = None
        self.positions: np.ndarray = np.empty((0, 3))
        self.cell: np.ndarray | None = None
        self.picked: list[int] = []
        self.measure = QCheckBox("Measure")
        hint = QLabel("Right-click two atoms for a distance, three for an angle.")
        hint.setWordWrap(True)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.result = QLabel("–")
        self.result.setWordWrap(True)
        self.element = QComboBox()
        self.element.currentIndexChanged.connect(lambda _i: self.refresh())
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("atom", "molecule", "nm", "Å"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        top = QHBoxLayout()
        top.addWidget(self.measure, 1)
        top.addWidget(clear)
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Nearest"))
        pick.addWidget(self.element)
        pick.addWidget(QLabel("to the last picked atom"), 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(hint)
        layout.addWidget(self.result)
        layout.addLayout(pick)
        layout.addWidget(self.table, 1)

    def set_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.positions, self.cell = scene.positions_nm, scene.cell_nm
        self.picked = []
        self.element.blockSignals(True)
        self.element.clear()
        self.element.addItems(sorted(set(scene.elements)))
        oxygen = self.element.findText("O")
        self.element.setCurrentIndex(oxygen if oxygen >= 0 else 0)
        self.element.blockSignals(False)
        self.refresh()

    def set_positions(self, positions_nm: np.ndarray, cell_nm: np.ndarray | None = None) -> None:
        """A trajectory frame; its own cell, or the scene's."""
        if self.scene is None or len(positions_nm) != self.scene.n_atoms:
            return
        self.positions = positions_nm
        self.cell = self.scene.cell_nm if cell_nm is None else cell_nm
        if self.picked:
            self.refresh()

    def pick(self, atom: int) -> None:
        """One more atom while measuring (a fourth starts again), else only this one."""
        if self.measure.isChecked() and len(self.picked) < 3 and atom not in self.picked:
            self.picked = [*self.picked, atom]
        else:
            self.picked = [atom]
        self.refresh()
        self.changed.emit(list(self.picked))

    def clear(self) -> None:
        self.picked = []
        self.refresh()
        self.changed.emit([])

    def _name(self, i: int) -> str:
        assert self.scene is not None
        return f"{self.scene.elements[i]}{i}"

    def refresh(self) -> None:
        self.table.setRowCount(0)
        if self.scene is None or not self.picked:
            self.result.setText("–")
            return
        x, cell, p = self.positions, self.cell, self.picked
        name = "–".join(self._name(i) for i in p)
        if len(p) == 2:
            d = distance_nm(x, *p, cell)
            self.result.setText(f"{name}: {d:.6f} nm = {d / ANGSTROM_IN_NM:.5f} Å")
        elif len(p) == 3:
            self.result.setText(f"{name}: {angle_deg(x, *p, cell):.3f}°")
        else:
            self.result.setText(f"{name} picked")
        chosen = self.element.currentText()
        candidates = np.flatnonzero(np.array([e == chosen for e in self.scene.elements]))
        rows = nearest(x, p[-1], candidates, cell)
        self.table.setRowCount(len(rows))
        for row, (atom, d) in enumerate(rows):
            info = self.scene.atom(atom)
            cells = (
                self._name(atom),
                f"{info.molecule_type} #{info.molecule}",
                f"{d:.4f}",
                f"{d / ANGSTROM_IN_NM:.3f}",
            )
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))

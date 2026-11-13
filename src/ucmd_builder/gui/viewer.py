"""The 3D view (design/B2a.md §4; ADR 0006): a small protocol and its PyVista implementation.

Atoms are spheres instanced on the GPU (`vtkGlyph3DMapper`): they scale with the zoom like
geometric glyphs, and playback only moves points (about 17 ms per frame for 7000 atoms on the
workstation). Bonds are lines that share the atom positions; the cell is a wireframe. Molecules
are drawn whole, with their centre in the cell (`Scene.display_positions`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import pyvista as pv
import vtk

from .elements import color, radius_nm
from .scene import Scene

BACKGROUND = "white"
BOND_COLOR = "#7f7f7f"
CELL_COLOR = "#303030"
HIGHLIGHT_COLOR = "#ffd700"


class Viewer(Protocol):
    """What the GUI needs from a 3D view; another viewer can implement the same protocol."""

    def show_scene(self, scene: Scene) -> None: ...

    def show_positions(self, positions_nm: np.ndarray) -> None: ...

    def highlight(self, atoms: Sequence[int]) -> None: ...

    def on_pick(self, callback: Callable[[int], None]) -> None: ...

    def screenshot(self, path: Path | str) -> None: ...


def cell_wireframe(cell_nm: np.ndarray) -> pv.PolyData:
    """The 12 edges of the cell spanned by the rows of `cell_nm`."""
    a, b, c = cell_nm
    corners = np.array([i * a + j * b + k * c for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    edges = [
        (p, q)
        for p in range(8)
        for q in range(p + 1, 8)
        if bin(p ^ q).count("1") == 1  # corners that differ in one lattice direction
    ]
    lines = np.array([[2, p, q] for p, q in edges]).ravel()
    return pv.PolyData(corners, lines=lines)


def _spheres(points: pv.PolyData) -> vtk.vtkActor:
    """Instanced spheres at the cloud's points, scaled by its `radius`, coloured by `colors`."""
    source = vtk.vtkSphereSource()
    source.SetThetaResolution(16)
    source.SetPhiResolution(12)
    source.SetRadius(1.0)
    mapper = vtk.vtkGlyph3DMapper()
    mapper.SetInputData(points)
    mapper.SetSourceConnection(source.GetOutputPort())
    mapper.SetScaleArray("radius")
    mapper.SetScaleModeToScaleByMagnitude()
    mapper.SetScaling(True)
    mapper.SetScalarModeToUsePointFieldData()
    mapper.SelectColorArray("colors")
    mapper.SetColorModeToDirectScalars()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    return actor


class SceneActors:
    """Draws a `Scene` into any PyVista plotter and moves its atoms."""

    def __init__(self, plotter: pv.BasePlotter) -> None:
        self.plotter = plotter
        self.scene: Scene | None = None
        self.points: np.ndarray = np.empty((0, 3))
        self._cloud: pv.PolyData | None = None
        self._bonds: pv.PolyData | None = None
        self._highlight: vtk.vtkActor | None = None
        plotter.set_background(BACKGROUND)

    def show(self, scene: Scene) -> None:
        self.plotter.clear_actors()
        self._highlight = None
        self.scene = scene
        self.points = scene.display_positions()
        cloud = pv.PolyData(self.points)
        cloud["radius"] = np.array([radius_nm(e) for e in scene.elements])
        cloud["colors"] = np.array([color(e) for e in scene.elements], dtype=np.uint8)
        self._cloud = cloud
        self.plotter.add_actor(_spheres(cloud), name="atoms", reset_camera=False)
        if len(scene.bonds):
            lines = np.column_stack([np.full(len(scene.bonds), 2), scene.bonds]).ravel()
            self._bonds = pv.PolyData(self.points.copy(), lines=lines)
            self.plotter.add_mesh(
                self._bonds, color=BOND_COLOR, line_width=2, name="bonds", reset_camera=False
            )
        else:
            self._bonds = None
        if scene.cell_nm is not None:
            self.plotter.add_mesh(
                cell_wireframe(scene.cell_nm),
                color=CELL_COLOR,
                line_width=1,
                name="cell",
                reset_camera=False,
            )
        self.plotter.reset_camera()
        self.plotter.render()

    def move(self, positions_nm: np.ndarray) -> None:
        """Show other positions of the same atoms (a trajectory frame)."""
        if self.scene is None or self._cloud is None:
            return
        self.points = self.scene.display_positions(positions_nm)
        self._cloud.points = self.points
        if self._bonds is not None:
            self._bonds.points = self.points
        self.plotter.render()

    def highlight(self, atoms: Sequence[int]) -> None:
        if self._highlight is not None:
            self.plotter.remove_actor(self._highlight)
            self._highlight = None
        if self.scene is None or len(atoms) == 0:
            self.plotter.render()
            return
        marked = pv.PolyData(self.points[list(atoms)])
        marked["radius"] = np.array([1.4 * radius_nm(self.scene.elements[i]) for i in atoms])
        marked["colors"] = np.tile(np.array(color("S"), dtype=np.uint8), (len(atoms), 1))
        actor = _spheres(marked)
        actor.GetProperty().SetOpacity(0.5)
        self._highlight = actor
        self.plotter.add_actor(actor, name="highlight", reset_camera=False)
        self.plotter.render()

    def nearest_atom(self, point: Sequence[float]) -> int | None:
        if len(self.points) == 0:
            return None
        return int(np.argmin(np.linalg.norm(self.points - np.asarray(point), axis=1)))


def make_qt_viewer(parent=None):
    """The 3D view widget of the main window (needs a display or a GL context)."""
    from pyvistaqt import QtInteractor

    class PyVistaViewer(QtInteractor):
        """The Viewer protocol on a pyvistaqt `QtInteractor`; right-click picks an atom."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.actors_ = SceneActors(self)
            self._pick: Callable[[int], None] | None = None
            self.enable_point_picking(
                callback=self._picked,
                show_message=False,
                show_point=False,
                use_picker=True,
                left_clicking=False,
            )

        def _picked(self, point, *_args) -> None:
            atom = self.actors_.nearest_atom(point)
            if atom is not None and self._pick is not None:
                self._pick(atom)

        def show_scene(self, scene: Scene) -> None:
            self.actors_.show(scene)

        def show_positions(self, positions_nm: np.ndarray) -> None:
            self.actors_.move(positions_nm)

        def highlight(self, atoms: Sequence[int]) -> None:
            self.actors_.highlight(atoms)

        def on_pick(self, callback: Callable[[int], None]) -> None:
            self._pick = callback

        def screenshot(self, path: Path | str) -> None:  # type: ignore[override]
            super().screenshot(str(path))

    return PyVistaViewer(parent)

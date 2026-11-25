"""The main window of the GUI preview (design/B2a.md §3–§7).

Every action is an edit of the recipe through `RecipeDocument`, or a call of a library function
(`build`, `write_package`, `to_toml`). The run preview starts the engine as an external program.
File dialogs only pick paths; the methods that do the work take paths, so tests call them
directly.
"""

from __future__ import annotations

import locale
import sys
import time
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..build import BuiltSystem
from ..package import system_document, write_package
from ..recipe import RecipeError, to_toml
from .build_worker import BuildWorker, start_build
from .components import ComponentsPanel
from .composition import CompositionDialog
from .document import RecipeDocument
from .molecule_file import MoleculeFileError, read_structures
from .notebook import Notebook
from .panels import LogPanel, PropertiesPanel
from .ruler import RulerPanel
from .run_preview import RunBrowser, RunController, RunDialog, RunPanel, write_run
from .runs import RunInfo, RunSettings, new_run_dir, preview_cutoff, run_outputs, scan_runs
from .scene import Scene, read_package
from .table_file import TableError, is_table
from .viewer import Viewer, make_qt_viewer

TITLE = "UnitcellMD builder"


def use_c_numeric_locale() -> None:
    """Numbers in files are C-formatted. On Linux, Qt sets the process locale from the system;
    a German one makes C's strtod read "0.89" as 0, which silently breaks RDKit's XYZ reader."""
    locale.setlocale(locale.LC_NUMERIC, "C")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MainWindow(QMainWindow):
    """One window (design/B2d.md): a notebook in the middle, panels that follow its page.

    Left: components. Middle: the 3D view with its playback bar, the plots of a run, result
    tables. Right, beside the 3D view only: properties and ruler. Below: the log, opened by a
    warning.
    """

    def __init__(self, viewer: Viewer | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        use_c_numeric_locale()  # also when someone else created the QApplication
        self.document = RecipeDocument(self)
        self.viewer = viewer if viewer is not None else make_qt_viewer(self)
        self.built: BuiltSystem | None = None
        self.scene: Scene | None = None
        self.package_path: Path | None = None
        self._thread: QThread | None = None
        self._worker: BuildWorker | None = None
        self.run: RunController | None = None
        self.run_dir: Path | None = None
        self.shown = ""  # what the 3D view shows, for the title
        self._late_frame: tuple | None = None  # a frame that came while another page was in front
        self._side: list[bool] | None = None  # which side panels were open beside the 3D view
        self.run_panel = RunPanel()
        self.view_page = QWidget()
        column = QVBoxLayout(self.view_page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        if isinstance(self.viewer, QWidget):
            column.addWidget(self.viewer, 1)
        else:
            column.addStretch(1)
        column.addWidget(self.run_panel.playback)
        # in a scroll area: the plots must not force the height of the window
        self.plots_page = QScrollArea()
        self.plots_page.setWidget(self.run_panel)
        self.plots_page.setWidgetResizable(True)
        self.plots_page.setFrameShape(QScrollArea.Shape.NoFrame)
        self.plots_page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.notebook = Notebook()
        self.notebook.add_fixed(self.view_page, "3D view")
        self.notebook.add_fixed(self.plots_page, "Plots")
        self.notebook.set_page_visible(self.plots_page, False)
        self.notebook.currentChanged.connect(self._page_changed)
        self.run_panel.run_shown.connect(
            lambda on: self.notebook.set_page_visible(self.plots_page, on)
        )
        self.setCentralWidget(self.notebook)
        self.log = LogPanel()
        self.properties = PropertiesPanel()
        self.components = ComponentsPanel(self.document)
        self.components_dock = self._dock("Components", self.components, "left")
        self.properties_dock = self._dock("Properties", self.properties, "right")
        self.ruler = RulerPanel()
        self.ruler_dock = self._dock("Ruler", self.ruler, "right")
        self.tabifyDockWidget(self.properties_dock, self.ruler_dock)
        self.properties_dock.raise_()
        self.log_dock = self._dock("Log", self.log, "bottom")
        self.log_dock.hide()
        self.log.added.connect(self._log_added)
        self.ruler.changed.connect(self.viewer.highlight)
        self.run_panel.frame.connect(self._show_frame)
        self.run_panel.cancel_requested.connect(self.stop_run)
        self.run_panel.structure.connect(self._show_structure)
        self._actions()
        self.document.changed.connect(self._recipe_changed)
        self.document.path_changed.connect(lambda _p: self._update_title())
        self.document.undo_stack.cleanChanged.connect(self._clean_changed)
        self.components.message.connect(self.log.warn)
        self.components.selected.connect(self._component_selected)
        self.components.composition_requested.connect(self.edit_composition)
        self.viewer.on_pick(self._atom_picked)
        self._update_title()
        self._update_actions()
        self.statusBar().showMessage("Add components, then Build. Right-click an atom for details.")
        self._default_layout = self.saveState()
        self.resize(1400, 900)
        self._size_docks()

    def _size_docks(self) -> None:
        self.resizeDocks(
            [self.components_dock, self.ruler_dock], [240, 300], Qt.Orientation.Horizontal
        )
        self.resizeDocks([self.log_dock], [110], Qt.Orientation.Vertical)

    def reset_layout(self) -> None:
        """Every panel back to its place and size."""
        self._side = None
        self.restoreState(self._default_layout)
        self._size_docks()
        self._page_changed()

    def _page_changed(self, _index: int = 0) -> None:
        """Properties and ruler belong to the 3D view; a frame it missed is shown now."""
        panels = (self.properties_dock, self.ruler_dock)
        in_front = self.notebook.currentWidget() is self.view_page
        if not in_front and self._side is None:
            self._side = [not d.isHidden() for d in panels]
            for dock in panels:
                dock.hide()
        elif in_front and self._side is not None:
            for dock, was_open in zip(panels, self._side, strict=True):
                dock.setVisible(was_open)
            self._side = None
        if in_front and self._late_frame is not None:
            frame, self._late_frame = self._late_frame, None
            self._draw_frame(*frame)

    def _log_added(self, text: str, warning: bool) -> None:
        self.statusBar().showMessage(text)
        if warning:
            self.log_dock.show()

    def _set_shown(self, text: str) -> None:
        self.shown = text
        self.shown_label.setText(text)
        self.shown_label.setToolTip(text)
        self._update_title()
        # components of a recipe, or of the one to be made; not next to somebody's results
        self.components_dock.setVisible(bool(self.document.recipe.components) or not text)

    def fit_to_screen(self) -> None:
        """Never larger than the screen the window opens on."""
        screen = self.screen().availableGeometry()
        self.resize(min(self.width(), screen.width()), min(self.height(), screen.height()))

    # -- layout and actions --------------------------------------------------------------------

    def _dock(self, title: str, widget: QWidget, where: str) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        area = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "right": Qt.DockWidgetArea.RightDockWidgetArea,
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
        }[where]
        self.addDockWidget(area, dock)
        return dock

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    def _actions(self) -> None:
        self.act_new = self._action("New", self.new_recipe, QKeySequence.StandardKey.New)
        self.act_open = self._action(
            "Open recipe…", self._open_dialog, QKeySequence.StandardKey.Open
        )
        self.act_save = self._action("Save", self._save, QKeySequence.StandardKey.Save)
        self.act_save_as = self._action("Save as…", self._save_as, QKeySequence.StandardKey.SaveAs)
        self.act_open_package = self._action("Open package…", self._open_package_dialog)
        self.act_build = self._action("Build", self.start_build, "Ctrl+B")
        self.act_cancel = self._action("Cancel", self.cancel_build)
        self.act_export = self._action("Export package…", self._export_dialog, "Ctrl+E")
        self.act_run = self._action("Run…", self._run_dialog, "Ctrl+R")
        self.act_stop = self._action("Stop run", self.stop_run)
        self.act_open_run = self._action("Open run…", self._open_run_dialog)
        self.act_open_molecule = self._action("Open molecule…", self._open_molecule_dialog)
        self.act_open_tables = self._action("Open tables…", self._open_tables_dialog)
        self.act_images = self._action("Periodic images", self.viewer.show_images, "Ctrl+I")
        self.act_images.setCheckable(True)
        self.act_undo = self.document.undo_stack.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = self.document.undo_stack.createRedoAction(self, "Redo")
        self.act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        menu = self.menuBar().addMenu("&File")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_open_package)
        menu.addAction(self.act_open_run)
        menu.addAction(self.act_open_molecule)
        menu.addAction(self.act_open_tables)
        menu.addAction(self.act_export)
        menu = self.menuBar().addMenu("&Edit")
        menu.addAction(self.act_undo)
        menu.addAction(self.act_redo)
        menu = self.menuBar().addMenu("&Build")
        menu.addAction(self.act_build)
        menu.addAction(self.act_cancel)
        menu = self.menuBar().addMenu("&View")
        menu.addAction(self.act_images)
        menu.addSeparator()
        for dock in self.findChildren(QDockWidget):
            menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        menu.addAction(self._action("Reset layout", self.reset_layout))
        menu = self.menuBar().addMenu("&Run")
        menu.addAction(self.act_run)
        menu.addAction(self.act_stop)
        menu.addAction(self.act_open_run)
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("Main")
        for a in (self.act_new, self.act_open, self.act_save):
            toolbar.addAction(a)
        toolbar.addSeparator()
        toolbar.addAction(self.act_undo)
        toolbar.addAction(self.act_redo)
        toolbar.addSeparator()
        toolbar.addAction(self.act_build)
        toolbar.addAction(self.act_cancel)
        toolbar.addSeparator()
        toolbar.addAction(self.act_export)
        toolbar.addAction(self.act_run)
        toolbar.addAction(self.act_stop)
        toolbar.addAction(self.act_open_run)
        toolbar.addSeparator()
        toolbar.addAction(self.act_images)
        self.shown_label = QLabel()
        self.shown_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.shown_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(self.shown_label)
        self.toolbar = toolbar

    def _clean_changed(self, clean: bool) -> None:
        # also emitted while the window is being destroyed: then do nothing
        if shiboken6.isValid(self):
            self._update_title(modified=not clean)

    def _update_title(self, modified: bool | None = None) -> None:
        recipe = self.document.recipe
        if modified is None:
            modified = self.document.modified
        star = "*" if modified else ""
        where = f" ({self.document.path})" if self.document.path else ""
        name = f"{recipe.name}{star}{where}"
        mine = recipe.components or self.document.path or modified or not self.shown
        parts = ([self.shown] if self.shown else []) + ([name] if mine else [])
        self.setWindowTitle(f"{' · '.join(parts)} — {TITLE}")

    def _update_actions(self) -> None:
        building = self._worker is not None
        self.act_build.setEnabled(not building and bool(self.document.recipe.components))
        self.act_cancel.setEnabled(building)
        self.act_export.setEnabled(not building and self.built is not None)
        runnable = self.package_path is not None or self.built is not None
        self.act_run.setEnabled(not building and not self.is_running() and runnable)
        self.act_stop.setEnabled(self.is_running())
        self.act_open_run.setEnabled(not building and not self.is_running())
        self.act_open_molecule.setEnabled(not building and not self.is_running())

    # -- recipe --------------------------------------------------------------------------------

    def _recipe_changed(self, recipe) -> None:
        self._update_title()
        if recipe.components:
            self.components_dock.show()
        self._update_actions()

    def new_recipe(self) -> None:
        if self._discard_ok():
            self.document.new()
            self.components_dock.show()
            self.log.add("new recipe")

    def open_recipe(self, path: Path | str) -> bool:
        try:
            self.document.open(path)
        except RecipeError as e:
            self.log.warn(f"cannot open {path}: {e}")
            return False
        self.log.add(f"opened {path}")
        return True

    def save_recipe(self, path: Path | str | None = None) -> Path | None:
        try:
            saved = self.document.save(path)
        except (RecipeError, OSError) as e:
            self.log.warn(f"not saved: {e}")
            return None
        self.log.add(f"saved {saved}")
        return saved

    def edit_composition(self) -> None:
        if len(self.document.recipe.components) < 3:
            self.log.add("a composition needs a cation, an anion and at least one solvent")
            return
        dialog = CompositionDialog(self.document.recipe, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.components.apply(dialog.result_recipe, "composition")

    def _open_dialog(self) -> None:
        if not self._discard_ok():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open recipe", "", "Recipes (*.toml)")
        if path:
            self.open_recipe(path)

    def _save(self) -> None:
        if self.document.path is None:
            self._save_as()
        else:
            self.save_recipe()

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save recipe", f"{self.document.recipe.name}.toml", "Recipes (*.toml)"
        )
        if path:
            self.save_recipe(path)

    def choose_run(self, folder: Path, runs: list[RunInfo]) -> RunInfo | None:
        """Let the user pick one of several runs (tests replace it)."""
        dialog = RunBrowser(folder, runs, self)
        return dialog.chosen() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def ask(self, question: str) -> bool:
        """A yes/no question to the user (tests replace it)."""
        return QMessageBox.question(self, TITLE, question) == QMessageBox.StandardButton.Yes

    def _discard_ok(self) -> bool:
        return not self.document.modified or self.ask(
            "The recipe has unsaved changes. Discard them?"
        )

    # -- build ---------------------------------------------------------------------------------

    def start_build(self) -> None:
        if self._worker is not None:
            return
        self.log.add(f"building {self.document.recipe.name} …")
        self._thread, self._worker = start_build(self.document.recipe, self)
        self._worker.progress.connect(self.log.add)
        self._worker.built.connect(self._built)
        self._worker.failed.connect(self._build_failed)
        self._thread.finished.connect(self._build_done)
        self._update_actions()

    def cancel_build(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.log.add("cancelling the build …")

    def is_building(self) -> bool:
        return self._worker is not None

    def _built(self, built: BuiltSystem) -> None:
        self.built = built
        self.run_panel.close_structures()
        self.package_path = None
        names = ", ".join(
            f"{n} {m.name}" for m, n in zip(built.molecules, built.counts, strict=True)
        )
        self.log.add(f"built: {built.atom_count} atoms ({names}), box {built.box_nm:.4f} nm")
        if "notes" in built.provenance:
            self.log.add(f"note: {built.provenance['notes']}")
        self.show_scene(Scene.from_document(system_document(built, utc_now()), built.positions_nm))
        self._set_shown(f"{self.document.recipe.name}, built · {built.atom_count} atoms")

    def _build_failed(self, reason: str) -> None:
        self.log.warn(f"build failed: {reason}")

    def _build_done(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._thread, self._worker = None, None
        self._update_actions()

    # -- packages ------------------------------------------------------------------------------

    def show_scene(self, scene: Scene) -> None:
        self.scene = scene
        self._late_frame = None
        self.ruler.set_scene(scene)
        self.notebook.show_fixed()
        self.viewer.show_scene(scene)
        self._update_actions()

    def _show_structure(self, structure) -> None:
        self.show_scene(structure.scene)
        self._set_shown(f"{structure.name} · {structure.scene.n_atoms} atoms · bonds guessed")

    def _show_frame(self, positions_nm, cell_nm) -> None:
        self.ruler.set_positions(positions_nm, cell_nm)
        if self.notebook.currentWidget() is self.view_page:
            self._draw_frame(positions_nm, cell_nm)
        else:  # nobody sees it: drawn when the page is in front again
            self._late_frame = (positions_nm, cell_nm)

    def _draw_frame(self, positions_nm, cell_nm) -> None:
        self.viewer.show_positions(positions_nm, cell_nm)
        if self.ruler.picked:  # the marks move with the atoms
            self.viewer.highlight(self.ruler.picked)

    def open_package(self, path: Path | str) -> bool:
        """Show a package read-only."""
        try:
            scene = read_package(path)
        except (OSError, ValueError, KeyError) as e:
            self.log.warn(f"cannot open package {path}: {e}")
            return False
        self.built = None
        self.run_panel.close_structures()
        self.package_path = Path(path)
        self.show_scene(scene)
        self._set_shown(f"{self.package_path.name} · {scene.n_atoms} atoms")
        self.log.add(f"opened package {path}: {scene.n_atoms} atoms (read-only)")
        return True

    def export_package(self, path: Path | str, overwrite: bool = False) -> Path | None:
        """Write the built system as a validated package, and the recipe next to it."""
        if self.built is None:
            self.log.warn("nothing to export: build first")
            return None
        path = Path(path)
        try:
            out = write_package(self.built, path, overwrite=overwrite, created_utc=utc_now())
        except Exception as e:  # validation failures and I/O errors go to the log
            self.log.warn(f"export failed: {type(e).__name__}: {e}")
            return None
        recipe_file = out.parent / f"{out.stem}.recipe.toml"
        recipe_file.write_text(to_toml(self.document.recipe), encoding="utf-8")
        self.package_path = out
        self.log.add(f"exported {out} and {recipe_file.name}")
        return out

    def _open_package_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open package (*.ucsys)")
        if path:
            self.open_package(path)

    def _export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export package", f"{self.document.recipe.name}.ucsys", "Packages (*.ucsys)"
        )
        if not path:
            return
        overwrite = False
        if Path(path).exists():
            if not self.ask(f"{path} exists. Replace it?"):
                return
            overwrite = True
        self.export_package(path, overwrite=overwrite)

    # -- run preview ---------------------------------------------------------------------------

    def is_running(self) -> bool:
        return self.run is not None

    def _run_dialog(self) -> None:
        if self.package_path is None and self.built is not None:
            # The engine reads packages only: export the built system first.
            self.log.add("the engine runs a package: choose where to export the built system")
            self._export_dialog()
        if self.package_path is None or self.scene is None:
            self.log.add("build (and export) a system, or open a package, first")
            return
        dialog = RunDialog(self.package_path, self.scene, self.ask, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start_run(dialog.settings())

    def start_run(self, settings: RunSettings) -> Path | None:
        """Write the run directory next to the package and start the engine."""
        from ucmd_spec.run_config import validate_run_config

        from ..package import _spec_dir
        from ..profile import executable

        if self.package_path is None or self.scene is None or self.is_running():
            return None
        ucmd = executable("ucmd")
        if ucmd is None:
            self.log.warn("no engine: set [executables] ucmd in the machine profile (Run preview…)")
            return None
        try:
            cutoff, warning = preview_cutoff(self.scene.cell_nm)
        except (TypeError, ValueError) as e:
            self.log.warn(f"cannot run: {e}")
            return None
        if warning:
            self.log.add(warning)
        run_dir = new_run_dir(self.package_path)
        write_run(self.package_path, run_dir, settings, cutoff)
        with _spec_dir():
            report = validate_run_config(run_dir / "run.toml")
        if not report.ok:  # a builder bug: never start the engine on an invalid file
            for issue in report.issues:
                self.log.add(f"run.toml: {issue.code} {issue.message}")
            return None
        self.run_dir = run_dir
        self.run = RunController(ucmd, run_dir, settings.minimize, self)
        self.run.line.connect(self.log.add)
        self.run.stage.connect(lambda stage: self.statusBar().showMessage(f"engine: {stage}"))
        self.run.finished.connect(self._run_finished)
        self.run_panel.begin(run_outputs(run_dir))
        self._set_shown(f"run {run_dir.name} of {self.package_path.name}")
        self.run.start()
        self._update_actions()
        return run_dir

    def stop_run(self) -> None:
        if self.run is not None:
            self.run.cancel()

    def _run_finished(self, code: int, message: str) -> None:
        (self.log.add if code == 0 else self.log.warn)(f"run {message}")
        if self.run_dir is not None:
            self.run_panel.end(message)
        self.statusBar().showMessage(f"run {message}")
        self.run = None
        self._update_actions()

    def open_run(self, path: Path | str) -> bool:
        """Show a run: its package, its thermo plots and its trajectory to play.

        `path` is a run folder or its run.toml. Any other folder (a folder of runs, a study,
        `examples/EC/runs`) or a package is searched for runs: one is opened directly, of
        several the user chooses (`choose_run`). The run may come from the GUI, a study or
        `ucmd run`. A run short of its last step is followed: plots and XYZ frames keep growing.
        """
        if self.is_running():
            self.log.warn("a run is going; stop it before opening another")
            return False
        path = Path(path)
        if not (path.is_file() or (path / "run.toml").is_file()):
            runs = scan_runs(path) if path.is_dir() else []
            if not runs:
                self.log.warn(f"cannot open run: no run.toml in or below {path}")
                return False
            chosen = runs[0] if len(runs) == 1 else self.choose_run(path, runs)
            if chosen is None:
                return False
            path = chosen.run_toml
        try:
            outputs = run_outputs(path)
        except (OSError, ValueError) as e:
            self.log.warn(f"cannot open run: {e}")
            return False
        if not outputs.system.is_dir():
            self.log.warn(
                f"cannot open run {path}: its package {outputs.system} does not exist "
                "(for a GUI run: did the minimization finish?)"
            )
            return False
        if not self.open_package(outputs.system):
            return False
        self.run_dir = outputs.run_dir
        self._set_shown(outputs.title)
        self.run_panel.show_run(outputs)
        trajectory = self.run_panel.trajectory
        if trajectory is not None and len(trajectory) and self.scene is not None:
            if trajectory.positions_nm.shape[1] != self.scene.n_atoms:
                self.log.warn(
                    f"the trajectory has {trajectory.positions_nm.shape[1]} atoms, the package "
                    f"{self.scene.n_atoms}: not played"
                )
                self.run_panel.trajectory = None
                self.run_panel.slider.setEnabled(False)
                self.run_panel.play_button.setEnabled(False)
                return False
        frames = len(trajectory) if trajectory is not None else 0
        rows = len(self.run_panel.tail) if self.run_panel.tail is not None else 0
        following = "; following it while it grows" if self.run_panel.following else ""
        self.log.add(
            f"opened run {outputs.run_dir}: {rows} thermo rows, {frames} frames{following}"
        )
        return True

    def _open_run_dialog(self) -> None:
        start = ""
        if self.package_path is not None:
            runs = self.package_path.parent / f"{self.package_path.name}.runs"
            start = str(runs if runs.is_dir() else self.package_path.parent)
        path = QFileDialog.getExistingDirectory(
            self, "Open run: a run folder, or any folder with runs below it", start
        )
        if path:
            self.open_run(path)

    def open_molecules(self, paths: list[Path | str]) -> bool:
        """Show molecule files (XYZ): structures to look at, never to run or export."""
        if self.is_running() or self.is_building():
            self.log.warn("wait for the build or stop the run before opening a molecule file")
            return False
        structures = []
        for path in paths:
            try:
                structures += read_structures(path)
            except (OSError, MoleculeFileError) as e:
                self.log.warn(f"cannot open {path}: {e}")
                return False
        if not structures:
            return False
        self.built = None
        self.package_path = None
        self.run_dir = None
        self.run_panel.show_structures(structures)
        self.ruler_dock.raise_()
        files = "file" if len(structures) == 1 else "files"
        frames = sum(len(s.trajectory) for s in structures)
        self.log.add(
            f"opened {len(structures)} molecule {files}, {frames} frames; bonds are guessed from "
            "distances (display only)"
        )
        self._update_actions()
        return True

    def _open_molecule_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open molecule", "", "XYZ files (*.xyz)")
        if paths:
            self.open_molecules(paths)

    def open_tables(self, paths: list[Path | str]) -> bool:
        """Show result tables, each on a page of the notebook."""
        ok = bool(paths)
        for path in paths:
            try:
                page = self.notebook.add_table(path)
            except (OSError, TableError) as e:
                self.log.warn(f"cannot open table {path}: {e}")
                ok = False
                continue
            self.log.add(f"opened table {path}: {len(page.table.rows)} rows")
        return ok

    def _open_tables_dialog(self) -> None:
        start = str(self.run_dir) if self.run_dir is not None else ""
        paths, _ = QFileDialog.getOpenFileNames(self, "Open tables", start, "Tables (*.tsv)")
        if paths:
            self.open_tables(paths)

    def open_paths(self, paths: list[Path]) -> None:
        """Open everything named on the command line; molecule files together, as one list."""
        molecules = [p for p in paths if p.suffix == ".xyz"]
        if molecules:
            self.open_molecules(molecules)
        for path in paths:
            if path not in molecules:
                self.open_path(path)

    def open_path(self, path: Path | str) -> bool:
        """Open what `path` is: a run (run.toml or its folder), a package, a molecule file
        (.xyz), a result table (.tsv) or a folder of them, or a recipe."""
        path = Path(path)
        if path.suffix == ".tsv" and path.is_file():
            return self.open_tables([path])
        if path.is_dir() and not (path / "run.toml").is_file():
            tables = sorted(p for p in path.glob("*.tsv") if is_table(p))
            if tables:
                return self.open_tables(tables)
        if path.suffix == ".xyz" and path.is_file():
            return self.open_molecules([path])
        if (path / "system.json").is_file():
            return self.open_package(path)
        if path.name == "run.toml" or path.is_dir():
            return self.open_run(path)
        return self.open_recipe(path)

    # -- selection -----------------------------------------------------------------------------

    def _component_selected(self, name: str) -> None:
        self.properties.show_component(self.document.recipe, name)
        if self.scene is not None:
            self.viewer.highlight(self.scene.atoms_of(name).tolist())

    def _atom_picked(self, index: int) -> None:
        if self.scene is None:
            return
        self.properties.show_atom(self.scene.atom(index))
        self.ruler.pick(index)  # highlights what it holds

    def closeEvent(self, event) -> None:
        if self.is_running() and not self.ask("A run is going. Stop it and quit?"):
            event.ignore()
            return
        if not self._discard_ok():
            event.ignore()
            return
        if self._worker is not None:
            self._worker.cancel()
            if self._thread is not None:
                self._thread.wait()
        if self.run is not None and self.run.process is not None:
            process = self.run.process
            self.run.cancel()
            process.waitForFinished(15_000)
        event.accept()


def run_app(argv: list[str]) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    use_c_numeric_locale()
    window = MainWindow()
    window.open_paths([Path(a) for a in argv])
    window.fit_to_screen()
    window.show()
    return app.exec()

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
    QMainWindow,
    QMessageBox,
    QWidget,
)

from ..build import BuiltSystem
from ..package import system_document, write_package
from ..recipe import RecipeError, to_toml
from .build_worker import BuildWorker, start_build
from .components import ComponentsPanel
from .composition import CompositionDialog
from .document import RecipeDocument
from .panels import LogPanel, PropertiesPanel
from .run_preview import RunController, RunDialog, RunPanel, write_run
from .runs import RunSettings, new_run_dir, preview_cutoff
from .scene import Scene, read_package
from .viewer import Viewer, make_qt_viewer

TITLE = "UnitcellMD builder"


def use_c_numeric_locale() -> None:
    """Numbers in files are C-formatted. On Linux, Qt sets the process locale from the system;
    a German one makes C's strtod read "0.89" as 0, which silently breaks RDKit's XYZ reader."""
    locale.setlocale(locale.LC_NUMERIC, "C")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MainWindow(QMainWindow):
    """Components | 3D view | properties, with the log below."""

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
        if isinstance(self.viewer, QWidget):
            self.setCentralWidget(self.viewer)
        self.log = LogPanel()
        self.properties = PropertiesPanel()
        self.components = ComponentsPanel(self.document)
        self._dock("Components", self.components, "left")
        properties_dock = self._dock("Properties", self.properties, "right")
        self._dock("Log", self.log, "bottom")
        self.run_panel = RunPanel()
        run_dock = self._dock("Run", self.run_panel, "right")
        self.splitDockWidget(properties_dock, run_dock, Qt.Orientation.Vertical)
        self.resizeDocks([properties_dock, run_dock], [200, 700], Qt.Orientation.Vertical)
        self.resizeDocks([run_dock], [440], Qt.Orientation.Horizontal)
        self.run_panel.frame.connect(self.viewer.show_positions)
        self.run_panel.cancel_requested.connect(self.stop_run)
        self._actions()
        self.document.changed.connect(self._recipe_changed)
        self.document.path_changed.connect(lambda _p: self._update_title())
        self.document.undo_stack.cleanChanged.connect(self._clean_changed)
        self.components.message.connect(self.log.add)
        self.components.selected.connect(self._component_selected)
        self.components.composition_requested.connect(self.edit_composition)
        self.viewer.on_pick(self._atom_picked)
        self._update_title()
        self._update_actions()
        self.statusBar().showMessage("Add components, then Build. Right-click an atom for details.")
        self.resize(1400, 900)

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
        self.act_run = self._action("Run preview…", self._run_dialog, "Ctrl+R")
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
        menu.addAction(self.act_export)
        menu = self.menuBar().addMenu("&Edit")
        menu.addAction(self.act_undo)
        menu.addAction(self.act_redo)
        menu = self.menuBar().addMenu("&Build")
        menu.addAction(self.act_build)
        menu.addAction(self.act_cancel)
        menu = self.menuBar().addMenu("&View")
        menu.addAction(self.act_images)
        menu = self.menuBar().addMenu("&Run")
        menu.addAction(self.act_run)
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
        toolbar.addSeparator()
        toolbar.addAction(self.act_images)
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
        self.setWindowTitle(f"{recipe.name}{star}{where} — {TITLE}")

    def _update_actions(self) -> None:
        building = self._worker is not None
        self.act_build.setEnabled(not building and bool(self.document.recipe.components))
        self.act_cancel.setEnabled(building)
        self.act_export.setEnabled(not building and self.built is not None)
        self.act_run.setEnabled(
            not building and not self.is_running() and self.package_path is not None
        )

    # -- recipe --------------------------------------------------------------------------------

    def _recipe_changed(self, _recipe) -> None:
        self._update_title()
        self._update_actions()

    def new_recipe(self) -> None:
        if self._discard_ok():
            self.document.new()
            self.log.add("new recipe")

    def open_recipe(self, path: Path | str) -> bool:
        try:
            self.document.open(path)
        except RecipeError as e:
            self.log.add(f"cannot open {path}: {e}")
            return False
        self.log.add(f"opened {path}")
        return True

    def save_recipe(self, path: Path | str | None = None) -> Path | None:
        try:
            saved = self.document.save(path)
        except (RecipeError, OSError) as e:
            self.log.add(f"not saved: {e}")
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
        self.package_path = None
        names = ", ".join(
            f"{n} {m.name}" for m, n in zip(built.molecules, built.counts, strict=True)
        )
        self.log.add(f"built: {built.atom_count} atoms ({names}), box {built.box_nm:.4f} nm")
        if "notes" in built.provenance:
            self.log.add(f"note: {built.provenance['notes']}")
        self.show_scene(Scene.from_document(system_document(built, utc_now()), built.positions_nm))

    def _build_failed(self, reason: str) -> None:
        self.log.add(f"build failed: {reason}")

    def _build_done(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._thread, self._worker = None, None
        self._update_actions()

    # -- packages ------------------------------------------------------------------------------

    def show_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.viewer.show_scene(scene)
        self._update_actions()

    def open_package(self, path: Path | str) -> bool:
        """Show a package read-only."""
        try:
            scene = read_package(path)
        except (OSError, ValueError, KeyError) as e:
            self.log.add(f"cannot open package {path}: {e}")
            return False
        self.built = None
        self.package_path = Path(path)
        self.show_scene(scene)
        self.log.add(f"opened package {path}: {scene.n_atoms} atoms (read-only)")
        return True

    def export_package(self, path: Path | str, overwrite: bool = False) -> Path | None:
        """Write the built system as a validated package, and the recipe next to it."""
        if self.built is None:
            self.log.add("nothing to export: build first")
            return None
        path = Path(path)
        try:
            out = write_package(self.built, path, overwrite=overwrite, created_utc=utc_now())
        except Exception as e:  # validation failures and I/O errors go to the log
            self.log.add(f"export failed: {type(e).__name__}: {e}")
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
        if self.package_path is None or self.scene is None:
            self.log.add("export the package (or open one) first")
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
            self.log.add("no engine: set [executables] ucmd in the machine profile (Run preview…)")
            return None
        try:
            cutoff, warning = preview_cutoff(self.scene.cell_nm)
        except (TypeError, ValueError) as e:
            self.log.add(f"cannot run: {e}")
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
        self.run_panel.begin(run_dir, settings)
        self.run.start()
        self._update_actions()
        return run_dir

    def stop_run(self) -> None:
        if self.run is not None:
            self.run.cancel()

    def _run_finished(self, code: int, message: str) -> None:
        self.log.add(f"run {message}")
        if self.run_dir is not None:
            self.run_panel.end(self.run_dir, message)
        self.statusBar().showMessage(f"run {message}")
        self.run = None
        self._update_actions()

    # -- selection -----------------------------------------------------------------------------

    def _component_selected(self, name: str) -> None:
        self.properties.show_component(self.document.recipe, name)
        if self.scene is not None:
            self.viewer.highlight(self.scene.atoms_of(name).tolist())

    def _atom_picked(self, index: int) -> None:
        if self.scene is None:
            return
        self.properties.show_atom(self.scene.atom(index))
        self.viewer.highlight([index])

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
    if argv:
        window.open_recipe(Path(argv[0]))
    window.show()
    return app.exec()

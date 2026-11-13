"""Screenshots of the GUI preview's main states, made without a display (design/B2a.md §9).

    QT_QPA_PLATFORM=offscreen .venv/bin/python validation/b2a/screenshots.py OUT_DIR

Qt's offscreen platform cannot host the OpenGL widget of the 3D view. This script therefore
uses a second implementation of the Viewer protocol: the same `SceneActors` drawing code renders
into an off-screen VTK window, and the image is shown in a QLabel. Everything else is the real
window. A run uses the engine of the machine profile if one is configured (UCMD_MACHINE_PROFILE),
else the test suite's fake engine.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pyvista as pv
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "gui"))


class ImageViewer(QLabel):
    """The Viewer protocol on an off-screen VTK render shown as an image."""

    def __init__(self) -> None:
        super().__init__()
        from ucmd_builder.gui.viewer import SceneActors

        self.setMinimumSize(640, 560)
        self.plotter = pv.Plotter(off_screen=True, window_size=(800, 640))
        self.actors = SceneActors(self.plotter)
        self.setStyleSheet("background: white")

    def _show(self) -> None:
        image = np.ascontiguousarray(self.plotter.screenshot())
        h, w, _ = image.shape
        qimage = QImage(image.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(qimage.copy()))

    def show_scene(self, scene) -> None:
        self.actors.show(scene)
        self._show()

    def show_positions(self, positions_nm) -> None:
        self.actors.move(positions_nm)
        self._show()

    def show_images(self, on: bool) -> None:
        self.actors.show_images(on)
        self._show()

    def highlight(self, atoms) -> None:
        self.actors.highlight(atoms)
        self._show()

    def on_pick(self, callback) -> None:
        pass

    def screenshot(self, path) -> None:
        self.plotter.screenshot(str(path))


def settle(app: QApplication, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main(out: Path) -> int:
    import os

    from sample_recipes import ELECTROLYTE

    from ucmd_builder.gui.app import MainWindow
    from ucmd_builder.gui.composition import CompositionDialog
    from ucmd_builder.gui.run_preview import RunDialog
    from ucmd_builder.gui.runs import RunSettings
    from ucmd_builder.profile import executable

    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(viewer=ImageViewer())
    window.ask = lambda _q: True
    window.resize(1500, 950)
    window.show()
    settle(app)
    window.grab().save(str(out / "1-empty.png"))

    window.open_recipe(ROOT / "examples" / "lp57-1M.toml")
    dialog = CompositionDialog(window.document.recipe, window)
    dialog.show()
    settle(app)
    dialog.grab().save(str(out / "2-composition.png"))
    dialog.close()

    recipe = out / "small.toml"
    recipe.write_text(ELECTROLYTE.replace("{method}", "insertion"))
    window.open_recipe(recipe)
    window.start_build()
    while window.is_building():
        settle(app, 0.1)
    window.components.table.selectRow(1)  # PF6 highlighted
    settle(app)
    window.grab().save(str(out / "3-built.png"))

    package = window.export_package(out / "small.ucsys", overwrite=True)
    if executable("ucmd") is None:
        from fakes import fake_engine

        class Env:
            def setenv(self, k, v):
                os.environ[k] = v

            def delenv(self, k, raising=False):
                os.environ.pop(k, None)

        fake_engine(out, Env())
    window.open_package(package)
    run_dialog = RunDialog(package, window.scene, window.ask, window)
    run_dialog.show()
    settle(app)
    run_dialog.grab().save(str(out / "4-run-dialog.png"))
    run_dialog.close()
    window.start_run(RunSettings(length_ps=1.0, frame_every=20))
    while window.is_running():
        settle(app, 0.1)
    window.run_panel.slider.setValue(len(window.run_panel.trajectory) - 1)
    settle(app)
    window.grab().save(str(out / "5-run.png"))
    print("\n".join(str(p) for p in sorted(out.glob("*.png"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out"))

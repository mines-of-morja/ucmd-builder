"""Screenshots of B2d with the real 3D widget (docs/design/B2d.md of the umbrella repo).

    QT_QPA_PLATFORM=xcb .venv/bin/python validation/b2d/screenshots.py OUT WORKSPACE STUDY XYZ...

STUDY must have a run `base_s1/T20C` with an H5MD trajectory and tables with a row `zeta_0.65`;
the queue server is asked at http://127.0.0.1:8765 (read only). Needs a display or a headless
compositor. Prints the window's size and minimum size per state and the time of 20 resizes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.settings import Settings


def main(out: Path, workspace: Path, study: Path, molecules: list[Path]) -> int:
    app = QApplication([])
    w = MainWindow(settings=Settings())
    w.ask = lambda _q: True
    w.background = True
    w.resize(1500, 950)
    w.show()

    def settle(seconds: float = 0.6) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def until(done) -> None:
        while not done():
            app.processEvents()

    def shot(name: str) -> None:
        settle()
        w.grab().save(str(out / f"{name}.png"))
        hint = w.minimumSizeHint()
        print(name, w.width(), w.height(), "min", hint.width(), hint.height(), flush=True)

    out.mkdir(parents=True, exist_ok=True)
    settle(1)
    w.set_workspace(workspace)
    w.project.item("studies").setExpanded(True)
    w.project.item(f"study/{study.name}").setExpanded(True)
    w.open_by_user([study / "runs" / "base_s1" / "T20C"])
    until(lambda: not w.is_loading() and w.shown)
    w.run_panel.slider.setValue(1250)
    shot("1-run-3d")
    w.notebook.setCurrentWidget(w.plots_page)
    w.run_panel.plots.tabs.setCurrentIndex(2)
    shot("2-plots")
    w.run_panel.tables_button.click()
    shot("3-trends")
    page = w.notebook.table_pages()[0]
    rows = {page.grid.item(r, 0).text(): r for r in range(page.grid.rowCount())}
    page.grid.cellDoubleClicked.emit(rows["zeta_0.65"], 0)
    until(lambda: not w.is_loading() and "zeta" in w.shown)
    shot("4-row-opened-its-run")
    w.open_by_user(molecules)
    w.ruler.measure.setChecked(True)
    w._atom_picked(1)
    w._atom_picked(0)
    shot("5-molecules-ruler")
    w.set_queue_url("http://127.0.0.1:8765")
    w.notebook.setCurrentWidget(w.queue_page)
    queue = w.queue_page
    until(lambda: not queue.is_asking() and (queue.state is not None or queue.problem))
    shot("6-queue")
    for size in ((900, 600), (1366, 740)):
        w.resize(*size)
        shot(f"7-{size[0]}x{size[1]}")
    print("queue problem:", queue.problem)
    start = time.perf_counter()
    for k in range(20):
        w.resize(1300 + 10 * k, 850)
        app.processEvents()
    print(f"20 resizes {time.perf_counter() - start:.2f} s")
    w.close()
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    ws = Path(args[1]).resolve()
    sys.exit(main(Path(args[0]), ws, ws / "studies" / args[2], [Path(p) for p in args[3:]]))

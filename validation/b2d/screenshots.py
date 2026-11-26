"""Screenshots of B2d with the real 3D widget (docs/design/B2d.md of the umbrella repo).

    QT_QPA_PLATFORM=xcb .venv/bin/python validation/b2d/screenshots.py OUT WORKSPACE STUDY XYZ...

STUDY must have a run `base_s1/T20C` with an H5MD trajectory, tables with a row `zeta_0.65`, and the
queue server must answer at http://127.0.0.1:8765 (read only). Needs a display (or a headless
compositor); prints the window's size and minimum size per state and the time of 20 resizes.
"""

import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.settings import Settings

OUT = Path(sys.argv[1])
WS = Path(sys.argv[2]).resolve()
ST = WS / "studies" / sys.argv[3]
app = QApplication([])
w = MainWindow(settings=Settings())
w.ask = lambda q: True
w.background = True
w.resize(1500, 950)
w.show()


def settle(s=0.6):
    e = time.time() + s
    while time.time() < e:
        app.processEvents()
        time.sleep(0.02)


def shot(name):
    settle()
    w.grab().save(str(OUT / f"{name}.png"))
    print(
        name,
        w.width(),
        w.height(),
        "min",
        w.minimumSizeHint().width(),
        w.minimumSizeHint().height(),
        flush=True,
    )


settle(1)
w.set_workspace(WS)
w.project.item("studies").setExpanded(True)
w.project.item(f"study/{ST.name}").setExpanded(True)
w.open_by_user([ST / "runs/base_s1/T20C"])
while w.is_loading() or not w.shown:
    app.processEvents()
w.run_panel.slider.setValue(1250)
shot("1-run-3d")
w.notebook.setCurrentWidget(w.plots_page)
w.run_panel.plots.tabs.setCurrentIndex(2)
shot("2-plots")
w.run_panel.tables_button.click()
shot("3-trends")
pg = w.notebook.table_pages()[0]
rows = {pg.grid.item(r, 0).text(): r for r in range(pg.grid.rowCount())}
pg.grid.cellDoubleClicked.emit(rows["zeta_0.65"], 0)
while w.is_loading() or "zeta" not in w.shown:
    app.processEvents()
shot("4-row-opened-its-run")
w.open_by_user([Path(p) for p in sys.argv[4:]])
w.ruler.measure.setChecked(True)
w._atom_picked(1)
w._atom_picked(0)
shot("5-molecules-ruler")
w.set_queue_url("http://127.0.0.1:8765")
w.notebook.setCurrentWidget(w.queue_page)
while w.queue_page.is_asking() or (w.queue_page.state is None and not w.queue_page.problem):
    app.processEvents()
shot("6-queue")
for size in ((900, 600), (1366, 740)):
    w.resize(*size)
    shot(f"7-{size[0]}x{size[1]}")
print("queue problem:", w.queue_page.problem)
t = time.perf_counter()
for k in range(20):
    w.resize(1300 + 10 * k, 850)
    app.processEvents()
print("20 resizes %.2f s" % (time.perf_counter() - t))
w.close()

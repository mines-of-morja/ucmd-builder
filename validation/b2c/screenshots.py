"""Screenshots of B2c (docs/design/B2c.md of the umbrella repo), made without a display.

    QT_QPA_PLATFORM=offscreen .venv/bin/python validation/b2c/screenshots.py OUT_DIR FILE [...]

FILE: molecule files (.xyz), result tables (.tsv). The 3D view is the off-screen viewer of
validation/b2a/screenshots.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "b2a"))


def main(out: Path, files: list[Path]) -> int:
    from PySide6.QtWidgets import QApplication
    from screenshots import ImageViewer, settle

    from ucmd_builder.gui.app import MainWindow

    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(viewer=ImageViewer())
    window.ask = lambda _q: True
    window.resize(1500, 950)
    window.show()
    settle(app)
    molecules = [f for f in files if f.suffix == ".xyz"]
    if molecules:
        window.open_molecules(molecules)
        window.ruler.measure.setChecked(True)
        ion = window.scene.elements.index("Li") if "Li" in window.scene.elements else 0
        window._atom_picked(window.scene.elements.index("O"))
        window._atom_picked(ion)
        window.ruler.parentWidget().raise_()
        settle(app, 0.5)
        window.grab().save(str(out / "1-molecule.png"))
    for k, table in enumerate(f for f in files if f.suffix == ".tsv"):
        window.open_tables([table])
        settle(app, 0.5)
        window.grab().save(str(out / f"{2 + k}-{table.stem}.png"))
    print("\n".join(str(p) for p in sorted(out.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]))

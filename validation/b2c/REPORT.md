# B2c — molecule files, ruler, tables in the GUI (2026-11-25)

Design: `docs/design/B2c.md` of the umbrella repo. Branch `b2c`, from `b2a`.

| Check (design §4) | Result |
|---|---|
| Molecule file: counts, frames, refusal of bad files | `tests/gui/test_molecule_file.py` |
| The ruler equals the coordinates to 1e-6 nm; minimum image against brute force (two cells) | `tests/gui/test_ruler.py` |
| Mean pressure over the rows that have one; the cell follows NPT frames | `tests/gui/test_b2c_defects.py` |
| Notebook and result tables | `tests/gui/test_notebook.py` |
| A real 5 ns NPT GPU run | 2501 frames with a cell each, 4.270 to 4.284 nm (first, last); 5000 of 25 000 rows of the second half have a pressure |
| Five DFT cluster paths (21 to 172 frames) | open; bonds 56, 52, 48, 44, 40 for n = 0 to 4, as 14 per EMC and 10 per EC |
| Real OpenGL widget in a tab | headless KWin (`--virtual --xwayland`, Mesa software rendering, `QT_QPA_PLATFORM=xcb`): all GUI tests pass, none skipped; frames that arrive while the 3D page is hidden do no harm |

Not checked: a hardware OpenGL window on the author's desktop; more than about 7000 atoms in a
molecule file (the bond guess compares all pairs).

Commands: `.venv/bin/pytest` (229 passed, 1 skipped without a display), `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .`; screenshots: `QT_QPA_PLATFORM=offscreen .venv/bin/python
validation/b2c/screenshots.py OUT FILE…`. Python 3.13.5, PySide6 and PyVista as in the venv.

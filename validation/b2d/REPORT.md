# B2d — one window for building, results and the queue (2026-11-26)

Design: `docs/design/B2d.md` of the umbrella repo (approved 2026-11-25). Branch `b2d`, from `b2c`.
Python 3.13.5, PySide6 and PyVista as in the venv; Mesa software OpenGL for the real 3D widget.

## Acceptance (design §4)

| Step | Criterion | Result |
|---|---|---|
| 1 | B2a and B2c tests pass; window ≤ 900 × 600 in every state | 310 passed, 1 skipped (it needs a display; it passes under a display, below); `tests/gui/test_resize.py`: empty, plots, molecule, tables all fit 900 × 600 |
| 2 | every kind opens by drop and from *Recent*; a broken settings file is ignored with a message | `tests/gui/test_open.py`: recipe, molecule, table, folder of tables, package, run folder, run.toml; five broken files |
| 3 | the sensitivity study opens runs and tables by double click; a missing run is reported | `tests/gui/test_project.py`; real study: the row `zeta_0.65` of `runs.tsv` opened its run (screenshot 4). Names in a table never lead outside the study's `runs/` |
| 4 | the window answers within 0.1 s while a 5 ns run opens; peak memory below 500 MB | run `base_s1` of the scan (2501 frames of 7036 atoms, 50 001 thermo rows), off-screen viewer: longest pause 0.022–0.032 s (four tries; was 0.31 s), peak 351–356 MB (was 1054 MB). With the real widget the memory was not measured |
| 5 | fixture states; no server, wrong schema, slow server are shown and never block | `tests/gui/test_queue_page.py` against a local test server: only `GET /api/state`, no token sent; longest pause < 0.1 s with a server slower than the time allowed. Live server: running and queued jobs shown (screenshot 6) |

## Other numbers

- Resizing: 20 resizes with a run open, real widget, software OpenGL: 1.4 s (before the fixes 28–31 s).
- Minimum window size with the real widget: 482–874 × 401–438 px depending on the page (before: 874 × 1042, 1202 × 1042 with a table).

## Found and fixed on the way

- `QXYSeries.replaceNp` reads a strided NumPy view wrongly (the thermo columns are views of one
  array): six run tests failed until the arrays were made contiguous. Test:
  `test_a_chart_draws_the_numbers_of_the_table_when_it_is_shown`.

## Commands

`.venv/bin/pytest`; `.venv/bin/ruff check .`; `.venv/bin/ruff format --check .`; real widget:
`QT_QPA_PLATFORM=xcb .venv/bin/pytest tests/gui` and `validation/b2d/screenshots.py` (above) under a
display. Here a private headless KWin was used (`kwin_wayland --virtual --xwayland`, own runtime
folder and session bus, `LIBGL_ALWAYS_SOFTWARE=1`): 156 GUI tests passed, none skipped.

## Not checked

- The author's desktop and hardware OpenGL.
- A queue server on another machine (only `127.0.0.1`).

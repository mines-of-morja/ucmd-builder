# ucmd-builder — agent notes

The shared rules are in `../CLAUDE.md`, in the UnitcellMD umbrella repo one level up in the
workspace. The plan is `../PLAN.md`, track B (§6).

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # once; includes the Packmol wheel
.venv/bin/pip install -e '.[dev,gui-test]'                    # the GUI (PySide6, PyVista, h5py)
.venv/bin/ucmd-build gui [RECIPE]                             # the GUI preview (design/B2a.md)
.venv/bin/pytest                                              # spec fixtures from ../spec or $UCMD_SPEC_DIR
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`UCMD_REQUIRE_SPEC=1` and `UCMD_REQUIRE_PACKMOL=1` turn the corresponding skips into failures
(CI sets both).

## Repo rules

- The builder prepares systems and never runs MD. It may depend on the spec validator
  (`ucmd_spec`), but never on engine, bridge, launcher or analysis code;
  `tests/test_architecture.py` enforces this.
- Every force-field parameter set cites its source. Values come only from sources the author
  names.
- The GUI is a thin layer over the library: every GUI action is an edit of the recipe.
  - Only `ucmd_builder.gui` imports Qt, VTK or h5py (`tests/test_architecture.py`). The modules
    `gui/scene.py`, `gui/thermo.py`, `gui/runs.py` and `gui/elements.py` need no Qt.
  - The GUI starts the engine as an external program (`[executables] ucmd` in the machine
    profile) and reads only spec'd files: the thermo table and the H5MD trajectory.
  - Qt sets the process locale from the system; the GUI resets `LC_NUMERIC` to C, because a
    decimal comma breaks C-level number parsing (RDKit's XYZ reader read "0.89" as 0).
  - GUI tests run headless (`QT_QPA_PLATFORM=offscreen`), with a fake engine
    (`tests/gui/fake_ucmd.py`). Modal dialogs go through `MainWindow.ask`, which tests replace.
    The OpenGL view needs a display: CI runs the GUI job under Xvfb.
- Force-field sources are plug-ins (ADR 0008). Every source passes
  `ucmd_builder.forcefield.testkit`. Library values are stored as printed in their source, with
  declared units, and are converted only in `forcefield/units.py`.
- Every package the builder writes goes through the `ucmd-spec` validator before it is moved
  into place. A validation failure is a builder bug, never something to work around.
- ucmd-spec 0.1.0 cannot find its schemas when installed as a wheel. Point it at `spec/` with
  `UCMD_SPEC_DIR`; in the workspace layout the builder uses `../spec` automatically.

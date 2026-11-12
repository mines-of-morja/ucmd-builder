# ucmd-builder — agent notes

The shared rules are in `../CLAUDE.md`, in the UnitcellMD umbrella repo one level up in the
workspace. The plan is `../PLAN.md`, track B (§6).

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # once; includes the Packmol wheel
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
- Force-field sources are plug-ins (ADR 0008). Every source passes
  `ucmd_builder.forcefield.testkit`. Library values are stored as printed in their source, with
  declared units, and are converted only in `forcefield/units.py`.
- Every package the builder writes goes through the `ucmd-spec` validator before it is moved
  into place. A validation failure is a builder bug, never something to work around.
- ucmd-spec 0.1.0 cannot find its schemas when installed as a wheel. Point it at `spec/` with
  `UCMD_SPEC_DIR`; in the workspace layout the builder uses `../spec` automatically.

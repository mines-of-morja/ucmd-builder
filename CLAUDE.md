# ucmd-builder — agent notes

The shared rules are in `../CLAUDE.md`, in the UnitcellMD umbrella repo one level up in the
workspace. The plan is `../PLAN.md`, track B (§6).

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # once
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Repo rules

- The builder prepares systems and never runs MD. It may depend on the spec validator
  (`ucmd_spec`), but never on engine, bridge, launcher or analysis code;
  `tests/test_architecture.py` enforces this.
- Every force-field parameter set cites its source. Values come only from sources the author
  names.
- The GUI is a thin layer over the library: every GUI action is an edit of the recipe.

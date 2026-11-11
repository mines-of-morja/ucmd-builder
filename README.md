# ucmd-builder

The system builder of **UnitcellMD**. It prepares molecular liquids, electrolytes and crystals
as spec-conformant system packages: molecules and unit cells in, packed and parameterized
periodic boxes out. It never runs MD; that is the job of `ucmd-engine`.

**Status:** Phase 0 skeleton. Package layout and checks only. Plan and shared spec:
[UnitcellMD](https://github.com/mines-of-morja/UnitcellMD).

**Planned** (PLAN.md, track B):

- A headless library and the `ucmd-build` CLI, driven by declarative `build.toml` recipes:
  - molecule import;
  - force-field templates with cited sources;
  - composition calculator (e.g. "1 M LiPF₆ in EC:EMC 3:7 w:w");
  - packing;
  - crystals and supercells;
  - initial conditions, including Wigner sampling.
- Then a PySide6 + pyvistaqt GUI on top of the same library.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Python ≥ 3.12.

## License

Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

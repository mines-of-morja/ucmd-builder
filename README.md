# ucmd-builder

The system builder of **UnitcellMD**. It prepares molecular liquids and electrolytes as
spec-conformant system packages: molecules in, packed and parameterized periodic boxes out. It
never runs MD; that is the job of `ucmd-engine`. Plan and shared spec:
[UnitcellMD](https://github.com/mines-of-morja/UnitcellMD).

**Status:** B1a, the headless builder, is under review. B1b (crystals, Wigner sampling, LAMMPS
export) and the GUI (B2) follow.

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'      # includes Packmol (PyPI wheel)
.venv/bin/ucmd-build build examples/lp57-1M.toml -o lp57-1M.ucsys
```

This builds 1 M LiPF₆ in EC:EMC 3:7 (w:w) as in Lehnert 2025: 47 Li⁺, 47 PF₆⁻, 168 EC and
332 EMC (7036 atoms), packed by Packmol in about 20 s. The package is validated with the
spec's reference validator before it is written.

## Recipes

A `build.toml` recipe describes the system: components, where their force-field parameters
come from, composition or counts, box, packing and seed. The same recipe and seed always give
the same package, byte for byte apart from the timestamp. See `examples/` and the design in
`docs/design/B1.md` of the umbrella repo.

```
ucmd-build build RECIPE -o PACKAGE [--force]   recipe → validated system package
ucmd-build compose RECIPE                      composition calculator: counts, box, achieved values
ucmd-build sources                             installed force-field sources (plug-ins)
ucmd-build library [--set NAME]                data sets and templates of the `library` source
ucmd-build check PACKAGE                       validate a package (ucmd-spec)
```

## Force-field sources are plug-ins

Parameters come from **sources** that register through the entry-point group
`ucmd_builder.forcefield_sources` (ADR 0008). Built in:

- `library`: curated data sets with a citation for every value. Values are stored as printed
  in the source and converted once.
  - `opls-aa-carbonates`: EC and EMC from LigParGen, Li⁺ from Jensen & Jorgensen 2006, PF₆⁻
    from CL&P (ADR 0009).
  - `water-spcfw`: SPC/Fw water.
  - `argon-rahman1964`: Rahman's argon.
- `gromacs-top`: imports GROMACS `.top`/`.itp` files. These are written by LigParGen, ATB,
  ACPYPE (GAFF), OpenFF Interchange and CHARMM-GUI. A recipe must cite the file's source.

A new generator (OpenFF, a graph-network force field, our own fit) is a separate package with
one entry point. `ucmd_builder.forcefield.testkit.check_source` checks it.

## Development

```sh
.venv/bin/pytest                                          # ~25 s; -m "not slow" skips the 7036-atom box
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

- Python ≥ 3.12.
- The tests read the spec fixtures from `../spec` in the workspace layout, or from
  `$UCMD_SPEC_DIR`.

## License

Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

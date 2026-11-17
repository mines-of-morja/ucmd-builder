# Carbonate electrolyte test boxes

LiPF₆ in five solvent mixtures, 0.1–3.0 M: 35 boxes from the author's table `boxes.csv`, as
builder recipes in `recipes/`. They are test systems for the engine once the force-field
gradients run on them.

```sh
.venv/bin/ucmd-build build examples/EC/recipes/ec-emc_3-7w_1.0M.toml -o ec-emc_1.0M.ucsys
.venv/bin/python examples/EC/make_recipes.py      # after editing boxes.csv
```

| Mixture | Counts from | ζ |
|---|---|---|
| EC:EMC 3:7 (w) | Lehnert 2025, SI Table S1 | 0.75 |
| EC:DMC:PC 27:63:10 (v) | Lehnert 2025, SI Table S1 | 0.75 |
| EC:EMC:MP 2:6:2 (v) | Lehnert 2025, SI Table S1 | 0.73 |
| EC:DMC 1:1 (w) | estimate, calibrated on Lehnert's EC:EMC | not set |
| EMC:FEC 19:1 (w) | estimate, calibrated on Lehnert's EC:EMC | not set |

All 35 build with Packmol (8–49 s each on the workstation), pass `ucmd check`, and give finite
energies and forces in an engine single point (PME, 1.4 nm). Minimize before running.

- **Box edge:** the table's approximate value, an initial box for packing. The density comes
  later from NPT, as in Lehnert's protocol (3 ns at 1 atm).
- **Force field:** `opls-aa-carbonates` (ADR 0009). All solvents (EC, EMC, DMC, PC, MP, FEC)
  come from LigParGen 3.0.1 with 1.14·CM1A-LBCC charges; DMC, PC, MP and FEC were generated on
  2026-11-17 the same way as EC and EMC. PC has no stereo label: both enantiomers have the same
  parameters. Li⁺ is Jensen & Jorgensen 2006, PF₆⁻ is CL&P.
- **ζ for the estimated mixtures** is empty in the table, so their ions are unscaled. Lehnert
  fits ζ per electrolyte, so this needs a decision before any production run.
- `make_recipes.py` writes the recipes from `boxes.csv`; a test checks that they are up to date
  and that every box has the table's atom count.

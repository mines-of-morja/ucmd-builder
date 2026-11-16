# Carbonate electrolyte test boxes

LiPF₆ in five solvent mixtures, 0.1–3.0 M: 35 boxes from the author's table `boxes.csv`, as
builder recipes in `recipes/`. They are test systems for the engine once the force-field
gradients run on them.

```sh
.venv/bin/ucmd-build build examples/EC/recipes/ec-emc_3-7w_1.0M.toml -o ec-emc_1.0M.ucsys
.venv/bin/python examples/EC/make_recipes.py      # after editing boxes.csv
```

| Mixture | Counts from | ζ | Builds now |
|---|---|---|---|
| EC:EMC 3:7 (w) | Lehnert 2025, SI Table S1 | 0.75 | yes, all 7 (14–30 s each with Packmol) |
| EC:DMC:PC 27:63:10 (v) | Lehnert 2025, SI Table S1 | 0.75 | no: DMC and PC need parameters |
| EC:EMC:MP 2:6:2 (v) | Lehnert 2025, SI Table S1 | 0.73 | no: MP (methyl propionate) needs parameters |
| EC:DMC 1:1 (w) | estimate, calibrated on Lehnert's EC:EMC | not set | no: DMC needs parameters |
| EMC:FEC 19:1 (w) | estimate, calibrated on Lehnert's EC:EMC | not set | no: FEC needs parameters |

- **Box edge:** the table's approximate value, an initial box for packing. The density comes
  later from NPT, as in Lehnert's protocol (3 ns at 1 atm).
- **Force field:** `opls-aa-carbonates` (ADR 0009) has EC, EMC, Li⁺ and PF₆⁻. DMC, PC, MP and
  FEC appear by SMILES. Those recipes build once a cited parameter source for them exists, for
  example LigParGen as for EC and EMC. Until then the builder stops with
  `no template … matches DMC`.
- **ζ for the estimated mixtures** is empty in the table, so their ions are unscaled. Lehnert
  fits ζ per electrolyte, so this needs a decision before any production run.
- `make_recipes.py` writes the recipes from `boxes.csv`; a test checks that they are up to date
  and that the EC:EMC atom counts match the table.

# B1 validation: builder packages vs OpenMM

- **Date:** 2026-11-12.
- **Result: PASS.** Two checks are covered:
  - **B1.3 and B1.9:** the builder's EC and EMC packages agree with OpenMM's reading of the
    original LigParGen topologies, term by term, to machine precision.
  - **Gate B1a:** "recipe → valid package → OpenMM runs it" for the full 1 M LiPF₆ in EC:EMC
    box of Lehnert 2025.

## What is compared, and why it is independent

The two evaluation paths share no code:

| Path | Steps |
|---|---|
| **Builder → oracle** | LigParGen output → `scripts/transcribe_gromacs.py` → library template (values as printed) → `forcefield/units.py` → `ucmd-build` → system package → `tools/oracle` v1 (our OpenMM converter) |
| **Reference** | the same LigParGen `LIG.gmx.itp` → OpenMM's own GROMACS reader (`app.GromacsTopFile`), with its own handling of comb-rule 3, gen-pairs and fudge factors, Ryckaert–Bellemans torsions and funct-4 impropers |

- Both paths are evaluated in vacuum on OpenMM's Reference platform (double precision), at
  the package's positions.
- `run.sh` first checks that the given topology is the one the library was transcribed from
  (`topology_sha256` in the template).

## 1. Single molecules, term by term

The tolerance is 10⁻⁸ relative per term; terms below 10⁻³ kJ/mol are compared absolutely.

| Term (kJ/mol) | EC: package | EC: OpenMM(GROMACS) | rel. diff | EMC: package | EMC: OpenMM(GROMACS) | rel. diff |
|---|---|---|---|---|---|---|
| bond | 16.5056995123 | 16.5056995123 | 0 | 11.5815417276 | 11.5815417276 | 0 |
| angle | 43.0478845893 | 43.0478845893 | 0 | 19.5216101225 | 19.5216101225 | 0 |
| dihedral (RB) | 46.9637855079 | 46.9637855079 | 0 | 1.7222748837 | 1.7222748837 | 0 |
| improper | 0.0000253238 | 0.0000253238 | 0 | 0.0000052003 | 0.0000052003 | 0 |
| Coulomb (incl. scaled 1-4) | −15.9373406205 | −15.9373406205 | 1.1×10⁻¹⁵ | −68.5126642359 | −68.5126642359 | 8.3×10⁻¹⁶ |
| LJ | −0.4442627568 | −0.4442627568 | 3.8×10⁻¹⁶ | 10.1706235264 | 10.1706235264 | 1.4×10⁻¹⁵ |
| LJ 1-4 | −0.0007630558 | −0.0007630558 | 1.5×10⁻¹² (abs.) | 14.2906043606 | 14.2906043606 | 1.5×10⁻¹⁵ |
| **total** | 90.1350285002 | 90.1350285002 | 1.6×10⁻¹⁶ | −11.2260044148 | −11.2260044148 | 7.9×10⁻¹⁵ |

- The worst difference is 1.5×10⁻¹², in a term of −7.6×10⁻⁴ kJ/mol.
- The oracle's own conventions are tested separately in `tools/oracle/test_oracle.py`:
  - SPC/Fw water against a NumPy hand calculation, to 10⁻¹⁰;
  - the NaCl Madelung constant from PME, 1.747565 within 2×10⁻⁶.

## 2. The full electrolyte box (Gate B1a)

- **System:** `examples/lp57-1M.toml` → 47 Li⁺, 47 PF₆⁻, 168 EC and 332 EMC.
  - 7036 atoms, matching Lehnert 2025 SI Table S1.
  - Box 4.2759 nm. The density is the PLAN.md placeholder of 1.2 g/cm³, since the Landesfeind
    2019 SI is still missing.
  - Packmol 21.2.3, tolerance 0.2 nm; closest intermolecular pair 0.220 nm.
  - Ion charges scaled by ζ = 0.75.
- **Evaluation:** OpenMM PME with a 1.4 nm cutoff (Lehnert), Ewald tolerance 10⁻⁵, LJ
  truncated.

| Term | kJ/mol |
|---|---|
| bond | 6618.030 |
| angle | 13713.219 |
| dihedral | 8461.711 |
| improper | 0.006 |
| Coulomb | −27720.380 |
| LJ | 57777.111 |
| LJ 1-4 | 4744.353 |
| **total** | 63594.049 |

- The terms sum to the total exactly; all forces are finite.
- The net charge is 1.7×10⁻¹⁴ e.
- The topology contains 6610 bonds, 11056 angles, 9500 dihedrals, 500 impropers and 8660
  1-4 pairs.
- The large LJ energy and a maximum force of 17324 kJ mol⁻¹ nm⁻¹ are expected for a freshly
  packed box. Two causes:
  - close contacts at the packing tolerance;
  - the template geometries are not at the force-field minimum: LigParGen ran without
    optimization rounds, e.g. EC bond energy 16.5 kJ/mol per molecule.

  The FIRE minimizer (E2.0) removes both before MD; this is not a builder defect.

## Reproduce

```sh
python3 validation/b1-openmm/fetch_ligpargen.py /tmp/lpg   # only if the files are not at hand
validation/b1-openmm/run.sh /tmp/lpg                       # about 30 s
```

`run.sh` refuses topologies other than the transcribed ones. A later LigParGen version may
return different numbers; the library then stays as it is until it is deliberately updated.

## Inputs and versions

- **LigParGen run of 2026-11-12:** LigParGen 3.0.1, BOSS 5.1, RDKit 2023.03.3.
  1.14·CM1A-LBCC charges, net charge 0, no optimization rounds.

  | File | sha256 |
  |---|---|
  | EC `LIG.gmx.itp` | `d6867a7d1272ecba6eea5c5c619ae287c3c49a6f8cb8799f389df91a1e724021` |
  | EMC `LIG.gmx.itp` | `b67293b96ddd296596089d4057c9226e7e48882dfbd22c671f3f42d2adcc91b9` |
  | EC `LIG.gmx.gro` | `c253b6ca85c18c0b81a939e7ad1b71e8f1eef2b13a9f2b28afa64620384782f9` |
  | EMC `LIG.gmx.gro` | `da8830ccdbebbf100e6e76e91606c0def0eb5bc7b68b7be5203af1989defb1fc` |

  The files are not committed: they carry a third-party header (CLAUDE.md).
- **Software:** ucmd-builder 0.1.0.dev0 (branch `b1`), Python 3.13.5, numpy 2.5.4,
  RDKit 2026.9.2, Packmol 21.2.3 (PyPI wheel), OpenMM 8.6.1 (oracle venv), oracle v1
  (umbrella branch `b1a`).

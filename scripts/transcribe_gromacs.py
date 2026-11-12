"""Transcribe a GROMACS topology (e.g. LigParGen output) into a `library` template file.

Usage:
    python scripts/transcribe_gromacs.py LIG.gmx.itp LIG.gmx.gro NAME "SMILES" "description" \
        --generation-tool "..." --generation-input "..." --generation-date YYYY-MM-DD \
        > src/ucmd_builder/forcefields/SET/NAME.toml

The values are written as the topology prints them (GROMACS units: nm, kJ/mol, degrees, ½k
forms). Atom order and names stay as generated. Citations are added by hand afterwards; the
script leaves a marker so that a template without them cannot be committed unnoticed (the
library tests require citations).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ucmd_builder.forcefield.gromacs import element_from_mass, read_gro, read_topology


def fmt(x: float) -> str:
    text = repr(float(x))
    return "0.0" if text == "-0.0" else text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("itp", type=Path)
    ap.add_argument("gro", type=Path)
    ap.add_argument("name")
    ap.add_argument("smiles")
    ap.add_argument("description")
    ap.add_argument("--generation-tool", required=True)
    ap.add_argument("--generation-input", required=True)
    ap.add_argument("--generation-date", required=True)
    args = ap.parse_args()

    top = read_topology(args.itp)
    (mol,) = top.molecules.values()
    xyz = read_gro(args.gro)
    out = []
    w = out.append
    w(f"# {args.name}: {args.description}.")
    w("# Values as printed in the generated GROMACS topology (nm, kJ/mol, degrees, ½k forms);")
    w("# atom indices are 0-based. Transcribed by scripts/transcribe_gromacs.py.")
    w(f'name = "{args.name}"')
    w(f'description = "{args.description}"')
    w(f'smiles = "{args.smiles}"')
    total = round(sum(q for _, _, q, _ in mol.atoms))
    w(f"formal_charge = {total}")
    w("atoms = [")
    for k, (type_name, atom_name, charge, mass) in enumerate(mol.atoms):
        t = top.atomtypes[type_name]
        m = t.mass if mass is None else mass
        element = element_from_mass(m, f"atom {k + 1}")
        x, y, z = (fmt(v) for v in xyz[k])
        w(
            f'  {{ name = "{atom_name}", element = "{element}", type = "{type_name}", '
            f"charge = {fmt(charge)}, mass = {fmt(m)}, xyz = [{x}, {y}, {z}] }},"
        )
    w("]")
    w("bonds = [")
    for b in mol.bonds:
        w(
            f'  {{ atoms = {list(b.atoms)}, form = "harmonic", r0 = {fmt(b.params["r0_nm"])}, '
            f"k = {fmt(b.params['k_kjmol_nm2'])} }},"
        )
    w("]")
    w("angles = [")
    for a in mol.angles:
        w(
            f'  {{ atoms = {list(a.atoms)}, form = "harmonic", '
            f"theta0 = {fmt(a.params['theta0_deg'])}, "
            f"k = {fmt(a.params['k_kjmol_rad2'])} }},"
        )
    w("]")
    w("dihedrals = [")
    for d in mol.dihedrals:
        if d.form == "ryckaert-bellemans":
            c = ", ".join(fmt(d.params[f"c{i}_kjmol"]) for i in range(6))
            w(f'  {{ atoms = {list(d.atoms)}, form = "ryckaert-bellemans", c = [{c}] }},')
        elif d.form == "periodic":
            p = d.params
            w(
                f'  {{ atoms = {list(d.atoms)}, form = "periodic", k = {fmt(p["k_kjmol"])}, '
                f"n = {int(p['n'])}, phi0 = {fmt(p['phi0_deg'])} }},"
            )
        else:
            raise SystemExit(f"unexpected dihedral form {d.form}")
    w("]")
    w("impropers = [")
    for d in mol.impropers:
        p = d.params
        w(
            f'  {{ atoms = {list(d.atoms)}, form = "periodic", k = {fmt(p["k_kjmol"])}, '
            f"n = {int(p['n'])}, phi0 = {fmt(p['phi0_deg'])} }},"
        )
    w("]")
    w("")
    w("[units]")
    w('energy = "kJ/mol"')
    w('length = "nm"')
    w('harmonic = "k/2"')
    w("")
    w("[lj]")
    for type_name in dict.fromkeys(t for t, _, _, _ in mol.atoms):
        t = top.atomtypes[type_name]
        w(f"{type_name} = {{ sigma = {fmt(t.sigma_nm)}, epsilon = {fmt(t.epsilon_kjmol)} }}")
    w("")
    w("[generation]")
    w(f'tool = "{args.generation_tool}"')
    w(f'input = "{args.generation_input}"')
    w(f'date = "{args.generation_date}"')
    w(f'topology_sha256 = "{top.sha256}"')
    w("")
    w("# CITATIONS: add [[citations]] entries by hand.")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

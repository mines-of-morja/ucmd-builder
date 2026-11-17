"""Write one builder recipe per row of boxes.csv (the author's table of test boxes).

    .venv/bin/python examples/EC/make_recipes.py      # from the repo root; rewrites recipes/

The counts are the table's. The box edge is the table's approximate value: an initial box for
packing; the density comes later from NPT, as in Lehnert's protocol. Every molecule is a
template of the `opls-aa-carbonates` library (ADR 0009).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import tomli_w

HERE = Path(__file__).resolve().parent
ORDER = ("Li", "PF6", "EC", "EMC", "DMC", "PC", "MP", "FEC")
IONS = ("Li", "PF6")


def slug(electrolyte: str, c_m: str) -> str:
    """'EC:EMC 3:7 (w)', '1.0' → 'ec-emc_3-7w_1.0M'."""
    solvents, ratio, basis = re.match(r"(\S+) (\S+) \((\w)\)", electrolyte).groups()
    return f"{solvents.lower().replace(':', '-')}_{ratio.replace(':', '-')}{basis}_{c_m}M"


def recipe(row: dict[str, str]) -> tuple[str, str]:
    name = slug(row["electrolyte"], row["c_M"])
    zeta = float(row["zeta"]) if row["zeta"] else None
    components = []
    for molecule in ORDER:
        count = int(row[f"n_{molecule}"])
        if count == 0:
            continue
        entry = {"name": molecule, "molecule": {"library": molecule}, "count": count}
        if molecule in IONS and zeta is not None:
            entry["charge_scale"] = zeta
        components.append(entry)
    doc = {
        "format": "ucmd-build",
        "version": "0.1",
        "name": name,
        "description": f"{row['c_M']} M LiPF6 in {row['electrolyte']}; counts: {row['source']}",
        "seed": 20261116,
        "forcefield": {"source": "library", "set": "opls-aa-carbonates"},
        "component": components,
        "box": {"length_nm": float(row["L_box_nm_approx"])},
        "packing": {"method": "packmol", "tolerance_nm": 0.2},
    }
    notes = [
        f"# {row['c_M']} M LiPF6 in {row['electrolyte']}: {row['n_atoms']} atoms.",
        f"# Counts: {row['source']}. Box edge: approximate initial box from the author's table",
        "# (examples/EC/boxes.csv); the density comes from NPT later.",
    ]
    if zeta is None:
        notes.append("# Ion charge scale ζ: not set in the table (ions unscaled); to be decided.")
    else:
        notes.append(f"# Ion charge scale ζ = {zeta} (Lehnert 2025, SI Table S3).")
    notes.append(f"# Build:  ucmd-build build examples/EC/recipes/{name}.toml -o {name}.ucsys")
    return name, "\n".join(notes) + "\n" + tomli_w.dumps(doc)


def main() -> None:
    out = HERE / "recipes"
    out.mkdir(exist_ok=True)
    with (HERE / "boxes.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        name, text = recipe(row)
        (out / f"{name}.toml").write_text(text, encoding="utf-8")
    print(f"wrote {len(rows)} recipes to {out}")


if __name__ == "__main__":
    main()

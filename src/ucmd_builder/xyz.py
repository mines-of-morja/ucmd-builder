"""Extended XYZ export of a system package, for viewers (PLAN.md B1.9, pulled forward).

Reads any spec v0.1 package (system.json and positions.npy) and writes one extended-XYZ frame
in Å, with the cell as `Lattice` (rows a, b, c) and `pbc`. OVITO, VMD, Jmol and `ase gui` read
it. Positions stay unwrapped, as in the package, so molecules are whole. This is a unit
boundary (nm → Å).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .constants import ANGSTROM_IN_NM


def package_elements(doc: dict) -> list[str]:
    elements = []
    for component in doc["components"]:
        atoms = doc["molecule_types"][component["molecule_type"]]["atoms"]
        for _ in range(component["count"]):
            elements += [a["element"] or "X" for a in atoms]
    return elements


def write_xyz(package: Path | str, output: Path | str) -> int:
    """Write the package as extended XYZ; return the number of atoms."""
    package = Path(package)
    doc = json.loads((package / "system.json").read_text(encoding="utf-8"))
    positions = np.load(package / "positions.npy") / ANGSTROM_IN_NM
    elements = package_elements(doc)
    if len(elements) != len(positions):
        raise ValueError(
            f"{package}: {len(elements)} atoms in system.json, {len(positions)} positions"
        )
    header = [f'Properties=species:S:1:pos:R:3 name="{doc["name"]}"']
    if doc["cell_nm"] is not None:
        cell = (np.array(doc["cell_nm"]) / ANGSTROM_IN_NM).ravel()
        header.insert(0, 'Lattice="' + " ".join(f"{v:.8f}" for v in cell) + '"')
    pbc = " ".join("T" if p else "F" for p in doc["periodicity"])
    header.append(f'pbc="{pbc}"')
    lines = [str(len(elements)), " ".join(header)]
    lines += [
        f"{e} {x:.6f} {y:.6f} {z:.6f}" for e, (x, y, z) in zip(elements, positions, strict=True)
    ]
    Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(elements)

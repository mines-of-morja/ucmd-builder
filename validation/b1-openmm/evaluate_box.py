"""Gate B1: OpenMM runs a builder-made electrolyte package (oracle v1, PME).

PYTHONPATH=../../../tools/oracle python evaluate_box.py PACKAGE [CUTOFF_NM]
"""

import sys

import numpy as np
import oracle

top = oracle.load_topology(sys.argv[1])
cutoff = float(sys.argv[2]) if len(sys.argv) > 2 else 1.4
print(
    f"{top.name}: {top.n_atoms} atoms, box {top.cell_nm[0, 0]:.4f} nm, "
    f"net charge {top.charges_e.sum():+.1e} e, {len(top.pairs14)} 1-4 pairs"
)
system = oracle.molecular_system(top, cutoff_nm=cutoff, ewald_tolerance=1e-5)
terms = oracle.energy_terms(system, top.positions_nm, top.cell_nm)
for name, value in terms.items():
    print(f"  {name:<9} {value:14.3f} kJ/mol")
residual = sum(v for k, v in terms.items() if k != "total") - terms["total"]
energy, forces = oracle.evaluate(system, top.positions_nm, top.cell_nm)
finite = bool(np.isfinite(energy) and np.all(np.isfinite(forces)))
print(
    f"  terms - total = {residual:.2e}; forces finite: {finite}; "
    f"max |F| = {np.abs(forces).max():.0f} kJ/mol/nm (packed, not minimized)"
)
sys.exit(0 if finite and abs(residual) < 1e-6 * abs(terms["total"]) else 1)

"""Request solvent parameters from the LigParGen server (ADR 0009).

    python3 fetch_ligpargen.py OUTDIR [NAME ...]     # default: all molecules below

Writes OUTDIR/NAME/ with every file the server returns (LIG.gmx.itp, LIG.gmx.gro, …).
EC and EMC were fetched on 2026-11-12, DMC, PC, MP and FEC on 2026-11-17.
The request is the one the web form sends: 1.14*CM1A-LBCC charges, net charge 0, no
optimization rounds. Only the SMILES are sent. The server's output may change with new
LigParGen versions; run.sh checks that the topology is the one the library was transcribed
from.
"""

import json
import sys
import urllib.request
from pathlib import Path

API = "https://jorgensenresearch.com/api/lpg-run"
MOLECULES = {
    "EC": "O=C1OCCO1",  # ethylene carbonate
    "EMC": "CCOC(=O)OC",  # ethyl methyl carbonate
    "DMC": "COC(=O)OC",  # dimethyl carbonate
    "PC": "CC1COC(=O)O1",  # propylene carbonate (no stereo label; both enantiomers alike)
    "MP": "CCC(=O)OC",  # methyl propionate
    "FEC": "O=C1OCC(F)O1",  # fluoroethylene carbonate
}


def main() -> int:
    out = Path(sys.argv[1])
    names = sys.argv[2:] or list(MOLECULES)
    for name in names:
        smiles = MOLECULES[name]
        payload = {
            "smiles": smiles,
            "molfile": "",
            "filename": "",
            "charge": "cm1a-lbcc",
            "molcharge": 0,
            "opt": 0,
            "name": "LIG",
            "engine": "current",
            "rebuild": False,
        }
        request = urllib.request.Request(
            API,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ucmd-builder validation"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.load(response)
        if not data.get("ok"):
            raise SystemExit(f"{name}: {data.get('error')}")
        target = out / name
        target.mkdir(parents=True, exist_ok=True)
        for f in data["files"]:
            (target / Path(f["name"]).name).write_text(f.get("text") or "")
        print(name, data.get("versions"), f"{len(data['files'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

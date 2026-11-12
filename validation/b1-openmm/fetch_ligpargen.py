"""Request EC and EMC parameters from the LigParGen server, as on 2026-11-12 (ADR 0009).

    python3 fetch_ligpargen.py OUTDIR

Writes OUTDIR/{EC,EMC}/ with every file the server returns (LIG.gmx.itp, LIG.gmx.gro, …).
The request is the one the web form sends: 1.14*CM1A-LBCC charges, net charge 0, no
optimization rounds. Only the two SMILES are sent. The server's output may change with new
LigParGen versions; run.sh checks that the topology is the one the library was transcribed
from.
"""

import json
import sys
import urllib.request
from pathlib import Path

API = "https://jorgensenresearch.com/api/lpg-run"
MOLECULES = {"EC": "O=C1OCCO1", "EMC": "CCOC(=O)OC"}


def main() -> int:
    out = Path(sys.argv[1])
    for name, smiles in MOLECULES.items():
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

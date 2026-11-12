#!/usr/bin/env bash
# B1 validation: builder packages vs OpenMM (see REPORT.md). Needs the workspace layout: the
# builder venv (.venv), tools/oracle/.venv (OpenMM) and the LigParGen output the library was
# transcribed from:  validation/b1-openmm/run.sh LIGPARGEN_DIR   (fetch with fetch_ligpargen.py)
set -euo pipefail
cd "$(dirname "$0")"
LPG=$(realpath "${1:?usage: run.sh LIGPARGEN_DIR (with EC/ and EMC/ from fetch_ligpargen.py)}")
ROOT=../../..
BUILD=../../.venv/bin/ucmd-build
PY=$ROOT/tools/oracle/.venv/bin/python
rm -rf out && mkdir -p out
for m in EC EMC; do
  # The topology must be the one the library template was transcribed from.
  expected=$(sed -n 's/^topology_sha256 = "\(.*\)"/\1/p' ../../src/ucmd_builder/forcefields/opls-aa-carbonates/$m.toml)
  actual=$(../../.venv/bin/python -c "import sys; from ucmd_builder.forcefield.gromacs import read_topology; print(read_topology(sys.argv[1]).sha256)" "$LPG/$m/LIG.gmx.itp")
  [[ "$expected" == "$actual" ]] || { echo "$m: $LPG/$m/LIG.gmx.itp is not the transcribed topology"; exit 1; }
  printf '#include "LIG.gmx.itp"\n\n[ system ]\n%s\n\n[ molecules ]\nLIG 1\n' "$m" > "$LPG/$m/LIG.top"
  cat > out/one-$m.toml <<TOML
format = "ucmd-build"
version = "0.1"
name = "one-$m"
seed = 1

[forcefield]
source = "library"
set = "opls-aa-carbonates"

[[component]]
name = "$m"
molecule = { library = "$m" }
count = 1

[box]
length_nm = 3.0

[packing]
method = "insertion"
TOML
  "$BUILD" build out/one-$m.toml -o out/one-$m.ucsys -q
  (cd $ROOT/tools/oracle && .venv/bin/python compare_gromacs.py "$OLDPWD/out/one-$m.ucsys" "$LPG/$m/LIG.top")
done
"$BUILD" build ../../examples/lp57-1M.toml -o out/lp57-1M.ucsys -q
PYTHONPATH=$ROOT/tools/oracle "$PY" evaluate_box.py out/lp57-1M.ucsys 1.4

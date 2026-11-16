#!/usr/bin/env bash
# Start the UnitcellMD builder GUI (docs/design/B2a.md of the umbrella repo).
#
#   ./start-gui.sh                         an empty recipe
#   ./start-gui.sh examples/lp57-1M.toml   open a recipe
#
# Works from any directory. The first start creates .venv and installs the GUI from PyPI
# (PySide6, PyVista, VTK, h5py and Packmol; about 1.5 GB, no sudo). Later starts are instant.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || {
    echo "start-gui: Python 3.12 or newer is needed (python3 is $(python3 --version 2>&1))" >&2
    exit 1
  }
  echo "start-gui: creating $VENV …"
  python3 -m venv "$VENV"
fi

if ! "$PY" -c 'import PySide6, pyvista, pyvistaqt, h5py, ucmd_builder' 2>/dev/null; then
  echo "start-gui: installing the GUI into $VENV (once; about 1.5 GB) …"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -e "$REPO[gui,packmol]"
fi

# The run preview starts the engine by the path in the machine profile. Point at a workspace
# build if there is one, but leave the profile alone: the GUI asks before it writes it.
ENGINE="$REPO/../ucmd-engine/target/release/ucmd"
if [[ -z "$("$PY" -c 'from ucmd_builder.profile import executable; print(executable("ucmd") or "")')" ]]; then
  if [[ -x "$ENGINE" ]]; then
    echo "start-gui: for Run preview, choose 'Locate ucmd…' and pick $(realpath "$ENGINE")"
  else
    echo "start-gui: Run preview needs the engine: cd ../ucmd-engine && cargo build --release"
  fi
fi

exec "$VENV/bin/ucmd-build" gui "$@"

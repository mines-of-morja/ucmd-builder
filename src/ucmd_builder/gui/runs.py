"""Run preview without Qt (design/B2a.md §7): settings, cutoff, run.toml, run directory, profile.

The GUI starts the engine as an external program and uses only the spec'd files: it writes
`run.toml` files into a run directory next to the package, reads the thermo table and the H5MD
trajectory, and never touches checkpoints (their format is private to the engine).
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tomli_w

from ..profile import profile_path

# The M2a protocol (Lehnert et al. 2025, SI S1.1): 1.4 nm cutoff, PME to 1e-5, Nosé–Hoover
# with 100 fs damping, 1 fs steps, velocities at 25 °C.
PREVIEW_CUTOFF_NM = 1.4
EWALD_TOLERANCE = 1e-5
SKIN_NM = 0.2  # the engine's default neighbour-list skin
CUTOFF_STEP_NM = 0.05
THERMOSTATS = ("nose-hoover-chain", "csvr")


@dataclass(frozen=True)
class RunSettings:
    temperature_K: float = 298.15
    length_ps: float = 2.0
    timestep_fs: float = 1.0
    thermostat: str = "nose-hoover-chain"
    tau_ps: float = 0.1
    minimize: bool = True
    frame_every: int = 100
    seed: int = 1

    @property
    def steps(self) -> int:
        return max(1, round(self.length_ps * 1000.0 / self.timestep_fs))


def cell_heights(cell_nm: np.ndarray) -> np.ndarray:
    """Distances between opposite faces of the cell (rows a, b, c), in nm."""
    a, b, c = np.asarray(cell_nm, dtype=float)
    volume = abs(float(np.dot(a, np.cross(b, c))))
    return np.array(
        [
            volume / np.linalg.norm(np.cross(b, c)),
            volume / np.linalg.norm(np.cross(c, a)),
            volume / np.linalg.norm(np.cross(a, b)),
        ]
    )


def preview_cutoff(cell_nm: np.ndarray) -> tuple[float, str | None]:
    """The cutoff for a preview run, and a warning if the box forces it below 1.4 nm.

    The cutoff plus the skin must fit in half the smallest cell height. Approved rule
    (design/B2a.md §11, question 2): min(1.4 nm, L/2 − 0.2 nm), rounded down to 0.05 nm.
    """
    limit = float(cell_heights(cell_nm).min()) / 2.0 - SKIN_NM
    if limit >= PREVIEW_CUTOFF_NM:
        return PREVIEW_CUTOFF_NM, None
    cutoff = math.floor(round(limit / CUTOFF_STEP_NM, 9)) * CUTOFF_STEP_NM
    if cutoff <= 0.0:
        raise ValueError(
            f"the box is too small for a run: half its height is {limit + SKIN_NM:.3f} nm"
        )
    return cutoff, (
        f"The box is small: the cutoff is reduced to {cutoff:.2f} nm for this preview "
        f"(the study protocol uses {PREVIEW_CUTOFF_NM} nm)."
    )


def _common(system: str, settings: RunSettings, cutoff_nm: float, title: str) -> dict:
    return {
        "spec_version": "0.1.0",
        "system": system,
        "title": title,
        "run": {
            "integrator": "velocity-verlet",
            "timestep_fs": settings.timestep_fs,
            "steps": settings.steps,
            "seed": settings.seed,
        },
        "providers": [
            {
                "kind": "force-field",
                "cutoff_nm": round(cutoff_nm, 6),
                "coulomb": "pme",
                "ewald_tolerance": EWALD_TOLERANCE,
            }
        ],
    }


def run_files(
    package: Path, run_dir: Path, settings: RunSettings, cutoff_nm: float
) -> dict[str, str]:
    """`minimize.toml` (if minimizing) and `run.toml` for a run directory, as text."""
    relative = os.path.relpath(package, run_dir)
    files = {}
    if settings.minimize:
        doc = _common(relative, settings, cutoff_nm, "GUI preview: minimization")
        doc["run"]["steps"] = 0
        files["minimize.toml"] = tomli_w.dumps(doc)
        system = "minimized.ucsys"
    else:
        system = relative
    doc = _common(system, settings, cutoff_nm, "GUI preview run")
    if settings.thermostat == "nose-hoover-chain":
        thermostat = {"kind": "nose-hoover-chain", "chain_length": 3}
    else:
        thermostat = {"kind": "csvr"}
    thermostat.update({"temperature_K": settings.temperature_K, "tau_ps": settings.tau_ps})
    doc.update(
        {
            "velocities": {
                "generate": "maxwell-boltzmann",
                "temperature_K": settings.temperature_K,
            },
            "momentum": {"remove_com_every": 100},
            "thermostat": thermostat,
            "output": {
                "directory": "out",
                "thermo": {"file": "thermo.tsv", "every": 10},
                "trajectory": {"file": "trajectory.h5", "every": settings.frame_every},
                "checkpoint": {"file": "checkpoint.ucchk", "every": 1000},
            },
        }
    )
    files["run.toml"] = tomli_w.dumps(doc)
    return files


def new_run_dir(package: Path) -> Path:
    """`<package>.runs/<YYYYmmdd-HHMMSS>[-k]/`, next to the package and never inside it."""
    base = package.parent / f"{package.name}.runs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate, k = base / stamp, 2
    while candidate.exists():
        candidate, k = base / f"{stamp}-{k}", k + 1
    candidate.mkdir(parents=True)
    return candidate


def default_profile_path() -> Path:
    if os.environ.get("UCMD_MACHINE_PROFILE"):
        return Path(os.environ["UCMD_MACHINE_PROFILE"])
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "ucmd" / "machine.toml"


def set_executable(name: str, executable: Path, profile: Path | None = None) -> Path:
    """Write `[executables] name = "<absolute path>"` into the machine profile.

    Edits the text, so comments and the order of the other entries stay as they are.
    """
    profile = profile or profile_path() or default_profile_path()
    line = f"{name} = {tomli_w.dumps({'x': str(Path(executable).resolve())}).split(' = ', 1)[1]}"
    text = profile.read_text(encoding="utf-8") if profile.exists() else ""
    entry = re.compile(rf"^{re.escape(name)}\s*=.*$", re.MULTILINE)
    section = re.search(r"^\[executables\]\s*$", text, re.MULTILINE)
    if section is None:
        text = text + ("" if text.endswith("\n") or not text else "\n") + f"\n[executables]\n{line}"
    else:
        rest = text[section.end() :]
        end = re.search(r"^\[", rest, re.MULTILINE)
        block = rest[: end.start()] if end else rest
        if entry.search(block):
            block = entry.sub(line.rstrip("\n"), block, count=1)
        else:
            block = "\n" + line.rstrip("\n") + block
        text = text[: section.end()] + block + (rest[end.start() :] if end else "")
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return profile

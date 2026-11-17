"""Run preview without Qt (design/B2a.md §7): settings, cutoff, run.toml, run directory, profile.

The GUI starts the engine as an external program: it writes `run.toml` files into a run
directory next to the package and reads the thermo table and the trajectory. The trajectory is
the engine's extended XYZ (frames readable while the run goes, the default for previews) or H5MD
(spec'd, for analysis). The GUI never touches checkpoints (their format is private to the
engine).
"""

from __future__ import annotations

import math
import os
import re
import time
import tomllib
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
TRAJECTORY_FORMATS = ("xyz", "h5md")  # xyz: watch frames during the run; h5md: for analysis


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
    trajectory_format: str = "xyz"

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
                # OPLS-AA was parametrized with the LJ tail correction (ADR 0009 amendment)
                "dispersion_correction": True,
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
                "trajectory": {
                    "file": "trajectory.xyz"
                    if settings.trajectory_format == "xyz"
                    else "trajectory.h5",
                    "every": settings.frame_every,
                },
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


@dataclass(frozen=True)
class RunOutputs:
    """Where a run's spec'd files are, read from its `run.toml` (spec/run-config.md §2)."""

    run_toml: Path
    title: str
    system: Path  # the package the run starts from
    thermo: Path | None  # None if the run writes no thermo table
    trajectory: Path | None  # None if the run writes no trajectory
    target_K: float | None  # the thermostat temperature, None without a thermostat
    steps: int

    @property
    def run_dir(self) -> Path:
        return self.run_toml.parent


RECENT_S = 120.0  # an incomplete run whose table changed this recently is probably running


@dataclass(frozen=True)
class RunInfo:
    """One run found on disk, for the run browser."""

    run_toml: Path
    title: str
    steps: int
    last_step: int | None  # the last step in its thermo table; None without a table
    trajectory: str  # "xyz", "h5md" or "none"
    modified: float  # seconds since the epoch: the newest of run.toml and the thermo table

    @property
    def run_dir(self) -> Path:
        return self.run_toml.parent

    @property
    def state(self) -> str:
        if self.last_step is None:
            return "no output"
        if self.last_step >= self.steps:
            return "complete"
        return "running?" if time.time() - self.modified < RECENT_S else "stopped"


def last_step(thermo: Path) -> int | None:
    """The step of the last complete row of a thermo table, reading only its head and tail."""
    try:
        with thermo.open("rb") as fh:
            header = fh.readline().decode("utf-8")
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    names = header.lstrip("# ").rstrip("\n").split("\t")
    if "step" not in names:
        return None
    for line in reversed(tail.split("\n")[:-1]):  # the part after the last newline is partial
        parts = line.split("\t")
        if not line.startswith("#") and len(parts) == len(names):
            try:
                return int(float(parts[names.index("step")]))
            except ValueError:
                return None
    return None


def run_info(run_toml: Path) -> RunInfo:
    out = run_outputs(run_toml)
    thermo_time = out.thermo.stat().st_mtime if out.thermo and out.thermo.is_file() else 0.0
    if out.trajectory is None:
        trajectory = "none"
    else:
        trajectory = "xyz" if out.trajectory.suffix == ".xyz" else "h5md"
    return RunInfo(
        run_toml=run_toml,
        title=out.title,
        steps=out.steps,
        last_step=last_step(out.thermo) if out.thermo else None,
        trajectory=trajectory,
        modified=max(run_toml.stat().st_mtime, thermo_time),
    )


def scan_runs(path: Path | str) -> list[RunInfo]:
    """Every run (a folder with run.toml) below `path`, sorted by path.

    `path` may be any folder: a run folder, `<package>.runs`, a study, `examples/EC/runs`. For a
    package, its runs next to it (`<package>.runs`) are scanned. Symbolic links are not
    followed, so a study's views/ do not list a run twice. Unreadable run.toml files are
    skipped.
    """
    path = Path(path)
    if (path / "system.json").is_file():
        path = path.parent / f"{path.name}.runs"
    found = []
    for root, dirs, files in os.walk(path):
        if "run.toml" in files:
            try:
                found.append(run_info(Path(root) / "run.toml"))
            except (OSError, ValueError):
                pass
        # never inside outputs or packages; a run folder may still hold other runs
        dirs[:] = sorted(d for d in dirs if d != "out" and not d.endswith(".ucsys"))
    return sorted(found, key=lambda r: str(r.run_toml))


def run_outputs(path: Path | str) -> RunOutputs:
    """The outputs of the run described by `path`: a `run.toml` or the directory holding one.

    Works for any spec'd run, not only those the GUI wrote: every path in a `run.toml` is
    relative to its directory, and output files to `[output] directory` (default "out").
    Raises OSError if there is no run.toml, ValueError if it lacks a key the GUI needs.
    """
    path = Path(path)
    run_toml = path / "run.toml" if path.is_dir() else path
    with run_toml.open("rb") as fh:
        try:
            doc = tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{run_toml}: {e}") from e
    base = run_toml.parent
    if not isinstance(doc.get("system"), str):
        raise ValueError(f"{run_toml}: no 'system' package")
    output = doc.get("output", {})
    out_dir = base / output.get("directory", "out")

    def output_file(table: str) -> Path | None:
        entry = output.get(table)
        return out_dir / entry["file"] if isinstance(entry, dict) and "file" in entry else None

    thermostat = doc.get("thermostat")
    target = thermostat.get("temperature_K") if isinstance(thermostat, dict) else None
    return RunOutputs(
        run_toml=run_toml,
        title=str(doc.get("title", run_toml.parent.name)),
        system=base / doc["system"],
        thermo=output_file("thermo"),
        trajectory=output_file("trajectory"),
        target_K=float(target) if target is not None else None,
        steps=int(doc.get("run", {}).get("steps", 0)),
    )


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

"""A stand-in for the engine in GUI tests (design/B2a.md §9).

`minimize <toml> --out <dir>` copies the package. `run <toml>` writes thermo lines step by
step (spec/run-config.md columns), then an H5MD trajectory with `/particles/all/position`.
SIGTERM stops a run with status 143, as the real engine does. FAKE_UCMD_FAIL=1 fails like an
engine error; FAKE_UCMD_DELAY sets the seconds per step.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import time
import tomllib
from pathlib import Path

import h5py
import numpy as np

HEADER = (
    "# step\ttime_ps\tpotential_kjmol\tkinetic_kjmol\ttotal_kjmol\tconserved_energy_kjmol"
    "\ttemperature_K\tpressure_bar\tvolume_nm3\n"
)


def write_h5md(path: Path, steps: list[int], dt_ps: float, frames: list[np.ndarray]) -> None:
    with h5py.File(path, "w") as fh:
        group = fh.create_group("particles/all/position")
        group["step"] = np.array(steps, dtype=np.int64)
        group["time"] = np.array(steps, dtype=float) * dt_ps
        group["value"] = np.array(frames)


def main(argv: list[str]) -> int:
    if os.environ.get("FAKE_UCMD_FAIL"):
        print("error: E_TEST  the fake engine fails on purpose", file=sys.stderr)
        return 1
    command, config_path = argv[0], Path(argv[1])
    config = tomllib.loads(config_path.read_text())
    package = (config_path.parent / config["system"]).resolve()
    if command == "minimize":
        out = config_path.parent / argv[argv.index("--out") + 1]
        shutil.copytree(package, out)
        print("converged: 3 steps (fake engine)", file=sys.stderr)
        return 0
    stop: list[int] = []
    signal.signal(signal.SIGTERM, lambda *_args: stop.append(1))
    steps = config["run"]["steps"]
    dt_ps = config["run"]["timestep_fs"] / 1000.0
    output = config["output"]
    out = config_path.parent / output["directory"]
    out.mkdir(exist_ok=True)
    target = config["thermostat"]["temperature_K"]
    positions = np.load(package / "positions.npy")
    delay = float(os.environ.get("FAKE_UCMD_DELAY", "0.005"))
    frames, frame_steps, last = [], [], 0
    print(f"ucmd (fake): {len(positions)} atoms, steps 0..{steps}", file=sys.stderr, flush=True)
    with (out / output["thermo"]["file"]).open("w") as fh:
        fh.write(HEADER)
        for step in range(steps + 1):
            if stop:
                break
            last = step
            if step % output["thermo"]["every"] == 0:
                t = target * (1.0 - 0.5 * np.exp(-step / 50.0))
                u, k = -100.0 - 0.01 * step, 0.1 * t
                fh.write(f"{step}\t{step * dt_ps:.6f}\t{u}\t{k}\t{u + k}\t-100.0\t{t}\t1.0\t27.0\n")
                fh.flush()
            if step % output["trajectory"]["every"] == 0:
                frames.append(positions + 0.001 * step)
                frame_steps.append(step)
            time.sleep(delay)
    write_h5md(out / output["trajectory"]["file"], frame_steps, dt_ps, frames)
    if stop:
        print(f"stopped by SIGTERM after step {last}", file=sys.stderr)
        return 143
    print(f"done: {steps} steps (fake engine)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

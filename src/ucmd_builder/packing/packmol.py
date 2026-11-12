"""Packmol driver (design/B1.md §7).

The executable comes from the machine profile (`[executables] packmol`, spec/run-config.md
§4). Otherwise the builder uses the Packmol of its own environment, installed with the extra
`ucmd-builder[packmol]` (the authors' PyPI wheel). `PATH` is never searched. The version is
probed and recorded.

Packmol's periodic mode (`pbc`, Packmol ≥ 20.15) packs into the periodic box directly; the
distance tolerance then holds across the boundaries. Packmol works in Å, so this is a unit
boundary.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from ..constants import ANGSTROM_IN_NM
from ..profile import executable
from . import Cancel, Cancelled, PackingError, PackResult, Progress, Structure, make_whole


def find_packmol() -> Path:
    configured = executable("packmol")
    if configured is not None:
        if not configured.is_absolute():
            raise PackingError(f"machine profile: packmol = {configured} must be an absolute path")
        if not (configured.is_file() and os.access(configured, os.X_OK)):
            raise PackingError(f"machine profile: packmol = {configured} is not an executable file")
        return configured
    try:
        from packmol.cli import get_binary_path  # the authors' wheel, extra `packmol`
    except ImportError:
        raise PackingError(
            "Packmol not found: set [executables] packmol in the machine profile, or install "
            "the extra: pip install 'ucmd-builder[packmol]'"
        ) from None
    return Path(get_binary_path())


def packmol_version(exe: Path) -> str:
    """Packmol prints its version in the banner; an empty input then fails harmlessly."""
    try:
        out = subprocess.run(
            [str(exe)], input="", capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PackingError(f"{exe}: cannot run Packmol ({e})") from None
    match = re.search(r"Version\s+([0-9][0-9.]*)", out)
    if not match:
        raise PackingError(f"{exe}: no Packmol version in its output; is this Packmol?")
    return match.group(1)


def _write_xyz(path: Path, elements: tuple[str, ...], xyz_angstrom: np.ndarray) -> None:
    lines = [str(len(elements)), path.stem]
    lines += [
        f"{e} {x:.6f} {y:.6f} {z:.6f}" for e, (x, y, z) in zip(elements, xyz_angstrom, strict=True)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_xyz(path: Path, n: int) -> np.ndarray:
    lines = path.read_text(encoding="utf-8").splitlines()
    if int(lines[0].split()[0]) != n:
        raise PackingError(f"Packmol wrote {lines[0].strip()} atoms, expected {n}")
    return np.array([[float(v) for v in line.split()[1:4]] for line in lines[2 : 2 + n]])


def pack(
    structures: list[Structure],
    box_nm: float,
    tolerance_nm: float,
    seed: int,
    *,
    timeout_s: float = 900.0,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> PackResult:
    exe = find_packmol()
    version = packmol_version(exe)
    major, minor = (int(v) for v in version.split(".")[:2])
    if (major, minor) < (20, 15):
        raise PackingError(
            f"Packmol {version} has no periodic mode; version 20.15 or newer is needed"
        )
    box_a = box_nm / ANGSTROM_IN_NM
    n_atoms = sum(len(s.elements) * s.count for s in structures)
    with tempfile.TemporaryDirectory(prefix="ucmd-packmol-") as tmp:
        work = Path(tmp)
        lines = [
            f"tolerance {tolerance_nm / ANGSTROM_IN_NM:.6f}",
            "filetype xyz",
            "output packed.xyz",
            f"seed {seed}",
            f"pbc 0. 0. 0. {box_a:.6f} {box_a:.6f} {box_a:.6f}",
        ]
        for k, s in enumerate(structures):
            name = f"s{k}.xyz"
            _write_xyz(work / name, s.elements, s.coordinates_nm / ANGSTROM_IN_NM)
            lines += [f"structure {name}", f"  number {s.count}", "end structure"]
        (work / "pack.inp").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log_path = work / "packmol.log"
        with (work / "pack.inp").open("rb") as stdin, log_path.open("wb") as log:
            proc = subprocess.Popen(
                [str(exe)], stdin=stdin, stdout=log, stderr=subprocess.STDOUT, cwd=work
            )
            start = time.monotonic()
            reported = 0
            while proc.poll() is None:
                if cancel is not None and cancel():
                    proc.kill()
                    proc.wait()
                    raise Cancelled("packing cancelled")
                if time.monotonic() - start > timeout_s:
                    proc.kill()
                    proc.wait()
                    raise PackingError(f"Packmol did not finish within {timeout_s:.0f} s")
                if progress is not None:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    if len(text) > reported:
                        tail = [ln.strip() for ln in text[reported:].splitlines() if ln.strip()]
                        if tail:
                            progress(f"packmol: {tail[-1][:100]}")
                        reported = len(text)
                time.sleep(0.2)
        output = log_path.read_text(encoding="utf-8", errors="replace")
        if proc.returncode != 0 or "Success!" not in output:
            last = "\n".join(output.strip().splitlines()[-15:])
            raise PackingError(
                f"Packmol failed (exit status {proc.returncode}). Lower the density or the "
                f"tolerance, or enlarge the box. Last output:\n{last}"
            )
        xyz = _read_xyz(work / "packed.xyz", n_atoms) * ANGSTROM_IN_NM
    sizes = [len(s.elements) for s in structures for _ in range(s.count)]
    positions = make_whole(xyz, sizes, box_nm)
    return PackResult(positions, "packmol", f"Packmol {version}", seed, np.inf)

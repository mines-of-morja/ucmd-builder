"""Writing a built system as a spec v0.1 system package (spec/system-package.md).

The package is written to a temporary directory next to the target and checked with the
reference validator (`ucmd-spec`). Only a valid package is moved into place, so a failed
build never leaves a half-written or invalid package behind.

Atom types are prefixed with their molecule type (`EC.opls_800`). Generators such as
LigParGen number their types per molecule, so the bare names would collide (ADR 0009).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from ucmd_spec import SPEC_VERSION
from ucmd_spec.system import validate_system_package

from .build import BuildError, BuiltMolecule, BuiltSystem

UNITS = {
    "length": "nm",
    "time": "ps",
    "mass": "amu",
    "energy": "kJ/mol",
    "charge": "e",
    "temperature": "K",
    "angle": "deg",
}
PACKAGE_FILES = ("system.json", "positions.npy", "velocities.npy")


def type_name(molecule: str, source_type: str) -> str:
    return f"{molecule}.{source_type}"


def _number(value: float | int) -> float | int:
    return int(value) if isinstance(value, int | np.integer) else float(value)


def _interactions(items: tuple, kind: str) -> list[dict[str, Any]]:
    return [
        {
            "atoms": [int(a) for a in item.atoms],
            "form": item.form,
            "params": {k: _number(v) for k, v in item.params.items()},
        }
        for item in items
    ]


def molecule_type_json(m: BuiltMolecule) -> dict[str, Any]:
    p = m.params
    graph_bonds = set(m.graph.bonds)
    param_bonds = {tuple(sorted(b.atoms)) for b in p.bonds}
    if graph_bonds - param_bonds:
        raise BuildError(
            f"{m.name}: bonds {sorted(graph_bonds - param_bonds)} have no parameters; a "
            "parameterized spec v0.1 package needs parameters for every bond"
        )
    doc: dict[str, Any] = {}
    if m.description:
        doc["description"] = m.description
    doc["formal_charge_e"] = int(m.graph.formal_charge)
    doc["atoms"] = [
        {
            "name": a.name,
            "element": m.graph.elements[k],
            "mass_amu": float(a.mass_amu),
            "charge_e": float(a.charge_e),
            "type": type_name(m.name, a.type),
        }
        for k, a in enumerate(p.atoms)
    ]
    doc["bonds"] = _interactions(p.bonds, "bond")
    for key, items in (
        ("angles", p.angles),
        ("dihedrals", p.dihedrals),
        ("impropers", p.impropers),
    ):
        if items:
            doc[key] = _interactions(items, key)
    if p.constraints:
        doc["constraints"] = [
            {"atoms": [int(c.atoms[0]), int(c.atoms[1])], "length_nm": float(c.length_nm)}
            for c in p.constraints
        ]
    return doc


def forcefield_json(built: BuiltSystem) -> dict[str, Any]:
    c = built.conventions
    families = sorted(
        {m.params.conventions.family for m in built.molecules if m.params.conventions.family}
    )
    sources = sorted({m.params.source_id.split("/")[0].split("#")[0] for m in built.molecules})
    atom_types: dict[str, dict[str, float]] = {}
    for m in built.molecules:
        for source_type in dict.fromkeys(a.type for a in m.params.atoms):
            lj = m.params.lj[source_type]
            atom_types[type_name(m.name, source_type)] = {
                "sigma_nm": float(lj.sigma_nm),
                "epsilon_kjmol": float(lj.epsilon_kjmol),
            }
    return {
        "name": f"{' + '.join(families) or 'force field'} ({', '.join(sources)})",
        "combination_rule": c.combination_rule,
        "scale_14_lj": float(c.scale_14_lj),
        "scale_14_coulomb": float(c.scale_14_coulomb),
        "atom_types": atom_types,
    }


def system_document(built: BuiltSystem, created_utc: str) -> dict[str, Any]:
    names = [m.name for m in built.molecules]
    if len(set(names)) != len(names):
        raise BuildError(f"molecule type names must be unique: {names}")
    L = float(built.box_nm)
    doc: dict[str, Any] = {
        "format": "ucmd-system",
        "spec_version": SPEC_VERSION,
        "name": built.name,
    }
    if built.description:
        doc["description"] = built.description
    provenance = {"created_by": built.provenance["created_by"], "created_utc": created_utc}
    provenance.update({k: v for k, v in built.provenance.items() if k != "created_by"})
    doc.update(
        {
            "units": UNITS,
            "periodicity": [True, True, True],
            "cell_nm": [[L, 0.0, 0.0], [0.0, L, 0.0], [0.0, 0.0, L]],
            "molecule_types": {m.name: molecule_type_json(m) for m in built.molecules},
            "components": [
                {"molecule_type": m.name, "count": int(n)}
                for m, n in zip(built.molecules, built.counts, strict=True)
            ],
            "atom_count": built.atom_count,
            "forcefield": forcefield_json(built),
            "provenance": provenance,
        }
    )
    return doc


def _save(path: Path, array: np.ndarray) -> None:
    data = np.ascontiguousarray(array, dtype="<f8")
    with path.open("wb") as f:
        np.lib.format.write_array(f, data, version=(1, 0), allow_pickle=False)


@contextmanager
def _spec_dir() -> Iterator[None]:
    """Make the validator find the spec's JSON Schemas.

    ucmd-spec 0.1.0 reads its schemas from `spec/` of a UnitcellMD checkout (`$UCMD_SPEC_DIR`,
    else its own source tree), so a plain install from the git tag cannot find them. Until the
    validator packages its schemas, the builder points it at the workspace's `../spec`, as the
    engine's contract tests do.
    """
    if os.environ.get("UCMD_SPEC_DIR"):
        yield
        return
    workspace_spec = Path(__file__).resolve().parents[3] / "spec"
    if not (workspace_spec / "schemas" / "system.schema.json").is_file():
        yield
        return
    os.environ["UCMD_SPEC_DIR"] = str(workspace_spec)
    try:
        yield
    finally:
        os.environ.pop("UCMD_SPEC_DIR", None)


def validate(directory: Path) -> None:
    """Run the reference validator; any error is a builder bug and stops the build."""
    try:
        with _spec_dir():
            report = validate_system_package(directory)
    except FileNotFoundError as e:
        raise BuildError(
            f"the spec validator cannot find the spec's JSON Schemas ({e.filename}); set "
            "UCMD_SPEC_DIR to the spec/ directory of a UnitcellMD checkout at tag spec-v0.1.0"
        ) from None
    if not report.ok:
        issue = report.error
        assert issue is not None
        raise BuildError(
            f"the builder produced an invalid package ({issue.code}: {issue.message} at "
            f"{issue.location}); this is a builder bug, please report it"
        )


def write_package(
    built: BuiltSystem,
    directory: Path | str,
    *,
    overwrite: bool = False,
    created_utc: str | None = None,
) -> Path:
    """Write and validate the package; return its path."""
    target = Path(directory)
    if target.exists():
        if not overwrite:
            raise BuildError(f"{target} exists; use --force to replace it")
        if not target.is_dir() or not (target / "system.json").is_file():
            raise BuildError(f"{target} exists and is not a system package; not replacing it")
        extra = sorted(
            p.name for p in target.iterdir() if p.name not in (*PACKAGE_FILES, "README.md")
        )
        if extra:
            raise BuildError(
                f"{target} contains other files ({', '.join(extra)}); not replacing it"
            )
    stamp = created_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = system_document(built, stamp)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    umask = os.umask(0)
    os.umask(umask)
    tmp.chmod(0o777 & ~umask)  # mkdtemp creates 0700; a package is an ordinary directory
    try:
        (tmp / "system.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )
        _save(tmp / "positions.npy", built.positions_nm)
        if built.velocities_nm_ps is not None:
            _save(tmp / "velocities.npy", built.velocities_nm_ps)
        validate(tmp)
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return target

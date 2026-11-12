"""The built-in source `library`: curated parameter sets with citations (design/B1.md §4.4).

A data set is a directory `ucmd_builder/forcefields/<set>/` holding `set.toml` (name,
family, conventions, citations, data licence) and one `<molecule>.toml` per template.
Template values are stored as printed in their source, with declared units, and converted
to spec units here, exactly once.

Each template carries its reference geometry. The geometry travels with the parameters:
when a template is mapped onto a molecule, the coordinates are reordered with it. This
matters for terms that depend on geometry rather than on the graph, such as the cis-only
F–P–F angles of PF₆⁻ (ADR 0009).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ..molecule import MoleculeGraph
from .api import (
    LJ,
    AtomParams,
    Citation,
    Constraint,
    Conventions,
    Interaction,
    Parameterized,
    SourceError,
    check_options,
)
from .matching import find_mapping
from .units import Units

SET_KEYS = {"name", "family", "version", "description", "conventions", "citations", "licence"}
TEMPLATE_KEYS = {
    "name",
    "description",
    "smiles",
    "formal_charge",
    "units",
    "citations",
    "generation",
    "atoms",
    "lj",
    "bonds",
    "angles",
    "dihedrals",
    "impropers",
    "constraints",
}


def data_root() -> Path:
    """Directory of the built-in data sets."""
    return Path(str(resources.files("ucmd_builder") / "forcefields"))


@dataclass(frozen=True)
class DataSet:
    name: str
    family: str
    version: int
    description: str
    conventions: Conventions
    citations: tuple[Citation, ...]
    licence: str
    directory: Path

    def template_names(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.toml") if p.name != "set.toml")


@dataclass(frozen=True, eq=False)
class Template:
    """A parameterized molecule in template order, in spec units."""

    data_set: DataSet
    name: str
    description: str
    graph: MoleculeGraph
    parameters: Parameterized


def _citations(items: list[dict[str, Any]], where: str) -> tuple[Citation, ...]:
    out = []
    for item in items:
        unknown = set(item) - {"what", "citation", "doi", "url"}
        if unknown or "what" not in item or "citation" not in item:
            raise SourceError(
                f"{where}: a citation needs `what` and `citation` (got {sorted(item)})"
            )
        out.append(Citation(item["what"], item["citation"], item.get("doi"), item.get("url")))
    return tuple(out)


def list_sets() -> list[str]:
    return sorted(p.name for p in data_root().iterdir() if (p / "set.toml").is_file())


@cache
def load_set(name: str) -> DataSet:
    directory = data_root() / name
    path = directory / "set.toml"
    if not path.is_file():
        raise SourceError(f"library: no data set {name!r}; available: {', '.join(list_sets())}")
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(doc) - SET_KEYS
    if unknown:
        raise SourceError(f"{path}: unknown key(s) {sorted(unknown)}")
    conv = doc["conventions"]
    return DataSet(
        name=doc["name"],
        family=doc["family"],
        version=int(doc["version"]),
        description=doc.get("description", ""),
        conventions=Conventions(
            conv["combination_rule"],
            float(conv["scale_14_lj"]),
            float(conv["scale_14_coulomb"]),
            doc["family"],
        ),
        citations=_citations(doc.get("citations", []), str(path)),
        licence=doc.get("licence", ""),
        directory=directory,
    )


def _interaction(kind: str, item: dict[str, Any], units: Units, where: str) -> Interaction:
    atoms = tuple(int(a) for a in item["atoms"])
    form = item["form"]
    if kind == "bond" and form == "harmonic":
        params = {"r0_nm": units.length_nm(item["r0"]), "k_kjmol_nm2": units.bond_k(item["k"])}
    elif kind == "angle" and form == "harmonic":
        params = {"theta0_deg": float(item["theta0"]), "k_kjmol_rad2": units.angle_k(item["k"])}
    elif kind in ("dihedral", "improper") and form == "periodic":
        params = {
            "k_kjmol": units.energy_kjmol(item["k"]),
            "n": int(item["n"]),
            "phi0_deg": float(item["phi0"]),
        }
    elif kind == "dihedral" and form == "ryckaert-bellemans":
        c = item["c"]
        if len(c) != 6:
            raise SourceError(f"{where}: Ryckaert–Bellemans needs six coefficients c0…c5")
        params = {f"c{i}_kjmol": units.energy_kjmol(v) for i, v in enumerate(c)}
    elif kind == "dihedral" and form == "opls-fourier":
        v = item["v"]
        if len(v) != 4:
            raise SourceError(f"{where}: OPLS Fourier needs four coefficients v1…v4")
        params = {f"v{i + 1}_kjmol": units.energy_kjmol(x) for i, x in enumerate(v)}
    elif kind == "improper" and form == "harmonic":
        params = {"xi0_deg": float(item["xi0"]), "k_kjmol_rad2": units.angle_k(item["k"])}
    else:
        raise SourceError(f"{where}: unsupported {kind} form {form!r}")
    return Interaction(atoms, form, params)


def _load_template(data_set: DataSet, name: str) -> Template:
    path = data_set.directory / f"{name}.toml"
    if not path.is_file():
        raise SourceError(
            f"library: set {data_set.name!r} has no template {name!r}; "
            f"available: {', '.join(data_set.template_names())}"
        )
    where = str(path)
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(doc) - TEMPLATE_KEYS
    if unknown:
        raise SourceError(f"{where}: unknown key(s) {sorted(unknown)}")
    units = Units.parse(doc.get("units", {}), where)
    raw_atoms = doc["atoms"]
    atoms = []
    xyz = []
    for a in raw_atoms:
        atoms.append(AtomParams(a["name"], a["type"], float(a["charge"]), float(a["mass"])))
        xyz.append([units.length_nm(v) for v in a["xyz"]])
    lj = {}
    for type_name, entry in doc.get("lj", {}).items():
        if "epsilon_over_kB" in entry:
            epsilon = Units.kelvin_to_kjmol(entry["epsilon_over_kB"])
        else:
            epsilon = units.energy_kjmol(entry["epsilon"])
        lj[type_name] = LJ(units.length_nm(entry["sigma"]), epsilon)
    missing = sorted({a.type for a in atoms} - set(lj))
    if missing:
        raise SourceError(f"{where}: no Lennard-Jones parameters for type(s) {missing}")
    groups = {
        kind: tuple(_interaction(kind, item, units, where) for item in doc.get(key, []))
        for kind, key in (
            ("bond", "bonds"),
            ("angle", "angles"),
            ("dihedral", "dihedrals"),
            ("improper", "impropers"),
        )
    }
    constraints = tuple(
        Constraint((int(c["atoms"][0]), int(c["atoms"][1])), units.length_nm(c["length"]))
        for c in doc.get("constraints", [])
    )
    elements = [a["element"] for a in raw_atoms]
    graph = MoleculeGraph.make(
        name,
        elements,
        [b.atoms for b in groups["bond"]],
        _formal_charges(elements, int(doc["formal_charge"])),
        np.array(xyz, dtype=np.float64),
        doc.get("smiles"),
    )
    parameters = Parameterized(
        atoms=tuple(atoms),
        lj=lj,
        bonds=groups["bond"],
        angles=groups["angle"],
        dihedrals=groups["dihedral"],
        impropers=groups["improper"],
        constraints=constraints,
        conventions=data_set.conventions,
        citations=data_set.citations + _citations(doc.get("citations", []), where),
        source_id=f"library:{data_set.name}/{name}@{data_set.version}",
        coordinates_nm=np.array(xyz, dtype=np.float64),
        description=doc.get("description", ""),
        record={
            "source": "library",
            "set": data_set.name,
            "set_version": data_set.version,
            "template": name,
            "units_as_printed": units.label,
            **({"generation": doc["generation"]} if "generation" in doc else {}),
        },
    )
    return Template(data_set, name, doc.get("description", ""), graph, parameters)


def _formal_charges(elements: list[str], total: int) -> list[int]:
    """Per-atom formal charges are not needed for parameters; the total goes on one atom."""
    charges = [0] * len(elements)
    if charges:
        charges[0] = total
    return charges


@cache
def load_template(set_name: str, name: str) -> Template:
    return _load_template(load_set(set_name), name)


class LibrarySource:
    """Parameters from a curated data set, assigned by graph isomorphism."""

    name: ClassVar[str] = "library"
    version: ClassVar[str] = "1"
    OPTIONS: ClassVar[set[str]] = {"set", "template"}

    def parameterize(
        self, molecule: MoleculeGraph, options: Mapping[str, Any], *, base_dir: Path
    ) -> Parameterized:
        check_options(self.name, options, self.OPTIONS)
        if "set" not in options:
            raise SourceError(
                f"library: option `set` is required; available: {', '.join(list_sets())}"
            )
        data_set = load_set(str(options["set"]))
        names = [str(options["template"])] if "template" in options else data_set.template_names()
        hits = []
        for name in names:
            template = load_template(data_set.name, name)
            mapping = find_mapping(
                template.graph.elements, template.graph.bonds, molecule.elements, molecule.bonds
            )
            if mapping is not None:
                hits.append((template, mapping))
        if not hits:
            raise SourceError(
                f"library: no template in set {data_set.name!r} matches {molecule.name} "
                f"({molecule.formula()}, {len(molecule.bonds)} bonds)"
            )
        if len(hits) > 1:
            raise SourceError(
                f"library: {molecule.name} matches several templates "
                f"({', '.join(t.name for t, _ in hits)}); choose one with `template`"
            )
        template, mapping = hits[0]
        if template.graph.formal_charge != molecule.formal_charge:
            raise SourceError(
                f"library: {molecule.name} has formal charge {molecule.formal_charge}, "
                f"template {template.name} has {template.graph.formal_charge}"
            )
        # mapping[t] = molecule index of template atom t; we need, for each molecule atom k,
        # the template atom that lands there.
        order = [0] * len(mapping)
        for t, k in enumerate(mapping):
            order[k] = t
        return template.parameters.reordered(order)

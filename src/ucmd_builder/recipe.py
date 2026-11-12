"""The build recipe `build.toml` (PLAN.md B1.1, design/B1.md §3).

A recipe is the single source of truth for a system: the CLI builds it, and the GUI (B2)
edits it. Parsing is strict: unknown keys are errors, as in `run.toml`. `to_toml` writes a
recipe back so that parsing the result gives an equal recipe.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

FORMAT = "ucmd-build"
VERSION = "0.1"
NAME = re.compile(r"^[A-Za-z0-9_.+-]+$")


class RecipeError(ValueError):
    """The recipe is invalid; the message names the key."""


@dataclass(frozen=True)
class MoleculeSpec:
    """Where a component's molecule comes from: a library template, SMILES or a file."""

    kind: str  # "library" | "smiles" | "file"
    value: str
    set: str | None = None
    charge: int = 0


@dataclass(frozen=True)
class Component:
    name: str
    molecule: MoleculeSpec
    count: int | None = None
    forcefield: Mapping[str, Any] | None = None
    charge_scale: float | None = None
    description: str = ""


@dataclass(frozen=True)
class Density:
    value_g_cm3: float
    source: str

    @property
    def is_placeholder(self) -> bool:
        return self.source.strip().lower().startswith("placeholder")


@dataclass(frozen=True)
class Composition:
    salt: tuple[str, str]
    solvent: Mapping[str, float]
    basis: str = "mass"
    molarity_mol_L: float | None = None
    molality_mol_kg: float | None = None
    solvent_molecules: int | None = None
    ion_pairs: int | None = None
    density: Density | None = None
    solvent_densities_g_cm3: Mapping[str, float] | None = None
    solvent_densities_source: str | None = None


@dataclass(frozen=True)
class Box:
    length_nm: float | None = None
    density: Density | None = None


@dataclass(frozen=True)
class Packing:
    method: str = "packmol"
    tolerance_nm: float = 0.2
    timeout_s: float = 900.0
    max_attempts: int = 2000


@dataclass(frozen=True)
class Velocities:
    temperature_K: float


@dataclass(frozen=True)
class Recipe:
    name: str
    seed: int
    components: tuple[Component, ...]
    description: str = ""
    forcefield: Mapping[str, Any] | None = None
    composition: Composition | None = None
    box: Box | None = None
    packing: Packing = field(default_factory=Packing)
    velocities: Velocities | None = None
    text: str = field(default="", compare=False)
    path: Path | None = field(default=None, compare=False)

    @property
    def directory(self) -> Path:
        return self.path.parent if self.path is not None else Path.cwd()

    def component(self, name: str) -> Component:
        for c in self.components:
            if c.name == name:
                return c
        raise RecipeError(f"no component {name!r}")


# ---------------------------------------------------------------------------------------------
# Parsing


def load_recipe(path: Path | str) -> Recipe:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RecipeError(f"{path}: {e.strerror}") from None
    return parse_recipe(text, path=path)


def parse_recipe(text: str, *, path: Path | None = None) -> Recipe:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RecipeError(f"{path or 'recipe'}: not valid TOML: {e}") from None
    return _recipe(doc, text, path)


def _keys(table: Mapping[str, Any], where: str, required: set[str], optional: set[str]) -> None:
    missing = sorted(required - set(table))
    if missing:
        raise RecipeError(f"{where}: missing key(s) {', '.join(missing)}")
    unknown = sorted(set(table) - required - optional)
    if unknown:
        raise RecipeError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _positive(value: Any, where: str, *, integer: bool = False) -> Any:
    ok = isinstance(value, int) if integer else isinstance(value, int | float)
    if isinstance(value, bool) or not ok or not math.isfinite(value) or value <= 0:
        kind = "a positive integer" if integer else "a positive number"
        raise RecipeError(f"{where} must be {kind}, got {value!r}")
    return int(value) if integer else float(value)


def _density(table: Any, where: str) -> Density:
    if not isinstance(table, Mapping):
        raise RecipeError(f'{where} must be a table {{ value = …, source = "…" }}')
    _keys(table, where, {"value", "source"}, set())
    source = table["source"]
    if not isinstance(source, str) or not source.strip():
        raise RecipeError(
            f"{where}.source must name where the density comes from (or 'placeholder …')"
        )
    return Density(_positive(table["value"], f"{where}.value"), source)


def _recipe(doc: dict[str, Any], text: str, path: Path | None) -> Recipe:
    _keys(
        doc,
        "recipe",
        {"format", "version", "name", "seed", "component"},
        {"description", "forcefield", "composition", "box", "packing", "velocities"},
    )
    if doc["format"] != FORMAT:
        raise RecipeError(f"format must be {FORMAT!r}")
    if doc["version"] != VERSION:
        raise RecipeError(
            f"version {doc['version']!r} is not supported (this builder reads {VERSION})"
        )
    name = doc["name"]
    if not isinstance(name, str) or not NAME.match(name):
        raise RecipeError(f"name {name!r} must match {NAME.pattern}")
    seed = doc["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        raise RecipeError(f"seed must be an integer ≥ 0, got {seed!r}")
    components = tuple(_component(c, i) for i, c in enumerate(doc["component"]))
    names = [c.name for c in components]
    if len(set(names)) != len(names):
        raise RecipeError(f"component names must be unique: {names}")
    forcefield = doc.get("forcefield")
    if forcefield is not None and (
        not isinstance(forcefield, Mapping) or "source" not in forcefield
    ):
        raise RecipeError('[forcefield] needs `source` (e.g. "library")')
    composition = _composition(doc["composition"], names) if "composition" in doc else None
    box = _box(doc["box"]) if "box" in doc else None
    packing = _packing(doc.get("packing", {}))
    velocities = None
    if "velocities" in doc:
        _keys(doc["velocities"], "[velocities]", {"temperature_K"}, set())
        velocities = Velocities(
            _positive(doc["velocities"]["temperature_K"], "velocities.temperature_K")
        )
    recipe = Recipe(
        name=name,
        seed=seed,
        components=components,
        description=doc.get("description", ""),
        forcefield=forcefield,
        composition=composition,
        box=box,
        packing=packing,
        velocities=velocities,
        text=text,
        path=path,
    )
    _check_sizes(recipe)
    return recipe


def _component(table: Any, index: int) -> Component:
    where = f"component[{index}]"
    if not isinstance(table, Mapping):
        raise RecipeError(f"{where} must be a table")
    _keys(
        table, where, {"name", "molecule"}, {"count", "forcefield", "charge_scale", "description"}
    )
    name = table["name"]
    if not isinstance(name, str) or not NAME.match(name):
        raise RecipeError(f"{where}.name {name!r} must match {NAME.pattern}")
    mol = table["molecule"]
    if not isinstance(mol, Mapping):
        raise RecipeError(f'{where}.molecule must be a table, e.g. {{ library = "EC" }}')
    kinds = [k for k in ("library", "smiles", "file") if k in mol]
    if len(kinds) != 1:
        raise RecipeError(f"{where}.molecule needs exactly one of library, smiles, file")
    _keys(mol, f"{where}.molecule", {kinds[0]}, {"set", "charge"})
    if "set" in mol and kinds[0] != "library":
        raise RecipeError(f"{where}.molecule.set only applies to library molecules")
    charge = mol.get("charge", 0)
    if isinstance(charge, bool) or not isinstance(charge, int):
        raise RecipeError(f"{where}.molecule.charge must be an integer")
    molecule = MoleculeSpec(kinds[0], str(mol[kinds[0]]), mol.get("set"), charge)
    count = _positive(table["count"], f"{where}.count", integer=True) if "count" in table else None
    forcefield = table.get("forcefield")
    if forcefield is not None and (
        not isinstance(forcefield, Mapping) or "source" not in forcefield
    ):
        raise RecipeError(f"{where}.forcefield needs `source`")
    scale = None
    if "charge_scale" in table:
        scale = _positive(table["charge_scale"], f"{where}.charge_scale")
        if scale > 1.0:
            raise RecipeError(f"{where}.charge_scale must lie in (0, 1], got {scale}")
    return Component(name, molecule, count, forcefield, scale, table.get("description", ""))


def _composition(table: Any, names: list[str]) -> Composition:
    where = "[composition]"
    _keys(
        table,
        where,
        {"salt", "solvent"},
        {
            "basis",
            "molarity_mol_L",
            "molality_mol_kg",
            "solvent_molecules",
            "ion_pairs",
            "density_g_cm3",
            "solvent_densities_g_cm3",
            "solvent_densities_source",
        },
    )
    salt = table["salt"]
    if not (isinstance(salt, list) and len(salt) == 2 and all(s in names for s in salt)):
        raise RecipeError(f"{where}.salt must name two components (cation, anion), got {salt!r}")
    solvent = table["solvent"]
    if not isinstance(solvent, Mapping) or not solvent or any(s not in names for s in solvent):
        raise RecipeError(f"{where}.solvent must map component names to fractions")
    fractions = {k: _positive(v, f"{where}.solvent.{k}") for k, v in solvent.items()}
    if abs(sum(fractions.values()) - 1.0) > 1e-6:
        raise RecipeError(f"{where}.solvent fractions must sum to 1, got {sum(fractions.values())}")
    basis = table.get("basis", "mass")
    if basis not in ("mass", "volume"):
        raise RecipeError(f"{where}.basis must be 'mass' or 'volume'")
    concentration = [k for k in ("molarity_mol_L", "molality_mol_kg") if k in table]
    if len(concentration) != 1:
        raise RecipeError(f"{where} needs exactly one of molarity_mol_L, molality_mol_kg")
    size = [k for k in ("solvent_molecules", "ion_pairs") if k in table]
    if len(size) != 1:
        raise RecipeError(f"{where} needs exactly one of solvent_molecules, ion_pairs")
    densities = table.get("solvent_densities_g_cm3")
    if basis == "volume":
        if not isinstance(densities, Mapping) or set(densities) != set(fractions):
            raise RecipeError(
                f"{where}: basis 'volume' needs solvent_densities_g_cm3 for every solvent"
            )
        if not table.get("solvent_densities_source"):
            raise RecipeError(
                f"{where}: solvent_densities_source must cite the pure-solvent densities"
            )
        densities = {
            k: _positive(v, f"{where}.solvent_densities_g_cm3.{k}") for k, v in densities.items()
        }
    return Composition(
        salt=(salt[0], salt[1]),
        solvent=fractions,
        basis=basis,
        molarity_mol_L=_positive(table["molarity_mol_L"], f"{where}.molarity_mol_L")
        if "molarity_mol_L" in table
        else None,
        molality_mol_kg=_positive(table["molality_mol_kg"], f"{where}.molality_mol_kg")
        if "molality_mol_kg" in table
        else None,
        solvent_molecules=_positive(
            table["solvent_molecules"], f"{where}.solvent_molecules", integer=True
        )
        if "solvent_molecules" in table
        else None,
        ion_pairs=_positive(table["ion_pairs"], f"{where}.ion_pairs", integer=True)
        if "ion_pairs" in table
        else None,
        density=_density(table["density_g_cm3"], f"{where}.density_g_cm3")
        if "density_g_cm3" in table
        else None,
        solvent_densities_g_cm3=densities,
        solvent_densities_source=table.get("solvent_densities_source"),
    )


def _box(table: Any) -> Box:
    _keys(table, "[box]", set(), {"length_nm", "density_g_cm3"})
    if ("length_nm" in table) == ("density_g_cm3" in table):
        raise RecipeError("[box] needs exactly one of length_nm, density_g_cm3")
    if "length_nm" in table:
        return Box(length_nm=_positive(table["length_nm"], "box.length_nm"))
    return Box(density=_density(table["density_g_cm3"], "box.density_g_cm3"))


def _packing(table: Any) -> Packing:
    _keys(table, "[packing]", set(), {"method", "tolerance_nm", "timeout_s", "max_attempts"})
    method = table.get("method", "packmol")
    if method not in ("packmol", "insertion"):
        raise RecipeError("packing.method must be 'packmol' or 'insertion'")
    return Packing(
        method=method,
        tolerance_nm=_positive(table.get("tolerance_nm", 0.2), "packing.tolerance_nm"),
        timeout_s=_positive(table.get("timeout_s", 900.0), "packing.timeout_s"),
        max_attempts=_positive(
            table.get("max_attempts", 2000), "packing.max_attempts", integer=True
        ),
    )


def _check_sizes(recipe: Recipe) -> None:
    comp = recipe.composition
    governed = set(comp.salt) | set(comp.solvent) if comp else set()
    for c in recipe.components:
        if c.name in governed and c.count is not None:
            raise RecipeError(
                f"component {c.name}: its count comes from [composition]; remove `count`"
            )
        if c.name not in governed and c.count is None:
            raise RecipeError(
                f"component {c.name}: `count` is required (it is not in [composition])"
            )
    if comp is not None and comp.molarity_mol_L is not None and comp.density is None:
        raise RecipeError(
            "[composition]: a molarity needs density_g_cm3 (solution density with its source)"
        )
    has_box = recipe.box is not None or (comp is not None and comp.density is not None)
    if not has_box:
        raise RecipeError("the box size is undefined: add [box] or [composition].density_g_cm3")
    if recipe.box is not None and comp is not None and comp.density is not None:
        raise RecipeError("give the box size once: either [box] or [composition].density_g_cm3")


# ---------------------------------------------------------------------------------------------
# Writing


def to_dict(recipe: Recipe) -> dict[str, Any]:
    doc: dict[str, Any] = {"format": FORMAT, "version": VERSION, "name": recipe.name}
    if recipe.description:
        doc["description"] = recipe.description
    doc["seed"] = recipe.seed
    if recipe.forcefield is not None:
        doc["forcefield"] = dict(recipe.forcefield)
    if recipe.composition is not None:
        c = recipe.composition
        comp: dict[str, Any] = {"salt": list(c.salt), "solvent": dict(c.solvent), "basis": c.basis}
        for key in ("molarity_mol_L", "molality_mol_kg", "solvent_molecules", "ion_pairs"):
            if getattr(c, key) is not None:
                comp[key] = getattr(c, key)
        if c.density is not None:
            comp["density_g_cm3"] = {"value": c.density.value_g_cm3, "source": c.density.source}
        if c.solvent_densities_g_cm3 is not None:
            comp["solvent_densities_g_cm3"] = dict(c.solvent_densities_g_cm3)
        if c.solvent_densities_source is not None:
            comp["solvent_densities_source"] = c.solvent_densities_source
        doc["composition"] = comp
    if recipe.box is not None:
        if recipe.box.length_nm is not None:
            doc["box"] = {"length_nm": recipe.box.length_nm}
        elif recipe.box.density is not None:
            d = recipe.box.density
            doc["box"] = {"density_g_cm3": {"value": d.value_g_cm3, "source": d.source}}
    p = recipe.packing
    doc["packing"] = {
        "method": p.method,
        "tolerance_nm": p.tolerance_nm,
        "timeout_s": p.timeout_s,
        "max_attempts": p.max_attempts,
    }
    if recipe.velocities is not None:
        doc["velocities"] = {"temperature_K": recipe.velocities.temperature_K}
    components = []
    for c in recipe.components:
        mol: dict[str, Any] = {c.molecule.kind: c.molecule.value}
        if c.molecule.set is not None:
            mol["set"] = c.molecule.set
        if c.molecule.charge:
            mol["charge"] = c.molecule.charge
        entry: dict[str, Any] = {"name": c.name, "molecule": mol}
        if c.description:
            entry["description"] = c.description
        if c.count is not None:
            entry["count"] = c.count
        if c.forcefield is not None:
            entry["forcefield"] = dict(c.forcefield)
        if c.charge_scale is not None:
            entry["charge_scale"] = c.charge_scale
        components.append(entry)
    doc["component"] = components
    return doc


def to_toml(recipe: Recipe) -> str:
    return tomli_w.dumps(to_dict(recipe))

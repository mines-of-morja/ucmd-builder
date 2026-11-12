"""From a recipe to a built system: the one entry point for the CLI and the GUI.

`build(recipe)` resolves the molecules, parameterizes them through their force-field
sources (ADR 0008), applies transforms (ion charge scaling ζ), computes the composition and
the box, packs the molecules and draws velocities. The result is a `BuiltSystem`, which
`package.write_package` turns into a validated system package.

Determinism (B1.8): every random number derives from the recipe seed through NumPy
`SeedSequence`, and the external programs' versions are recorded.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__
from .composition import CompositionResult, compose
from .constants import G_PER_CM3_IN_AMU_PER_NM3
from .forcefield import registry
from .forcefield.api import Conventions, Parameterized, SourceError
from .forcefield.library import load_template
from .initial import maxwell_boltzmann
from .molecule import MoleculeGraph, from_file, from_smiles
from .packing import Cancel, PackingError, PackResult, Progress, Structure, insertion
from .packing import packmol as packmol_driver
from .packing.checks import min_intermolecular_distance
from .recipe import Component, Recipe, RecipeError


class BuildError(RuntimeError):
    """The build failed; the message says why."""


@dataclass(frozen=True, eq=False)
class BuiltMolecule:
    """One molecule type of the package: graph and parameters in the same atom order."""

    name: str
    description: str
    graph: MoleculeGraph
    params: Parameterized

    @property
    def mass_amu(self) -> float:
        return float(sum(a.mass_amu for a in self.params.atoms))


@dataclass(frozen=True, eq=False)
class BuiltSystem:
    name: str
    description: str
    molecules: tuple[BuiltMolecule, ...]
    counts: tuple[int, ...]
    box_nm: float
    positions_nm: np.ndarray
    velocities_nm_ps: np.ndarray | None
    conventions: Conventions
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def atom_count(self) -> int:
        return sum(m.graph.n_atoms * n for m, n in zip(self.molecules, self.counts, strict=True))


def _git_hash() -> str | None:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ("-dirty" if dirty else "")


def created_by() -> str:
    git = _git_hash()
    return f"ucmd-builder {__version__}" + (f" (git {git})" if git else "")


# ---------------------------------------------------------------------------------------------
# Molecules and parameters


def _source_options(component: Component, recipe: Recipe) -> tuple[str, dict[str, Any]]:
    """The source name and options for a component (component override, else recipe default)."""
    table = component.forcefield or recipe.forcefield
    mol = component.molecule
    if table is None:
        if mol.kind == "library" and mol.set:
            return "library", {"set": mol.set, "template": mol.value}
        raise RecipeError(
            f"component {component.name}: no force-field source; add [forcefield] or "
            "component.forcefield"
        )
    options = {k: v for k, v in table.items() if k != "source"}
    name = str(table["source"])
    if mol.kind == "library" and name == "library":
        options.setdefault("template", mol.value)
        if mol.set:
            options["set"] = mol.set
    return name, options


def _molecule(component: Component, recipe: Recipe, seed: int) -> MoleculeGraph:
    mol = component.molecule
    if mol.kind == "smiles":
        return from_smiles(mol.value, component.name, seed=seed)
    if mol.kind == "file":
        return from_file(recipe.directory / mol.value, component.name, charge=mol.charge)
    data_set = mol.set
    if data_set is None:
        table = component.forcefield or recipe.forcefield or {}
        data_set = table.get("set") if table.get("source") == "library" else None
    if data_set is None:
        raise RecipeError(
            f"component {component.name}: library molecule needs a data set (molecule.set)"
        )
    template = load_template(str(data_set), mol.value)
    g = template.graph
    return MoleculeGraph.make(
        component.name, g.elements, g.bonds, g.formal_charges, g.coordinates_nm, g.smiles
    )


def _parameterize(
    component: Component, recipe: Recipe, graph: MoleculeGraph
) -> tuple[Parameterized, dict]:
    name, options = _source_options(component, recipe)
    source = registry.get(name)
    params = source.parameterize(graph, options, base_dir=recipe.directory)
    info = registry.available()[name]
    record = {
        "component": component.name,
        "source": name,
        "source_version": source.version,
        "distribution": info.distribution,
        "source_id": params.source_id,
        **{k: v for k, v in params.record.items() if k != "source"},
    }
    return params, record


def _transform(component: Component, params: Parameterized) -> tuple[Parameterized, list[dict]]:
    if component.charge_scale is None or component.charge_scale == 1.0:
        return params, []
    scaled = params.with_charges_scaled(component.charge_scale)
    return scaled, [
        {
            "kind": "charge_scale",
            "component": component.name,
            "factor": component.charge_scale,
            "net_charge_e": round(scaled.net_charge_e, 12),
        }
    ]


def _geometry(m: BuiltMolecule) -> np.ndarray:
    """The reference geometry for packing: the source's, else the input's."""
    if m.params.coordinates_nm is not None:
        return np.asarray(m.params.coordinates_nm)
    if m.graph.coordinates_nm is not None:
        return np.asarray(m.graph.coordinates_nm)
    raise BuildError(
        f"{m.name}: no geometry; give the source coordinates (e.g. gromacs-top `coordinates`) "
        "or a molecule file with coordinates"
    )


# ---------------------------------------------------------------------------------------------
# The build


def build(
    recipe: Recipe, *, progress: Progress | None = None, cancel: Cancel | None = None
) -> BuiltSystem:
    say = progress or (lambda _msg: None)
    seq = np.random.SeedSequence(recipe.seed)
    seed_molecules, seed_packing, seed_velocities = (
        int(s.generate_state(1, dtype=np.uint32)[0] & 0x7FFFFFFF) for s in seq.spawn(3)
    )

    molecules: list[BuiltMolecule] = []
    sources: list[dict] = []
    transforms: list[dict] = []
    for k, component in enumerate(recipe.components):
        say(f"parameterizing {component.name}")
        try:
            graph = _molecule(component, recipe, seed_molecules + k)
            params, record = _parameterize(component, recipe, graph)
        except SourceError as e:
            raise BuildError(f"component {component.name}: {e}") from None
        params, done = _transform(component, params)
        molecules.append(
            BuiltMolecule(
                component.name, component.description or params.description, graph, params
            )
        )
        sources.append(record)
        transforms += done

    conventions = molecules[0].params.conventions
    for m in molecules[1:]:
        if not m.params.conventions.same_physics(conventions):
            listing = "; ".join(
                f"{x.name}: {x.params.conventions.combination_rule}, 1-4 LJ "
                f"{x.params.conventions.scale_14_lj}, "
                f"1-4 Coulomb {x.params.conventions.scale_14_coulomb}"
                for x in molecules
            )
            raise BuildError(
                f"the components' force-field conventions differ ({listing}); "
                "one package uses one set (ADR 0008 §3)"
            )

    masses = {m.name: m.mass_amu for m in molecules}
    composition: CompositionResult | None = None
    if recipe.composition is not None:
        composition = compose(recipe.composition, masses)
    counts = tuple(
        c.count if c.count is not None else composition.counts[c.name]  # type: ignore[union-attr]
        for c in recipe.components
    )
    total_mass = sum(masses[c.name] * n for c, n in zip(recipe.components, counts, strict=True))

    box_record: dict[str, Any] = {}
    if composition is not None and composition.box_length_nm is not None:
        box_nm = composition.box_length_nm
        density = recipe.composition.density  # type: ignore[union-attr]
        box_record = {
            "from": "composition",
            "density_g_cm3": density.value_g_cm3,
            "density_source": density.source,
        }
    elif recipe.box is not None and recipe.box.length_nm is not None:
        box_nm = recipe.box.length_nm
        box_record = {"from": "box.length_nm"}
    elif recipe.box is not None and recipe.box.density is not None:
        density = recipe.box.density
        box_nm = (total_mass / (density.value_g_cm3 * G_PER_CM3_IN_AMU_PER_NM3)) ** (1.0 / 3.0)
        box_record = {
            "from": "box.density_g_cm3",
            "density_g_cm3": density.value_g_cm3,
            "density_source": density.source,
        }
    else:
        raise RecipeError("the box size is undefined")
    box_record["length_nm"] = box_nm
    notes = []
    if box_record.get("density_source", "").strip().lower().startswith("placeholder"):
        notes.append(
            f"the density is a placeholder ({box_record['density_source']}); not for production"
        )

    structures = [
        Structure(m.name, m.graph.elements, _geometry(m), n)
        for m, n in zip(molecules, counts, strict=True)
    ]
    packing = recipe.packing
    say(f"packing {sum(counts)} molecules into a {box_nm:.4f} nm box ({packing.method})")
    try:
        if packing.method == "packmol":
            result = packmol_driver.pack(
                structures,
                box_nm,
                packing.tolerance_nm,
                seed_packing,
                timeout_s=packing.timeout_s,
                progress=progress,
                cancel=cancel,
            )
        else:
            result = insertion.insert(
                structures,
                box_nm,
                packing.tolerance_nm,
                seed_packing,
                max_attempts=packing.max_attempts,
                progress=progress,
                cancel=cancel,
            )
    except PackingError as e:
        raise BuildError(str(e)) from None
    result = _check_distances(result, structures, box_nm, packing.tolerance_nm)

    velocities = None
    if recipe.velocities is not None:
        atom_masses = np.concatenate(
            [
                np.tile([a.mass_amu for a in m.params.atoms], n)
                for m, n in zip(molecules, counts, strict=True)
            ]
        )
        velocities = maxwell_boltzmann(
            atom_masses, recipe.velocities.temperature_K, seed_velocities
        )

    citations: list[dict] = []
    for m in molecules:
        for c in m.params.citations:
            if c.to_json() not in citations:
                citations.append(c.to_json())
    for density in (
        recipe.composition.density if recipe.composition else None,
        recipe.box.density if recipe.box else None,
    ):
        if density is not None:
            entry = {"what": "solution density for the box size", "citation": density.source}
            if entry not in citations:
                citations.append(entry)
    if recipe.composition is not None and recipe.composition.solvent_densities_source:
        citations.append(
            {
                "what": "pure-solvent densities (volume fractions)",
                "citation": recipe.composition.solvent_densities_source,
            }
        )

    provenance: dict[str, Any] = {
        "created_by": created_by(),
        "recipe": recipe.text,
        "seed": recipe.seed,
        "sources": citations,
        "forcefield_sources": sources,
        "transforms": transforms,
        "box": box_record,
        "packing": {
            "method": result.method,
            "program": result.program,
            "seed": result.seed,
            "tolerance_nm": packing.tolerance_nm,
            **(
                {"min_intermolecular_distance_nm": result.min_distance_nm}
                if np.isfinite(result.min_distance_nm)
                else {
                    "min_intermolecular_distance_at_least_nm": SEARCH_FACTOR * packing.tolerance_nm
                }
            ),
        },
    }
    if composition is not None:
        provenance["composition"] = composition.to_json()
    if recipe.velocities is not None:
        provenance["velocities"] = {
            "distribution": "maxwell-boltzmann",
            "temperature_K": recipe.velocities.temperature_K,
            "seed": seed_velocities,
            "com_removed": True,
            "rescaled_exactly": True,
        }
    if notes:
        provenance["notes"] = "; ".join(notes)
    return BuiltSystem(
        recipe.name,
        recipe.description,
        tuple(molecules),
        counts,
        box_nm,
        result.positions_nm,
        velocities,
        conventions,
        provenance,
    )


PACKMOL_SLACK = 0.9
"""Packmol converges to its tolerance up to a small residual; we accept 90 % of it."""


SEARCH_FACTOR = 1.5
"""The distance check searches up to 1.5 × the tolerance, so it reports real minima."""


def _check_distances(
    result: PackResult, structures: list[Structure], box_nm: float, tolerance_nm: float
) -> PackResult:
    # Molecule k is the k-th copy in canonical order.
    sizes = [len(s.elements) for s in structures for _ in range(s.count)]
    molecule_index = np.repeat(np.arange(len(sizes)), sizes)
    d, i, j = min_intermolecular_distance(
        result.positions_nm, molecule_index, box_nm, SEARCH_FACTOR * tolerance_nm
    )
    required = tolerance_nm * (PACKMOL_SLACK if result.method == "packmol" else 1.0)
    if d < required:
        raise BuildError(
            f"atoms {i} and {j} of different molecules are {d:.4f} nm apart, below "
            f"{required:.4f} nm; lower the density or enlarge the box"
        )
    return PackResult(
        result.positions_nm,
        result.method,
        result.program,
        result.seed,
        float(d) if np.isfinite(d) else float("inf"),
        result.notes,
    )


def molecule_index(built: BuiltSystem) -> np.ndarray:
    sizes = [
        m.graph.n_atoms
        for m, n in zip(built.molecules, built.counts, strict=True)
        for _ in range(n)
    ]
    return np.repeat(np.arange(len(sizes)), sizes)


def masses_by_component(built: BuiltSystem) -> Mapping[str, float]:
    return {m.name: m.mass_amu for m in built.molecules}

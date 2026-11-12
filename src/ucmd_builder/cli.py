"""`ucmd-build`: the builder's command-line interface (design/B1.md §11)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .build import BuildError, build, created_by
from .composition import compose
from .forcefield import registry
from .forcefield.api import SourceError
from .forcefield.library import list_sets, load_set, load_template
from .molecule import MoleculeError
from .packing import PackingError
from .recipe import RecipeError, load_recipe


def _cmd_build(args: argparse.Namespace) -> int:
    recipe = load_recipe(args.recipe)
    built = build(recipe, progress=None if args.quiet else lambda m: print(m, file=sys.stderr))
    from .package import write_package

    out = write_package(built, args.output, overwrite=args.force)
    packing = built.provenance["packing"]
    dmin = packing.get("min_intermolecular_distance_nm")
    print(
        f"wrote {out}: {built.atom_count} atoms, "
        f"{', '.join(f'{n} {m.name}' for m, n in zip(built.molecules, built.counts, strict=True))}"
        ", "
        f"box {built.box_nm:.4f} nm, {packing['program']}"
        + (f", closest intermolecular pair {dmin:.3f} nm" if dmin else "")
    )
    if "notes" in built.provenance:
        print(f"note: {built.provenance['notes']}", file=sys.stderr)
    return 0


def _cmd_compose(args: argparse.Namespace) -> int:
    recipe = load_recipe(args.recipe)
    if recipe.composition is None:
        raise RecipeError("the recipe has no [composition]")
    from .build import BuiltMolecule, _molecule, _parameterize, _transform  # internal helpers

    masses = {}
    for k, c in enumerate(recipe.components):
        graph = _molecule(c, recipe, k)
        params, _ = _parameterize(c, recipe, graph)
        params, _ = _transform(c, params)
        masses[c.name] = BuiltMolecule(c.name, "", graph, params).mass_amu
    result = compose(recipe.composition, masses)
    print(json.dumps(result.to_json(), indent=2))
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    for name, info in registry.available().items():
        source = registry.get(name)
        print(f"{name:<14} version {source.version:<4} {info.target}  ({info.distribution})")
    return 0


def _cmd_library(args: argparse.Namespace) -> int:
    for set_name in [args.set] if args.set else list_sets():
        ds = load_set(set_name)
        c = ds.conventions
        print(
            f"{ds.name} (v{ds.version}, {ds.family}: {c.combination_rule}, "
            f"1-4 {c.scale_14_lj}/{c.scale_14_coulomb})"
        )
        for name in ds.template_names():
            t = load_template(set_name, name)
            print(
                f"  {name:<8} {t.graph.formula():<10} z = {t.graph.formal_charge:+d}  "
                f"{t.description}"
            )
    return 0


def _cmd_xyz(args: argparse.Namespace) -> int:
    from .xyz import write_xyz

    n = write_xyz(args.package, args.output)
    print(f"wrote {args.output}: {n} atoms (extended XYZ, Å)")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from ucmd_spec.system import validate_system_package

    from .package import _spec_dir

    with _spec_dir():
        report = validate_system_package(args.package)
    for issue in report.issues:
        print(f"{issue.code}  {issue.message}  ({issue.location})")
    print("OK" if report.ok else "INVALID", args.package)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ucmd-build", description="UnitcellMD system builder")
    ap.add_argument("--version", action="version", version=created_by())
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="recipe → validated system package")
    p.add_argument("recipe", type=Path)
    p.add_argument(
        "-o", "--output", type=Path, required=True, help="package directory, e.g. box.ucsys"
    )
    p.add_argument("--force", action="store_true", help="replace an existing package")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=_cmd_build)
    p = sub.add_parser(
        "compose", help="composition calculator: counts, box, achieved concentrations"
    )
    p.add_argument("recipe", type=Path)
    p.set_defaults(func=_cmd_compose)
    p = sub.add_parser("sources", help="installed force-field sources")
    p.set_defaults(func=_cmd_sources)
    p = sub.add_parser("library", help="data sets and templates of the library source")
    p.add_argument("--set")
    p.set_defaults(func=_cmd_library)
    p = sub.add_parser("xyz", help="write a package as extended XYZ (Å) for viewers")
    p.add_argument("package", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.set_defaults(func=_cmd_xyz)
    p = sub.add_parser("check", help="validate a system package (ucmd-spec)")
    p.add_argument("package", type=Path)
    p.set_defaults(func=_cmd_check)
    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except (RecipeError, SourceError, BuildError, PackingError, MoleculeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["__version__", "main"]

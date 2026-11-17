"""The example recipes parse, and examples/EC/recipes match boxes.csv."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from ucmd_builder.forcefield.library import load_template
from ucmd_builder.recipe import load_recipe

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EC = EXAMPLES / "EC"


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.toml")), ids=lambda p: p.name)
def test_every_example_recipe_parses(path: Path) -> None:
    load_recipe(path)


def test_ec_recipes_are_generated_from_the_table(tmp_path: Path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("make_recipes", EC / "make_recipes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (EC / "boxes.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(list((EC / "recipes").glob("*.toml"))) == 35
    for row in rows:
        name, text = module.recipe(row)
        assert (EC / "recipes" / f"{name}.toml").read_text() == text, (
            f"rerun make_recipes.py: {name}"
        )


def test_every_ec_box_has_the_table_atom_count() -> None:
    """All 35 rows: every molecule is a library template, and the atoms add up to n_atoms."""
    spec = importlib.util.spec_from_file_location("make_recipes", EC / "make_recipes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    size = {m: load_template("opls-aa-carbonates", m).graph.n_atoms for m in module.ORDER}
    with (EC / "boxes.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 35
    for row in rows:
        name = module.slug(row["electrolyte"], row["c_M"])
        recipe = load_recipe(EC / "recipes" / f"{name}.toml")
        atoms = sum(size[c.molecule.value] * c.count for c in recipe.components)
        assert atoms == int(row["n_atoms"]), name
        assert recipe.box.length_nm == float(row["L_box_nm_approx"])

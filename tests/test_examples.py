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


def test_ec_emc_atom_counts_match_the_table() -> None:
    size = {
        m: load_template("opls-aa-carbonates", m).graph.n_atoms for m in ("Li", "PF6", "EC", "EMC")
    }
    with (EC / "boxes.csv").open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["electrolyte"].startswith("EC:EMC 3:7")]
    assert len(rows) == 7
    for row in rows:
        name = f"ec-emc_3-7w_{row['c_M']}M"
        recipe = load_recipe(EC / "recipes" / f"{name}.toml")
        atoms = sum(size[c.molecule.value] * c.count for c in recipe.components)
        assert atoms == int(row["n_atoms"]), name
        assert recipe.box.length_nm == float(row["L_box_nm_approx"])

"""The log and properties docks (design/B2a.md §3)."""

from __future__ import annotations

import html
import time

from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QWidget

from ..forcefield.library import load_template
from ..recipe import Recipe
from .components import governed, library_set
from .scene import AtomInfo


class LogPanel(QPlainTextEdit):
    """Build progress, refused edits, validator and engine messages, with the time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(10_000)

    def add(self, text: str) -> None:
        self.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")


def _table(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(v)}</td></tr>" for k, v in rows
    )
    return f"<table cellspacing='4'>{cells}</table>"


class PropertiesPanel(QTextBrowser):
    """Details of the selected component or the picked atom."""

    def show_component(self, recipe: Recipe, name: str) -> None:
        c = recipe.component(name)
        rows = [("component", c.name), ("molecule", f"{c.molecule.kind}: {c.molecule.value}")]
        data_set = library_set(recipe)
        if c.molecule.kind == "library" and data_set is not None:
            t = load_template(data_set, c.molecule.value)
            rows += [
                ("formula", t.graph.formula()),
                ("formal charge", f"{t.graph.formal_charge:+d} e"),
                ("description", t.description),
            ]
        rows += [
            ("count", "from [composition]" if name in governed(recipe) else str(c.count)),
            ("charge scale ζ", "1" if c.charge_scale is None else f"{c.charge_scale:g}"),
        ]
        self.setHtml(_table(rows))

    def show_atom(self, atom: AtomInfo) -> None:
        def number(value: float | None, unit: str, digits: int = 4) -> str:
            return "–" if value is None else f"{value:.{digits}g} {unit}"

        self.setHtml(
            _table(
                [
                    ("atom", f"{atom.index} ({atom.name})"),
                    ("element", atom.element),
                    ("molecule", f"{atom.molecule_type} #{atom.molecule}"),
                    ("type", atom.type or "–"),
                    ("charge", number(atom.charge_e, "e")),
                    ("σ", number(atom.sigma_nm, "nm")),
                    ("ε", number(atom.epsilon_kjmol, "kJ/mol")),
                ]
            )
        )

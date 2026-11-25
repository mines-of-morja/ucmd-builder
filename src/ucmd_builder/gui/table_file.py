"""Result tables that an analysis writes for the GUI (design/B2c.md §3.5): a tab-separated file.

    # ucmd-table 1
    # title: Sensitivity at 1 M
    # note: any number of lines, shown above the table
    # plot: x=x y=kappa_NE_S_per_m group=knob
    knob<TAB>x<TAB>kappa_NE_S_per_m
    zeta<TAB>0.867<TAB>0.31

`plot` is optional and names columns. An empty cell or `nan` has no value: it is shown as a dash
and never plotted. The GUI draws what is in the file; it fits and averages nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

MAGIC = "# ucmd-table 1"


class TableError(ValueError):
    """The file is not a ucmd-table."""


@dataclass(frozen=True)
class Table:
    path: Path
    title: str
    notes: tuple[str, ...]
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    plot: dict[str, str]

    def index(self, column: str) -> int:
        return self.columns.index(column)

    def number(self, row: int, column: int) -> float | None:
        """The value of a cell, or None if it has none or is text."""
        return _number(self.rows[row][column])

    def is_numeric(self, column: int) -> bool:
        """Whether every cell that is filled holds a number, and at least one does."""
        filled = [r[column] for r in self.rows if not _empty(r[column])]
        return bool(filled) and all(_number(c) is not None for c in filled)

    def numeric_columns(self) -> list[str]:
        return [c for k, c in enumerate(self.columns) if self.is_numeric(k)]


def _empty(cell: str) -> bool:
    return not cell or cell.lower() == "nan"


def _number(cell: str) -> float | None:
    try:
        value = float(cell)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def is_table(path: Path | str) -> bool:
    try:
        with Path(path).open(encoding="utf-8") as fh:
            return fh.readline().rstrip("\n") == MAGIC
    except (OSError, UnicodeDecodeError):
        return False


def read_table(path: Path | str) -> Table:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as e:
        raise TableError(f"{path.name} is not a text file") from e
    if not lines or lines[0] != MAGIC:
        raise TableError(f"{path.name} does not start with {MAGIC!r}")
    title, notes, plot = path.stem, [], {}
    at = 1
    while at < len(lines) and lines[at].startswith("#"):
        key, _, value = lines[at].lstrip("# ").partition(":")
        value = value.strip()
        if key == "title":
            title = value
        elif key == "note":
            notes.append(value)
        elif key == "plot":
            for word in value.split():
                name, _, column = word.partition("=")
                plot[name] = column
        at += 1
    body = [(n, line.split("\t")) for n, line in enumerate(lines[at:], at + 1) if line.strip()]
    if not body:
        raise TableError(f"{path.name} has no header line")
    columns = tuple(c.strip() for c in body[0][1])
    if len(set(columns)) != len(columns):
        raise TableError(f"{path.name}: two columns have the same name")
    rows = []
    for n, cells in body[1:]:
        if len(cells) != len(columns):
            raise TableError(f"{path.name}, line {n}: {len(cells)} cells, {len(columns)} columns")
        rows.append(tuple(c.strip() for c in cells))
    table = Table(path, title, tuple(notes), columns, tuple(rows), plot)
    for role, column in plot.items():
        if role not in ("x", "y", "group"):
            raise TableError(f"{path.name}: plot has no {role!r}")
        if column not in columns:
            raise TableError(f"{path.name}: plot names {column!r}, which is no column")
        if role != "group" and not table.is_numeric(table.index(column)):
            raise TableError(f"{path.name}: column {column!r} is not numeric")
    if plot and not {"x", "y"} <= plot.keys():
        raise TableError(f"{path.name}: plot needs x and y")
    return table

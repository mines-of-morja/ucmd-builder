"""The recipe being edited, the single source of truth of the GUI (design/B2a.md §3).

`Recipe` and its parts are frozen dataclasses. An edit is a new recipe: it goes through the
recipe parser (`to_toml` → `parse_recipe`), and if the parser refuses it, the edit is not
applied and the caller gets the `RecipeError`. Accepted edits are `QUndoCommand`s, so undo and
redo just swap recipes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..recipe import Box, Packing, Recipe, RecipeError, load_recipe, parse_recipe, to_toml

DEFAULT_BOX_NM = 3.0


def new_recipe() -> Recipe:
    """An empty recipe that the parser accepts: no components yet, a 3 nm box, Packmol."""
    return Recipe(
        name="untitled",
        seed=1,
        components=(),
        box=Box(length_nm=DEFAULT_BOX_NM),
        packing=Packing(),
    )


def checked(recipe: Recipe) -> Recipe:
    """The recipe as the parser reads it back; raises RecipeError if the parser refuses it."""
    return parse_recipe(to_toml(recipe), path=recipe.path)


class ReplaceRecipe(QUndoCommand):
    """One edit: the recipe before and after."""

    def __init__(self, document: RecipeDocument, old: Recipe, new: Recipe, text: str) -> None:
        super().__init__(text)
        self._document, self._old, self._new = document, old, new

    def redo(self) -> None:
        self._document._set(self._new)

    def undo(self) -> None:
        self._document._set(self._old)


class RecipeDocument(QObject):
    """Holds the recipe, its file and the undo stack; emits `changed` after every change."""

    changed = Signal(object)  # the new Recipe
    path_changed = Signal(object)  # Path | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._recipe = new_recipe()
        self._path: Path | None = None
        self.undo_stack = QUndoStack(self)

    @property
    def recipe(self) -> Recipe:
        return self._recipe

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def modified(self) -> bool:
        return not self.undo_stack.isClean()

    def edit(self, recipe: Recipe, text: str) -> None:
        """Apply an edit described by `text` (shown in the undo menu).

        Raises RecipeError, and changes nothing, if the recipe parser refuses the result.
        """
        recipe = checked(recipe)
        if to_toml(recipe) == to_toml(self._recipe):
            return
        self.undo_stack.push(ReplaceRecipe(self, self._recipe, recipe, text))

    def _set(self, recipe: Recipe) -> None:
        self._recipe = recipe
        self.changed.emit(recipe)

    def new(self) -> None:
        self._replace_all(new_recipe(), None)

    def open(self, path: Path | str) -> None:
        """Load a recipe file; raises RecipeError if it is invalid."""
        path = Path(path)
        self._replace_all(load_recipe(path), path)

    def save(self, path: Path | str | None = None) -> Path:
        """Write the recipe as TOML to `path`, or to its current file."""
        target = Path(path) if path is not None else self._path
        if target is None:
            raise RecipeError("the recipe has no file yet; choose one")
        target.write_text(to_toml(self._recipe), encoding="utf-8")
        self.undo_stack.setClean()
        if target != self._path:
            self._path = target
            self.path_changed.emit(target)
        return target

    def _replace_all(self, recipe: Recipe, path: Path | None) -> None:
        self.undo_stack.clear()
        self._recipe = recipe
        self._path = path
        self.changed.emit(recipe)
        self.path_changed.emit(path)

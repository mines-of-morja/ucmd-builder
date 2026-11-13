"""The composition dialog (design/B2a.md §5): `[composition]` with a live `compose()` preview.

The dialog edits the fields of `[composition]` on the mass basis (volume fractions, which need
pure-solvent densities, come with B2b). Solvent weights are normalized, so "3 : 7" works.
The result governs the counts of salt and solvents and the box, so it removes their `count`
and the `[box]` in the same edit.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..build import BuildError, component_masses
from ..composition import compose
from ..recipe import Composition, Density, Recipe, RecipeError


class CompositionDialog(QDialog):
    """Salt, solvents with weights, molarity, size and solution density."""

    def __init__(self, recipe: Recipe, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Composition")
        self._recipe = recipe
        names = [c.name for c in recipe.components]
        try:
            self._masses: dict[str, float] | None = component_masses(recipe)
        except (BuildError, RecipeError) as e:
            self._masses = None
            self._mass_error = str(e)
        old = recipe.composition
        self.cation = QComboBox()
        self.anion = QComboBox()
        self.cation.addItems(names)
        self.anion.addItems(names)
        self.solvents = QTableWidget(len(names), 2)
        self.solvents.setHorizontalHeaderLabels(["Component", "Weight (mass)"])
        self.solvents.verticalHeader().setVisible(False)
        for row, name in enumerate(names):
            self.solvents.setItem(row, 0, QTableWidgetItem(name))
            weight = old.solvent.get(name, 0.0) if old is not None else 0.0
            self.solvents.setItem(row, 1, QTableWidgetItem(f"{weight:g}"))
        self.molarity = QDoubleSpinBox()
        self.molarity.setRange(0.001, 20.0)
        self.molarity.setDecimals(3)
        self.molarity.setSuffix(" mol/L")
        self.molarity.setValue(1.0)
        self.solvent_molecules = QSpinBox()
        self.solvent_molecules.setRange(1, 1_000_000)
        self.solvent_molecules.setValue(500)
        self.density = QDoubleSpinBox()
        self.density.setRange(0.1, 10.0)
        self.density.setDecimals(4)
        self.density.setSuffix(" g/cm³")
        self.density.setValue(1.0)
        self.density_source = QLineEdit()
        self.density_source.setPlaceholderText("citation, or 'placeholder …'")
        if old is not None:
            self.cation.setCurrentText(old.salt[0])
            self.anion.setCurrentText(old.salt[1])
            if old.molarity_mol_L is not None:
                self.molarity.setValue(old.molarity_mol_L)
            if old.solvent_molecules is not None:
                self.solvent_molecules.setValue(old.solvent_molecules)
            if old.density is not None:
                self.density.setValue(old.density.value_g_cm3)
                self.density_source.setText(old.density.source)
        elif len(names) >= 2:
            self.anion.setCurrentIndex(1)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Cation", self.cation)
        form.addRow("Anion", self.anion)
        form.addRow("Solvents", self.solvents)
        form.addRow("Salt concentration", self.molarity)
        form.addRow("Solvent molecules", self.solvent_molecules)
        form.addRow("Solution density", self.density)
        form.addRow("Density source", self.density_source)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview)
        layout.addWidget(self.buttons)
        for signal in (
            self.cation.currentTextChanged,
            self.anion.currentTextChanged,
            self.molarity.valueChanged,
            self.solvent_molecules.valueChanged,
            self.density.valueChanged,
            self.density_source.textChanged,
        ):
            signal.connect(self.update_preview)
        self.solvents.itemChanged.connect(self.update_preview)
        self.update_preview()

    def set_weight(self, name: str, weight: float) -> None:
        """Set a solvent weight (also for tests)."""
        for row in range(self.solvents.rowCount()):
            if self.solvents.item(row, 0).text() == name:
                self.solvents.item(row, 1).setText(f"{weight:g}")
                return
        raise KeyError(name)

    def composition(self) -> Composition:
        """The composition the fields describe; raises RecipeError if they are incomplete."""
        salt = (self.cation.currentText(), self.anion.currentText())
        weights = {}
        for row in range(self.solvents.rowCount()):
            name = self.solvents.item(row, 0).text()
            try:
                w = float(self.solvents.item(row, 1).text() or 0)
            except ValueError:
                raise RecipeError(f"weight of {name} is not a number") from None
            if w > 0 and name not in salt:
                weights[name] = w
        if salt[0] == salt[1]:
            raise RecipeError("cation and anion must be different components")
        if not weights:
            raise RecipeError("give at least one solvent a weight > 0")
        total = sum(weights.values())
        source = self.density_source.text().strip()
        if not source:
            raise RecipeError("the solution density needs a source (or 'placeholder …')")
        return Composition(
            salt=salt,
            solvent={k: w / total for k, w in weights.items()},
            basis="mass",
            molarity_mol_L=self.molarity.value(),
            solvent_molecules=self.solvent_molecules.value(),
            density=Density(self.density.value(), source),
        )

    def result_recipe(self) -> Recipe:
        """The recipe with this composition; counts of governed components and [box] removed."""
        comp = self.composition()
        governed = set(comp.salt) | set(comp.solvent)
        components = tuple(
            replace(c, count=None) if c.name in governed else c for c in self._recipe.components
        )
        return replace(self._recipe, composition=comp, components=components, box=None)

    def update_preview(self, *_args) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        try:
            comp = self.composition()
            if self._masses is None:
                raise RecipeError(self._mass_error)
            result = compose(comp, self._masses)
        except (RecipeError, KeyError, ValueError) as e:
            self.preview.setText(f"<span style='color:#b00000'>{e}</span>")
            ok.setEnabled(False)
            return
        counts = ", ".join(f"{n} {name}" for name, n in result.counts.items())
        box = f"box {result.box_length_nm:.4f} nm" if result.box_length_nm else "box from [box]"
        molarity = (
            f", {result.molarity_mol_L:.3f} M achieved" if result.molarity_mol_L is not None else ""
        )
        warning = (
            "<br><span style='color:#b06000'>The density is a placeholder: not for production."
            "</span>"
            if comp.density is not None and comp.density.is_placeholder
            else ""
        )
        self.preview.setText(f"{counts}; {box}{molarity}{warning}")
        ok.setEnabled(True)

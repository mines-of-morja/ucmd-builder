"""The components dock and the dialog that adds a library molecule (design/B2a.md §3, §5)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..forcefield.library import list_sets, load_set, load_template
from ..recipe import Component, MoleculeSpec, Recipe, RecipeError
from .document import RecipeDocument

COLUMNS = ("Name", "Molecule", "Count", "ζ")
GOVERNED = "from composition"


def library_set(recipe: Recipe) -> str | None:
    """The library data set of the recipe's force field, if it uses the library source."""
    ff = recipe.forcefield or {}
    return ff.get("set") if ff.get("source") == "library" else None


def molecule_text(component: Component) -> str:
    spec = component.molecule
    return f"{spec.kind}: {spec.value}"


def governed(recipe: Recipe) -> set[str]:
    comp = recipe.composition
    return set(comp.salt) | set(comp.solvent) if comp is not None else set()


class AddComponentDialog(QDialog):
    """Pick a molecule of a library data set; name, count and charge scale."""

    def __init__(self, recipe: Recipe, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add component")
        self._recipe = recipe
        self.set_box = QComboBox()
        self.set_box.addItems(list_sets())
        current = library_set(recipe)
        if current is not None:
            self.set_box.setCurrentText(current)
            self.set_box.setEnabled(False)  # one data set per recipe in the preview
        self.molecules = QListWidget()
        self.name = QLineEdit()
        self.count = QSpinBox()
        self.count.setRange(1, 1_000_000)
        self.count.setValue(1)
        self.zeta = QDoubleSpinBox()
        self.zeta.setRange(0.01, 2.0)
        self.zeta.setDecimals(3)
        self.zeta.setSingleStep(0.05)
        self.zeta.setValue(1.0)
        form = QFormLayout()
        form.addRow("Force-field set", self.set_box)
        form.addRow("Molecule", self.molecules)
        form.addRow("Component name", self.name)
        form.addRow("Count", self.count)
        form.addRow("Charge scale ζ", self.zeta)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.set_box.currentTextChanged.connect(self._fill)
        self.molecules.currentItemChanged.connect(self._picked)
        self._fill(self.set_box.currentText())

    def _fill(self, set_name: str) -> None:
        self.molecules.clear()
        ds = load_set(set_name)
        for name in ds.template_names():
            t = load_template(set_name, name)
            item = QListWidgetItem(
                f"{name}   {t.graph.formula()}   z = {t.graph.formal_charge:+d}   {t.description}"
            )
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.molecules.addItem(item)
        if self.molecules.count():
            self.molecules.setCurrentRow(0)

    def _picked(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        template = item.data(Qt.ItemDataRole.UserRole)
        taken = {c.name for c in self._recipe.components}
        name, k = template, 2
        while name in taken:
            name, k = f"{template}{k}", k + 1
        self.name.setText(name)

    def choose(self, template: str) -> None:
        """Select a template by name (also for tests)."""
        for row in range(self.molecules.count()):
            if self.molecules.item(row).data(Qt.ItemDataRole.UserRole) == template:
                self.molecules.setCurrentRow(row)
                return
        raise KeyError(template)

    def result_recipe(self) -> Recipe:
        """The recipe with the new component (and the force field, if it had none)."""
        item = self.molecules.currentItem()
        if item is None:
            raise RecipeError("choose a molecule")
        component = Component(
            name=self.name.text().strip(),
            molecule=MoleculeSpec("library", item.data(Qt.ItemDataRole.UserRole)),
            count=self.count.value(),
            charge_scale=None if self.zeta.value() == 1.0 else self.zeta.value(),
        )
        forcefield = self._recipe.forcefield or {
            "source": "library",
            "set": self.set_box.currentText(),
        }
        return replace(
            self._recipe,
            forcefield=forcefield,
            components=(*self._recipe.components, component),
        )


class ComponentsPanel(QWidget):
    """Components with count and charge scale; edits go through the document."""

    selected = Signal(str)  # component name
    message = Signal(str)  # for the log
    composition_requested = Signal()

    def __init__(self, document: RecipeDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.forcefield = QLabel()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.add_button = QPushButton("Add…")
        self.remove_button = QPushButton("Remove")
        self.composition_button = QPushButton("Composition…")
        buttons = QHBoxLayout()
        for b in (self.add_button, self.remove_button, self.composition_button):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        layout.addWidget(self.forcefield)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add)
        self.remove_button.clicked.connect(self.remove_selected)
        self.composition_button.clicked.connect(self.composition_requested)
        self.table.itemChanged.connect(self._edited)
        self.table.itemSelectionChanged.connect(self._selection)
        document.changed.connect(self.refresh)
        self.refresh(document.recipe)

    def refresh(self, recipe: Recipe) -> None:
        ff = recipe.forcefield or {}
        self.forcefield.setText(
            f"Force field: {ff.get('source', '–')} {ff.get('set', '')}".rstrip()
        )
        self.table.blockSignals(True)
        self.table.setRowCount(len(recipe.components))
        from_composition = governed(recipe)
        for row, c in enumerate(recipe.components):
            count = GOVERNED if c.name in from_composition else str(c.count)
            zeta = "1" if c.charge_scale is None else f"{c.charge_scale:g}"
            for col, text in enumerate((c.name, molecule_text(c), count, zeta)):
                item = QTableWidgetItem(text)
                editable = col == 3 or (col == 2 and count != GOVERNED)
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)

    def add(self) -> None:
        dialog = AddComponentDialog(self.document.recipe, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply(lambda: dialog.result_recipe(), "add component")

    def remove_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return
        recipe = self.document.recipe
        name = recipe.components[rows.pop()].name
        kept = tuple(c for c in recipe.components if c.name != name)
        self.apply(lambda: replace(recipe, components=kept), f"remove {name}")

    def _edited(self, item: QTableWidgetItem) -> None:
        recipe = self.document.recipe
        c = recipe.components[item.row()]

        def changed() -> Recipe:
            text = item.text().strip()
            try:
                value = float(text) if item.column() == 3 else int(text)
            except ValueError:
                raise RecipeError(
                    f"{COLUMNS[item.column()]} of {c.name}: not a number: {text!r}"
                ) from None
            if item.column() == 2:
                new = replace(c, count=value)
            else:
                new = replace(c, charge_scale=None if value == 1.0 else value)
            parts = tuple(new if x.name == c.name else x for x in recipe.components)
            return replace(recipe, components=parts)

        self.apply(changed, f"edit {c.name}")

    def apply(self, make, text: str) -> None:
        """Build the new recipe and hand it to the document; refusals go to the log."""
        try:
            self.document.edit(make(), text)
        except RecipeError as e:
            self.message.emit(f"refused: {e}")
            self.refresh(self.document.recipe)

    def _selection(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if rows:
            self.selected.emit(self.document.recipe.components[rows.pop()].name)

"""The project panel (design/B2d.md §2.1): the workspace as a tree; a double click opens."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .project import Project, Study, scan_project
from .runs import RunInfo

PATH = Qt.ItemDataRole.UserRole  # the path a double click opens
KEY = Qt.ItemDataRole.UserRole + 1  # names the item across refreshes


def run_state(run: RunInfo) -> str:
    if run.state == "complete" or run.last_step is None or run.steps <= 0:
        return run.state
    return f"{run.state} {100 * run.last_step / run.steps:.0f} %"


class ProjectPanel(QWidget):
    """Studies with runs and tables, and the files of the workspace folder."""

    open_requested = Signal(object)  # Path
    root_chosen = Signal(object)  # Path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root: Path | None = None
        self.project: Project | None = None
        self.place = QLabel("No folder chosen")
        self.place.setMinimumWidth(1)
        self.choose_button = QPushButton("Folder…")
        self.refresh_button = QPushButton("Refresh")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter, e.g. sensitivity zeta")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(20)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        buttons = QHBoxLayout()
        buttons.addWidget(self.choose_button)
        buttons.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.place)
        layout.addWidget(self.filter)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self.choose_button.clicked.connect(self._choose)
        self.refresh_button.clicked.connect(self.refresh)
        self.filter.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._open)

    def set_root(self, root: Path | str | None) -> None:
        self.root = None if root is None else Path(root)
        self.refresh()

    def refresh(self) -> None:
        """Read the folder again; what was unfolded stays unfolded."""
        self.show_project(scan_project(self.root) if self.root and self.root.is_dir() else None)

    def show_project(self, project: Project | None) -> None:
        was_open = {i.data(0, KEY) for i in self._items() if i.isExpanded()}
        first = self.project is None
        self.project = project
        self.tree.clear()
        if project is None:
            text = "No folder chosen" if self.root is None else f"{self.root} does not exist"
            self.place.setText(text)
            return
        self.place.setText(str(project.root))
        self.place.setToolTip(str(project.root))
        if project.studies:
            top = self._item(self.tree, "Studies", f"{len(project.studies)}", "studies")
            for study in project.studies:
                self._study(top, study)
            if first:
                was_open.add("studies")
        for kind, paths in project.files.items():
            top = self._item(self.tree, kind, f"{len(paths)}", f"files/{kind}")
            for path in paths:
                self._item(top, path.name, "", f"file/{path}", path)
        for item in self._items():
            item.setExpanded(item.data(0, KEY) in was_open)
        self._filter(self.filter.text())

    def _study(self, parent: QTreeWidgetItem, study: Study) -> None:
        top = self._item(
            parent, study.name, f"{study.done}/{len(study.runs)}", f"study/{study.name}"
        )
        top.setToolTip(0, study.question or study.name)
        for table in study.tables:
            self._item(top, table.name, "table", f"table/{table}", table)
        base = study.path / "runs"
        for run in study.runs:
            name = run.run_dir.relative_to(base).as_posix()
            item = self._item(top, name, run_state(run), f"run/{run.run_dir}", run.run_dir)
            item.setToolTip(0, run.title)

    @staticmethod
    def _item(parent, text: str, state: str, key: str, path: Path | None = None) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text, state])
        item.setData(0, KEY, key)
        if path is not None:
            item.setData(0, PATH, str(path))
            item.setToolTip(0, str(path))
        return item

    def _items(self) -> list[QTreeWidgetItem]:
        found, todo = [], [self.tree.topLevelItem(k) for k in range(self.tree.topLevelItemCount())]
        while todo:
            item = todo.pop()
            found.append(item)
            todo += [item.child(k) for k in range(item.childCount())]
        return found

    def item(self, key: str) -> QTreeWidgetItem | None:
        return next((i for i in self._items() if i.data(0, KEY) == key), None)

    def _open(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(0, PATH)
        if path:
            self.open_requested.emit(Path(path))

    def _choose(self) -> None:
        start = str(self.root) if self.root is not None else ""
        path = QFileDialog.getExistingDirectory(self, "The workspace folder", start)
        if path:
            self.root_chosen.emit(Path(path))

    def _filter(self, text: str) -> None:
        """Show the items whose name, with those above it, holds every word."""
        words = text.lower().split()

        def visit(item: QTreeWidgetItem, above: str) -> bool:
            line = f"{above} {item.text(0)} {item.text(1)}".lower()
            below = [visit(item.child(k), line) for k in range(item.childCount())]
            shown = any(below) or all(w in line for w in words)
            item.setHidden(not shown)
            if words and any(below):
                item.setExpanded(True)
            return shown

        for k in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(k), "")

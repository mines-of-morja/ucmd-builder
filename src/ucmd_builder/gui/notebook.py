"""The notebook in the middle of the window: pages as tabs (design/B2c.md §3.5).

The first page is the 3D view and stays. Every result table gets a page of its own: the table,
its notes and, if it names a plot, the chosen columns as points. A page can be closed; opening
the same file again replaces its page.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCharts import QChart, QChartView, QScatterSeries, QValueAxis
from PySide6.QtCore import QMargins, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .table_file import Table, TableError, read_table

NO_GROUP = "(no groups)"
MARKERS = (
    QScatterSeries.MarkerShape.MarkerShapeCircle,
    QScatterSeries.MarkerShape.MarkerShapeRectangle,
    QScatterSeries.MarkerShape.MarkerShapeTriangle,
    QScatterSeries.MarkerShape.MarkerShapeRotatedRectangle,
    QScatterSeries.MarkerShape.MarkerShapeStar,
)


class _Cell(QTableWidgetItem):
    """Sorts by value, not by text; a cell without a value comes last."""

    def __init__(self, text: str, value: float | None) -> None:
        super().__init__(text)
        self.value = value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        # never super().__lt__: in PySide it calls this method again, without end
        mine, theirs = self.value, getattr(other, "value", None)
        if mine is None and theirs is None:
            return self.text() < other.text()
        if mine is None or theirs is None:
            return theirs is None
        return mine < theirs


class TablePage(QWidget):
    """One result table and its plot."""

    def __init__(self, table: Table, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = table
        title = QLabel(table.title)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        self.notes = QLabel("\n".join(table.notes))
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setWordWrap(True)
        self.notes.setVisible(bool(table.notes))
        self.grid = QTableWidget(len(table.rows), len(table.columns))
        self.grid.setHorizontalHeaderLabels(table.columns)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.verticalHeader().setVisible(False)
        self.grid.verticalHeader().setDefaultSectionSize(24)
        for r, row in enumerate(table.rows):
            for c, text in enumerate(row):
                value = table.number(r, c)
                empty = value is None and text.lower() in ("", "nan")
                item = _Cell("–" if empty else text, value)
                if value is not None:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.grid.setItem(r, c, item)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # file order until clicked
        self.grid.setSortingEnabled(True)
        self.reload = QPushButton("Reload")
        top = QHBoxLayout()
        top.addWidget(title, 1)
        top.addWidget(self.reload)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.grid)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.notes)
        layout.addWidget(self.splitter, 1)
        self.view: QChartView | None = None
        self.series: list[QScatterSeries] = []
        if table.plot:
            self._add_plot()

    def _add_plot(self) -> None:
        self.x_box, self.y_box, self.group_box = QComboBox(), QComboBox(), QComboBox()
        numeric = self.table.numeric_columns()
        self.x_box.addItems(numeric)
        self.y_box.addItems(numeric)
        text = [c for c in self.table.columns if c not in numeric]
        self.group_box.addItems([NO_GROUP, *text])
        self.x_box.setCurrentText(self.table.plot["x"])
        self.y_box.setCurrentText(self.table.plot["y"])
        self.group_box.setCurrentText(self.table.plot.get("group", NO_GROUP))
        chart = QChart()
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.setMargins(QMargins(2, 2, 2, 2))
        self.x_axis, self.y_axis = QValueAxis(), QValueAxis()
        for axis in (self.x_axis, self.y_axis):
            axis.setTickCount(5)
        chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        self.view = QChartView(chart)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setMinimumHeight(120)
        controls = QHBoxLayout()
        for label, box in (("x", self.x_box), ("y", self.y_box), ("by", self.group_box)):
            controls.addWidget(QLabel(label))
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            box.setMinimumContentsLength(8)
            controls.addWidget(box, 1)
            box.currentIndexChanged.connect(lambda _i: self.draw())
        area = QWidget()
        inner = QVBoxLayout(area)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(controls)
        inner.addWidget(self.view, 1)
        self.splitter.addWidget(area)
        self.splitter.setSizes([350, 450])
        self.draw()

    def draw(self) -> None:
        """The points of the file, one series per group; rows without a value are left out."""
        assert self.view is not None
        chart = self.view.chart()
        for series in self.series:
            chart.removeSeries(series)
        self.series = []
        x, y = (self.table.index(b.currentText()) for b in (self.x_box, self.y_box))
        group = self.group_box.currentText()
        g = None if group == NO_GROUP else self.table.index(group)
        points: dict[str, list[tuple[float, float]]] = {}
        for r, row in enumerate(self.table.rows):
            px, py = self.table.number(r, x), self.table.number(r, y)
            if px is not None and py is not None:
                points.setdefault("" if g is None else row[g], []).append((px, py))
        for k, (name, members) in enumerate(points.items()):
            series = QScatterSeries()
            series.setName(name)
            series.setMarkerShape(MARKERS[k % len(MARKERS)])
            series.setMarkerSize(11.0)
            for px, py in members:
                series.append(px, py)
            chart.addSeries(series)
            series.attachAxis(self.x_axis)
            series.attachAxis(self.y_axis)
            self.series.append(series)
        chart.legend().setVisible(g is not None)
        every = [p for members in points.values() for p in members]
        for axis, k, box in ((self.x_axis, 0, self.x_box), (self.y_axis, 1, self.y_box)):
            axis.setTitleText(box.currentText())
            if every:
                low, high = min(p[k] for p in every), max(p[k] for p in every)
                margin = 0.05 * ((high - low) or abs(high) or 1.0)
                axis.setRange(low - margin, high + margin)
                axis.setLabelFormat("%.3g")


class Notebook(QTabWidget):
    """Pages as tabs: the 3D view, then one page per result table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(False)
        self.tabCloseRequested.connect(self.close_page)
        self._fixed: list[QWidget] = []

    def add_fixed(self, widget: QWidget, title: str) -> None:
        """A page that cannot be closed."""
        index = self.addTab(widget, title)
        self._fixed.append(widget)
        for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
            self.tabBar().setTabButton(index, side, None)

    def close_page(self, index: int) -> None:
        page = self.widget(index)
        if page is None or page in self._fixed:
            return
        self.removeTab(index)
        page.deleteLater()

    def show_fixed(self) -> None:
        if self._fixed:
            self.setCurrentWidget(self._fixed[0])

    def table_pages(self) -> list[TablePage]:
        pages = (self.widget(k) for k in range(self.count()))
        return [p for p in pages if isinstance(p, TablePage)]

    def add_table(self, path: Path | str) -> TablePage:
        """Read the file and show it; a page of the same file is replaced (raises TableError)."""
        table = read_table(Path(path))
        page = TablePage(table)
        page.reload.clicked.connect(lambda: self._reload(page))
        same = [p for p in self.table_pages() if p.table.path.resolve() == table.path.resolve()]
        if same:
            at = self.indexOf(same[0])
            self.removeTab(at)
            same[0].deleteLater()
        else:
            at = self.count()
        self.insertTab(at, page, table.title)
        self.setTabToolTip(at, str(table.path))
        self.setCurrentIndex(at)
        return page

    def _reload(self, page: TablePage) -> None:
        try:
            self.add_table(page.table.path)
        except (OSError, TableError) as e:
            page.notes.setText(f"cannot reload: {e}")
            page.notes.show()

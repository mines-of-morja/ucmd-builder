"""Live plots of temperature and energies from the thermo table (design/B2a.md §7).

QtCharts is part of PySide6, so no plotting library is added.
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QWidget

from .thermo import ThermoTail

ENERGIES = (
    ("potential_kjmol", "potential"),
    ("total_kjmol", "total"),
    ("conserved_energy_kjmol", "conserved"),
)


class _Chart(QChartView):
    def __init__(self, title: str, y_label: str) -> None:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        super().__init__(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.x_axis = QValueAxis()
        self.x_axis.setTitleText("time (ps)")
        self.y_axis = QValueAxis()
        self.y_axis.setTitleText(y_label)
        chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        self.series: dict[str, QLineSeries] = {}

    def line(self, key: str, name: str, dashed: bool = False) -> QLineSeries:
        series = QLineSeries()
        series.setName(name)
        if dashed:
            pen = QPen(series.pen())
            pen.setStyle(Qt.PenStyle.DashLine)
            series.setPen(pen)
        self.chart().addSeries(series)
        series.attachAxis(self.x_axis)
        series.attachAxis(self.y_axis)
        self.series[key] = series
        return series

    def set_data(self, key: str, xs, ys) -> None:
        self.series[key].replace([QPointF(float(x), float(y)) for x, y in zip(xs, ys, strict=True)])

    def rescale(self) -> None:
        points = [p for s in self.series.values() for p in s.points()]
        if not points:
            return
        xs = [p.x() for p in points]
        ys = [p.y() for p in points]
        span = (max(ys) - min(ys)) or abs(max(ys)) or 1.0
        self.x_axis.setRange(min(xs), max(max(xs), min(xs) + 1e-3))
        self.y_axis.setRange(min(ys) - 0.05 * span, max(ys) + 0.05 * span)


class LivePlots(QWidget):
    """Temperature with its target, and potential, total and conserved energy."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.temperature = _Chart("Temperature", "T (K)")
        self.temperature.line("temperature_K", "T")
        self.temperature.line("target", "target", dashed=True)
        self.energy = _Chart("Energy", "kJ/mol")
        for key, name in ENERGIES:
            self.energy.line(key, name)
        layout = QHBoxLayout(self)
        layout.addWidget(self.temperature)
        layout.addWidget(self.energy)
        self.target_K: float | None = None

    def reset(self, target_K: float | None) -> None:
        self.target_K = target_K
        for chart in (self.temperature, self.energy):
            for series in chart.series.values():
                series.clear()

    def update_from(self, tail: ThermoTail) -> None:
        t = tail.column("time_ps")
        if len(t) == 0:
            return
        self.temperature.set_data("temperature_K", t, tail.column("temperature_K"))
        if self.target_K is not None:
            self.temperature.set_data("target", [t[0], t[-1]], [self.target_K] * 2)
        for key, _name in ENERGIES:
            values = tail.column(key)
            if len(values) == len(t):
                self.energy.set_data(key, t, values)
        self.temperature.rescale()
        self.energy.rescale()

    def points(self, key: str) -> int:
        for chart in (self.temperature, self.energy):
            if key in chart.series:
                return chart.series[key].count()
        return 0

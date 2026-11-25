"""Plots of the thermo table (design/B2a.md §7): temperature on top, then tabs.

- **Energies:** potential, kinetic, total and conserved energy.
- **Conservation:** H − H₀, the change of the conserved energy since the first row. Its slope
  is the drift: a check of the time step and the integrator.
- **Pressure:** the pressure P = (2K + tr W)/(3V) in bar, split into its kinetic part 2K/(3V)
  (atoms carrying momentum, as in an ideal gas) and the part of the forces, tr W/(3V) (the
  rest), with a moving average over MOVING_AVERAGE_PS of the total to show a plateau.
- **Volume:** the cell volume (nm³); it changes under a barostat (NPT).

A summary line under the tabs gives means over the second half of the table and the drift of H
(`thermo.summary`). QtCharts is part of PySide6, so no plotting library is added.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from ..constants import BAR_PER_KJMOL_NM3
from .thermo import ThermoTail, summary

MIN_CHART_HEIGHT = 230
MOVING_AVERAGE_PS = 1.0
ENERGIES = (
    ("potential_kjmol", "potential"),
    ("kinetic_kjmol", "kinetic"),
    ("total_kjmol", "total"),
    ("conserved_energy_kjmol", "conserved"),
)


class _Chart(QChartView):
    def __init__(self, y_label: str, y_format: str) -> None:
        chart = QChart()
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.setMargins(QMargins(2, 2, 2, 2))
        chart.layout().setContentsMargins(0, 0, 0, 0)
        super().__init__(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(MIN_CHART_HEIGHT)
        self.x_axis = QValueAxis()
        self.x_axis.setTitleText("time (ps)")
        self.x_axis.setLabelFormat("%.2f")
        self.x_axis.setTickCount(5)
        self.y_axis = QValueAxis()
        self.y_axis.setTitleText(y_label)
        self.y_axis.setLabelFormat(y_format)
        self.y_axis.setTickCount(5)
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
    """Temperature with its target; energies, conservation and pressure as tabs; a summary."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.temperature = _Chart("T (K)", "%.0f")
        self.temperature.line("temperature_K", "temperature")
        self.temperature.line("target", "target", dashed=True)
        self.energy = _Chart("E (kJ/mol)", "%.0f")
        for key, name in ENERGIES:
            self.energy.line(key, name)
        self.conservation = _Chart("H − H₀ (kJ/mol)", "%.1f")
        self.conservation.line("conserved_change", "conserved energy − first value")
        self.pressure = _Chart("P (bar)", "%.0f")
        self.pressure.line("pressure_bar", "total")
        self.pressure.line("pressure_kinetic", "kinetic 2K/3V")
        self.pressure.line("pressure_forces", "forces W/3V")
        self.pressure.line(
            "pressure_average", f"total, {MOVING_AVERAGE_PS:g} ps average", dashed=True
        )
        self.volume = _Chart("V (nm³)", "%.2f")
        self.volume.line("volume_nm3", "cell volume")
        self.tabs = QTabWidget()
        self.tabs.addTab(self.energy, "Energies")
        self.tabs.addTab(self.conservation, "Conservation")
        self.tabs.addTab(self.pressure, "Pressure")
        self.tabs.addTab(self.volume, "Volume")
        self.summary = QLabel("–")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.temperature)
        layout.addWidget(self.tabs)
        layout.addWidget(self.summary)
        self.target_K: float | None = None
        self.charts = (self.temperature, self.energy, self.conservation, self.pressure, self.volume)

    def reset(self, target_K: float | None) -> None:
        self.target_K = target_K
        for chart in self.charts:
            for series in chart.series.values():
                series.clear()
        self.summary.setText("–")

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
        h = tail.column("conserved_energy_kjmol")
        if len(h) == len(t):
            self.conservation.set_data("conserved_change", t, h - h[0])
        values = tail.column("volume_nm3")
        if len(values) == len(t):
            self.volume.set_data("volume_nm3", t, values)
        parts = pressure_parts(tail)
        if parts:
            times = parts.pop("time_ps")
            for key, values in parts.items():
                self.pressure.set_data(key, times, values)
        else:
            values = tail.column("pressure_bar")
            if len(values) == len(t):
                has = ~np.isnan(values)
                self.pressure.set_data("pressure_bar", t[has], values[has])
        for chart in self.charts:
            chart.rescale()
        self.summary.setText(summary_text(summary(tail)))

    def points(self, key: str) -> int:
        for chart in self.charts:
            if key in chart.series:
                return chart.series[key].count()
        return 0


def moving_average(t: np.ndarray, x: np.ndarray, window_ps: float) -> np.ndarray:
    """Mean of x over the trailing window (t − window, t]; shorter at the start."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    start = np.searchsorted(t, t - window_ps, side="right")
    end = np.arange(1, len(t) + 1)
    return (c[end] - c[start]) / (end - start)


def pressure_parts(tail: ThermoTail) -> dict[str, np.ndarray]:
    """The rows that have a pressure: time, total, kinetic part 2K/(3V), the part of the forces
    (total − kinetic) and the moving average of the total, in bar; empty if the table lacks a
    column."""
    t, p = tail.column("time_ps"), tail.column("pressure_bar")
    k, v = tail.column("kinetic_kjmol"), tail.column("volume_nm3")
    if not len(t) or not (len(p) == len(k) == len(v) == len(t)):
        return {}
    has = ~np.isnan(p)  # rows with a pressure (thermo.SPARSE_COLUMNS)
    t, p, k, v = t[has], p[has], k[has], v[has]
    kinetic = 2.0 * k / (3.0 * v) * BAR_PER_KJMOL_NM3
    return {
        "time_ps": t,
        "pressure_bar": p,
        "pressure_kinetic": kinetic,
        "pressure_forces": p - kinetic,
        "pressure_average": moving_average(t, p, MOVING_AVERAGE_PS),
    }


def summary_text(s: dict) -> str:
    if not s:
        return "–"
    lo, hi = s["window_ps"]
    parts = [f"{lo:.2f}–{hi:.2f} ps:"]
    for key, label, unit, digits in (
        ("temperature_K", "⟨T⟩", "K", 1),
        ("pressure_bar", "⟨P⟩", "bar", 0),
        ("potential_kjmol", "⟨E_pot⟩", "kJ/mol", 0),
        ("volume_nm3", "⟨V⟩", "nm³", 2),
    ):
        if key in s:
            text = f"{label} {s[key]['mean']:.{digits}f} ± {s[key]['sd']:.{digits}f} {unit}"
            if s[key]["rows"] < s["rows"]:
                text += f" ({s[key]['rows']} of {s['rows']} rows have a value)"
            parts.append(text)
    if "drift_kjmol_per_ps" in s:
        parts.append(f"drift of H {s['drift_kjmol_per_ps']:.2f} kJ/mol/ps")
    return "; ".join(parts[:1]) + " " + "; ".join(parts[1:])

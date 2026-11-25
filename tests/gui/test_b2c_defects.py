"""The two display defects of design/B2c.md §1: ⟨P⟩ with sparse rows, the NPT cell."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
import pyvista as pv

from ucmd_builder.gui.plots import LivePlots, summary_text
from ucmd_builder.gui.run_preview import RunPanel
from ucmd_builder.gui.scene import Scene
from ucmd_builder.gui.thermo import ThermoTail, summary
from ucmd_builder.gui.trajectory import XyzTail, read_h5md
from ucmd_builder.gui.viewer import SceneActors, cell_corners

COLUMNS = ("step", "time_ps", "kinetic_kjmol", "temperature_K", "pressure_bar", "volume_nm3")


def thermo(path: Path, every: int = 5, rows: int = 100, bad_temperature: bool = False) -> Path:
    lines = ["# " + "\t".join(COLUMNS)]
    for i in range(rows):
        p = f"{10.0 + i}" if i % every == 0 else "nan"
        t = "nan" if bad_temperature and i == rows - 1 else "300.0"
        lines.append(f"{i}\t{0.1 * i}\t100.0\t{t}\t{p}\t8.0")
    path.write_text("\n".join(lines) + "\n")
    return path


def tail(path: Path) -> ThermoTail:
    t = ThermoTail(path)
    t.poll()
    return t


def test_the_mean_pressure_is_the_mean_of_the_rows_that_have_one(tmp_path: Path) -> None:
    s = summary(tail(thermo(tmp_path / "t.tsv")))
    values = [10.0 + i for i in range(100) if i % 5 == 0 and 0.1 * i >= 0.1 * 99 * 0.5]
    assert s["pressure_bar"]["mean"] == pytest.approx(np.mean(values))
    assert s["pressure_bar"]["rows"] == len(values) and s["rows"] == 50
    assert f"({len(values)} of 50 rows have a value)" in summary_text(s)


def test_a_nan_temperature_still_shows(tmp_path: Path) -> None:
    """Only the pressure may be missing from a row; elsewhere nan is a fault, not a gap."""
    s = summary(tail(thermo(tmp_path / "t.tsv", bad_temperature=True)))
    assert np.isnan(s["temperature_K"]["mean"])


def test_a_table_without_any_pressure_has_no_mean(tmp_path: Path) -> None:
    s = summary(tail(thermo(tmp_path / "t.tsv", every=1000)))
    assert "pressure_bar" not in s


def test_the_pressure_plot_skips_empty_rows(qtbot, tmp_path: Path) -> None:
    plots = LivePlots()
    qtbot.addWidget(plots)
    plots.update_from(tail(thermo(tmp_path / "t.tsv")))
    plots.pressure.draw()
    for key in ("pressure_bar", "pressure_kinetic", "pressure_forces", "pressure_average"):
        series = plots.pressure.series[key]
        assert series.count() == 20
        assert np.isfinite([[p.x(), p.y()] for p in series.points()]).all()
    assert plots.points("volume_nm3") == 100


def npt_file(path: Path, box_steps=(0, 10, 20)) -> list[np.ndarray]:
    cells = [np.diag([2.0, 2.0, 2.0]) * f for f in (1.0, 1.1, 0.9)]
    with h5py.File(path, "w") as fh:
        for name, value, steps in (
            ("particles/all/position", np.zeros((3, 2, 3)) + 2.1, (0, 10, 20)),
            ("particles/all/box/edges", np.array(cells), box_steps),
        ):
            g = fh.create_group(name)
            g["step"] = np.array(steps)
            g["time"] = np.array(steps) * 0.001
            g["value"] = value
    return cells


def scene() -> Scene:
    return Scene(
        name="two",
        elements=("O", "Li"),
        positions_nm=np.array([[0.1, 0.1, 0.1], [1.0, 1.0, 1.0]]),
        bonds=np.empty((0, 2), dtype=int),
        cell_nm=np.diag([2.0, 2.0, 2.0]),
        molecule=np.array([0, 1]),
        doc={},
    )


def test_the_cell_follows_the_frames(qtbot, tmp_path: Path) -> None:
    cells = npt_file(tmp_path / "t.h5")
    trajectory = read_h5md(tmp_path / "t.h5")
    panel = RunPanel()
    qtbot.addWidget(panel)
    got = []
    panel.frame.connect(lambda x, c: got.append(c))
    panel.trajectory = trajectory
    for k in range(3):
        panel.show_frame(k)
    assert np.allclose(got, cells)
    plotter = pv.Plotter(off_screen=True, window_size=(100, 100))
    actors = SceneActors(plotter)
    actors.show(scene())
    for k, cell in enumerate(cells):
        actors.move(trajectory.positions_nm[k], trajectory.cell(k))
        assert np.allclose(actors._cell.points, cell_corners(cell))
        # wrapped in this frame's cell: 2.1 is inside 2.2, outside 2.0 and 1.8
        assert np.allclose(actors.points, 2.1 - np.floor(2.1 / cell[0, 0]) * cell[0, 0])
    actors.move(trajectory.positions_nm[0])  # a frame without a cell: the package's
    assert np.allclose(actors._cell.points, cell_corners(np.diag([2.0, 2.0, 2.0])))
    plotter.close()


def test_a_box_at_other_steps_is_refused(tmp_path: Path) -> None:
    npt_file(tmp_path / "t.h5", box_steps=(0, 10, 30))
    with pytest.raises(ValueError, match="steps of box/edges"):
        read_h5md(tmp_path / "t.h5")


def test_a_zero_box_is_no_cell(tmp_path: Path) -> None:
    npt_file(tmp_path / "t.h5")
    with h5py.File(tmp_path / "t.h5", "a") as fh:
        fh["particles/all/box/edges/value"][...] = 0.0
    assert read_h5md(tmp_path / "t.h5").cells_nm is None


def test_xyz_frames_carry_their_cell(tmp_path: Path) -> None:
    path = tmp_path / "t.xyz"
    path.write_text(
        "".join(
            f'1\nLattice="{a} 0 0 0 {a} 0 0 0 {a}" step={k} time_ps={k}\nO 1 2 3\n'
            for k, a in enumerate((20.0, 21.0))
        )
    )
    frames = XyzTail(path)
    frames.poll()
    assert np.allclose(frames.trajectory().cells_nm, [np.eye(3) * 2.0, np.eye(3) * 2.1])

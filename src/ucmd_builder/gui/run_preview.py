"""The run preview (design/B2a.md §7): settings dialog, engine process, live plots, playback.

The engine is an external program: `ucmd minimize`, then `ucmd run`, by the absolute path in
the machine profile (`[executables] ucmd`). The GUI reads the thermo table while the run goes
and the H5MD trajectory once it has ended. Cancel sends SIGTERM: the engine finishes its step,
writes a checkpoint, closes its files and exits with status 143.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..profile import executable, load_profile
from .molecule_file import Structure
from .plots import LivePlots
from .runs import (
    THERMOSTATS,
    RunInfo,
    RunOutputs,
    RunSettings,
    preview_cutoff,
    run_files,
    set_executable,
)
from .scene import Scene
from .thermo import ThermoTail
from .trajectory import Trajectory, XyzTail, is_live_format, read_trajectory

STOPPED = (130, 143)  # 128 + SIGINT, 128 + SIGTERM: the engine stopped gracefully


class RunDialog(QDialog):
    """Settings of a preview run, with the generated run.toml shown read-only."""

    def __init__(self, package: Path, scene: Scene, ask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run preview")
        self.package = package
        self.ask = ask
        defaults = RunSettings()
        self.temperature = self._number(defaults.temperature_K, 1.0, 5000.0, 2, " K")
        self.length = self._number(defaults.length_ps, 0.001, 10_000.0, 3, " ps")
        self.timestep = self._number(defaults.timestep_fs, 0.1, 10.0, 2, " fs")
        self.thermostat = QComboBox()
        self.thermostat.addItems(THERMOSTATS)
        self.tau = self._number(defaults.tau_ps, 0.001, 100.0, 3, " ps")
        self.minimize = QCheckBox("minimize first (FIRE)")
        self.minimize.setChecked(defaults.minimize)
        self.frames = QSpinBox()
        self.frames.setRange(1, 1_000_000)
        self.frames.setValue(defaults.frame_every)
        self.frames.setSuffix(" steps")
        self.trajectory_format = QComboBox()
        self.trajectory_format.addItem("XYZ: watch frames during the run", "xyz")
        self.trajectory_format.addItem("H5MD: for analysis (frames after the run)", "h5md")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_000_000_000)
        self.seed.setValue(defaults.seed)
        try:
            self.cutoff, warning = preview_cutoff(scene.cell_nm)
            cutoff_text = f"{self.cutoff:.2f} nm, PME to 1e-5" + (
                f"<br><b>{warning}</b>" if warning else ""
            )
        except (TypeError, ValueError) as e:  # no cell, or a box too small
            self.cutoff, cutoff_text = None, f"<b>cannot run: {e}</b>"
        self.cutoff_label = QLabel(cutoff_text)
        self.cutoff_label.setWordWrap(True)
        threads = load_profile().get("threads")
        self.threads_label = QLabel(
            f"{threads} (machine profile)" if threads else "all logical cores"
        )
        self.ucmd_label = QLabel()
        locate = QPushButton("Locate ucmd…")
        locate.clicked.connect(self._locate)
        ucmd_row = QHBoxLayout()
        ucmd_row.addWidget(self.ucmd_label, 1)
        ucmd_row.addWidget(locate)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        form = QFormLayout()
        form.addRow("Temperature", self.temperature)
        form.addRow("Length", self.length)
        form.addRow("Time step", self.timestep)
        form.addRow("Thermostat", self.thermostat)
        form.addRow("Coupling time τ", self.tau)
        form.addRow("", self.minimize)
        form.addRow("Frame every", self.frames)
        form.addRow("Trajectory", self.trajectory_format)
        form.addRow("Seed", self.seed)
        form.addRow("Cutoff", self.cutoff_label)
        form.addRow("Threads", self.threads_label)
        form.addRow("Engine", ucmd_row)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Files the run will use (read-only):"))
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.buttons)
        for box in (self.temperature, self.length, self.timestep, self.tau):
            box.valueChanged.connect(self.refresh)
        for spin in (self.frames, self.seed):
            spin.valueChanged.connect(self.refresh)
        self.thermostat.currentTextChanged.connect(self.refresh)
        self.trajectory_format.currentIndexChanged.connect(self.refresh)
        self.minimize.toggled.connect(self.refresh)
        self.resize(640, 820)
        self.refresh()

    @staticmethod
    def _number(
        value: float, low: float, high: float, decimals: int, suffix: str
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(decimals)
        box.setSuffix(suffix)
        box.setValue(value)
        return box

    def settings(self) -> RunSettings:
        return RunSettings(
            temperature_K=self.temperature.value(),
            length_ps=self.length.value(),
            timestep_fs=self.timestep.value(),
            thermostat=self.thermostat.currentText(),
            tau_ps=self.tau.value(),
            minimize=self.minimize.isChecked(),
            frame_every=self.frames.value(),
            seed=self.seed.value(),
            trajectory_format=self.trajectory_format.currentData(),
        )

    def refresh(self, *_args) -> None:
        ucmd = executable("ucmd")
        self.ucmd_label.setText(str(ucmd) if ucmd else "not configured in the machine profile")
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(ucmd is not None and self.cutoff is not None)
        if self.cutoff is None:
            self.preview.setPlainText("")
            return
        files = run_files(
            self.package,
            self.package.parent / f"{self.package.name}.runs" / "<run>",
            self.settings(),
            self.cutoff,
        )
        self.preview.setPlainText("\n".join(f"# {name}\n{text}" for name, text in files.items()))

    def _locate(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "The ucmd program")
        if path and self.ask(f"Write [executables] ucmd = {path!r} into the machine profile?"):
            set_executable("ucmd", Path(path))
            self.refresh()


class RunBrowser(QDialog):
    """The runs found below a folder; the user picks one to open."""

    COLUMNS = ("run", "title", "steps", "state", "trajectory", "changed")

    def __init__(self, folder: Path, runs: list[RunInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Open run — {len(runs)} runs in {folder.name}")
        self.runs = runs
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter, e.g. ec-emc 1.0M T20C")
        self.table = QTableWidget(len(runs), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for row, run in enumerate(runs):
            done = "–" if run.last_step is None else f"{run.last_step} / {run.steps}"
            changed = time.strftime("%Y-%m-%d %H:%M", time.localtime(run.modified))
            cells = (
                run.run_dir.relative_to(folder).as_posix()
                if run.run_dir.is_relative_to(folder)
                else str(run.run_dir),
                run.title,
                done,
                run.state,
                run.trajectory,
                changed,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        with_output = [k for k, r in enumerate(runs) if r.last_step is not None] or list(
            range(len(runs))
        )
        newest = max(with_output, key=lambda k: runs[k].modified)
        self.select(newest)
        self.filter.textChanged.connect(self._filter)
        self.table.doubleClicked.connect(lambda _index: self.accept())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Runs below {folder}. Double-click one, or select it and Open."))
        layout.addWidget(self.filter)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        self.resize(1000, 600)

    def _row_of(self, index: int) -> int:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == index:
                return row
        return -1

    def select(self, index: int) -> None:
        """Select runs[index] in the table, whatever the sort order."""
        self.table.selectRow(self._row_of(index))

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for row in range(self.table.rowCount()):
            line = " ".join(self.table.item(row, c).text() for c in range(len(self.COLUMNS)))
            self.table.setRowHidden(row, not all(w in line.lower() for w in words))

    def chosen(self) -> RunInfo | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.table.isRowHidden(rows[0].row()):
            return None
        return self.runs[self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)]


class RunController(QObject):
    """Runs `ucmd minimize` (optional) and `ucmd run` in a run directory."""

    line = Signal(str)
    stage = Signal(str)
    finished = Signal(int, str)  # exit status, what happened

    def __init__(self, ucmd: Path, run_dir: Path, minimize: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.ucmd = ucmd
        self.run_dir = run_dir
        self.minimize = minimize
        self.process: QProcess | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self.process is not None

    def start(self) -> None:
        if self.minimize:
            self._launch("minimizing", ["minimize", "minimize.toml", "--out", "minimized.ucsys"])
        else:
            self._launch("running", ["run", "run.toml"])

    def cancel(self) -> None:
        self._cancelled = True
        process = self.process
        if process is not None:
            self.line.emit("stopping the engine (SIGTERM) …")
            process.terminate()
            QTimer.singleShot(10_000, lambda: self._kill(process))

    def _kill(self, process: QProcess) -> None:
        if self.process is process and process.state() != QProcess.ProcessState.NotRunning:
            self.line.emit("the engine did not stop; killing it")
            process.kill()

    def _launch(self, stage: str, arguments: list[str]) -> None:
        process = QProcess(self)
        process.setWorkingDirectory(str(self.run_dir))
        process.setProgram(str(self.ucmd))
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.finished.connect(lambda code, _status: self._ended(process, stage, code))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        self.process = process
        self.stage.emit(stage)
        self.line.emit(f"$ {self.ucmd} {' '.join(arguments)}")
        process.start()

    def _read(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self.line.emit(line)

    def _error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and self.process is process:
            self.process = None
            self.finished.emit(-1, f"could not start {self.ucmd}: {process.errorString()}")

    def _ended(self, process: QProcess, stage: str, code: int) -> None:
        if self.process is not process:
            return
        self._read(process)
        self.process = None
        if code == 0 and stage == "minimizing" and not self._cancelled:
            self._launch("running", ["run", "run.toml"])
            return
        if code == 0:
            self.finished.emit(0, "finished")
        elif code in STOPPED:
            self.finished.emit(code, "stopped; the engine wrote a checkpoint")
        elif self._cancelled:
            self.finished.emit(code, "cancelled")
        else:
            self.finished.emit(code, f"{stage} failed (exit status {code}); see the log")


class RunPanel(QWidget):
    """Status, live plots and trajectory playback of the current run."""

    frame = Signal(object, object)  # positions and cell of the frame in nm (cell: or None)
    cancel_requested = Signal()
    structure = Signal(object)  # the Structure of a molecule file to show

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.structures: list[Structure] = []
        self.status = QLabel("No run yet. Build, then Run; or open an earlier run.")
        self.cancel_button = QPushButton("Stop run")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.plots = LivePlots()
        self.play_button = QPushButton("Play")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_label = QLabel("–")
        self.structure_box = QComboBox()  # only for molecule files with several structures
        self.structure_box.hide()
        self.structure_box.currentIndexChanged.connect(self._structure_chosen)
        for w in (self.play_button, self.slider):
            w.setEnabled(False)
        self.status.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.cancel_button)
        playback = QHBoxLayout()
        playback.addWidget(self.play_button)
        playback.addWidget(self.slider, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.structure_box)
        layout.addWidget(self.plots, 1)
        layout.addLayout(playback)
        layout.addWidget(self.frame_label)
        self.tail: ThermoTail | None = None
        self.trajectory: Trajectory | None = None
        self.outputs: RunOutputs | None = None
        self.frames: XyzTail | None = None
        self.following = False
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self.poll)
        self._player = QTimer(self)
        self._player.setInterval(100)
        self._player.timeout.connect(self._next_frame)
        self.play_button.clicked.connect(self.toggle_play)
        self.slider.valueChanged.connect(self.show_frame)

    def _reset(self, outputs: RunOutputs | None) -> None:
        self._poll.stop()
        self._prefix = ""
        self.outputs = outputs
        self.structures = []
        self.structure_box.hide()
        self.plots.show()
        thermo = outputs.thermo if outputs is not None else None
        path = outputs.trajectory if outputs is not None else None
        self.tail = ThermoTail(thermo) if thermo is not None else None
        live = path is not None and is_live_format(path)
        self.frames = XyzTail(path) if live else None
        self.following = False
        self.trajectory = None
        self._player.stop()
        self.play_button.setText("Play")
        self.play_button.setEnabled(False)
        self.slider.setEnabled(False)
        self.frame_label.setText("–")
        self.plots.reset(outputs.target_K if outputs is not None else None)

    def show_structures(self, structures: list[Structure]) -> None:
        """Molecule files: no plots; a list of structures, each with its frames."""
        self._reset(None)
        self.cancel_button.setEnabled(False)
        self.plots.hide()
        self.structures = structures
        self.structure_box.blockSignals(True)
        self.structure_box.clear()
        for s in structures:
            self.structure_box.addItem(f"{s.name} ({s.scene.n_atoms} atoms, {len(s.trajectory)})")
        self.structure_box.blockSignals(False)
        self.structure_box.setVisible(len(structures) > 1)
        self._structure_chosen(0)

    def close_structures(self) -> None:
        """Forget the molecule files: another scene is shown."""
        if self.structures:
            self._reset(None)
            self.status.setText("–")

    def _structure_chosen(self, k: int) -> None:
        if not 0 <= k < len(self.structures):
            return
        chosen = self.structures[k]
        self._player.stop()
        self.play_button.setText("Play")
        self.trajectory = None
        self.structure.emit(chosen)
        frames = len(chosen.trajectory)
        self.status.setText(
            f"{chosen.name}: {chosen.scene.n_atoms} atoms, {frames} frame{'s' * (frames != 1)}; "
            "bonds guessed from distances (display only)"
        )
        self.trajectory = chosen.trajectory
        several = frames > 1
        self.slider.setRange(0, frames - 1)
        self.slider.setEnabled(several)
        self.play_button.setEnabled(several)
        self.slider.setValue(frames - 1)  # the last one: for an optimization the result
        self.show_frame(frames - 1)

    def begin(self, outputs: RunOutputs) -> None:
        """A run that is starting: follow its thermo table (and XYZ frames) until `end`."""
        self._reset(outputs)
        self.cancel_button.setEnabled(True)
        self.status.setText(f"starting in {outputs.run_dir}")
        self._poll.start()

    def complete(self) -> bool:
        """Whether the thermo table has reached the last step of the run."""
        if self.tail is None or self.outputs is None or not len(self.tail):
            return False
        return int(self.tail.column("step")[-1]) >= self.outputs.steps

    def poll(self) -> None:
        if self.outputs is None:
            return
        if self.tail is not None and self.tail.poll():
            self.plots.update_from(self.tail)
            steps = self.tail.column("step")
            temperature = self.tail.column("temperature_K")
            text = f"step {int(steps[-1])} of {self.outputs.steps}"
            if len(temperature):
                text += f", T = {temperature[-1]:.1f} K"
            self.status.setText(f"{self._prefix}{text}")
        if self.frames is not None:
            try:
                new = self.frames.poll()
            except (OSError, ValueError) as e:
                self.status.setText(f"{self.status.text()}; frames not readable: {e}")
                self.frames = None
                return
            if new:
                self._show(self.frames.trajectory())
        if self.following and self.complete():  # an opened run has just finished
            self.following = False
            self._poll.stop()
            if self.frames is None:
                self._load_trajectory()

    _prefix = ""

    def end(self, message: str) -> None:
        """The run has ended: read the rest of the table and frames, then the H5MD file."""
        self._poll.stop()
        self.poll()
        self.cancel_button.setEnabled(False)
        self.status.setText(f"{self.status.text()} — {message}")
        if self.frames is None:
            self._load_trajectory()

    def show_run(self, outputs: RunOutputs) -> None:
        """A run opened from disk: finished, stopped, or still going (then it is followed)."""
        self._reset(outputs)
        self.cancel_button.setEnabled(False)
        self._prefix = f"{outputs.title} ({outputs.run_dir.name}): "
        self.status.setText(f"{self._prefix}no thermo table yet")
        self.poll()
        if self.complete():
            if self.frames is None:
                self._load_trajectory()
            return
        # Not at its last step: still running elsewhere, or stopped. Keep reading; H5MD frames
        # follow once the run has ended.
        self.following = True
        self._poll.start()
        if self.frames is None and self.outputs.trajectory is not None:
            self.status.setText(f"{self.status.text()}; frames once the run has ended (H5MD)")

    def stop_following(self) -> None:
        self.following = False
        self._poll.stop()

    def _show(self, trajectory: Trajectory) -> None:
        """New frames: keep the slider at the newest frame unless the user moved it back."""
        at_end = not self.slider.isEnabled() or self.slider.value() == self.slider.maximum()
        self.trajectory = trajectory
        if not len(trajectory):
            return
        self.slider.setRange(0, len(trajectory) - 1)
        self.slider.setEnabled(True)
        self.play_button.setEnabled(True)
        if at_end and not self._player.isActive():
            self.slider.setValue(len(trajectory) - 1)
        self.show_frame(self.slider.value())

    def _load_trajectory(self) -> None:
        path = self.outputs.trajectory if self.outputs is not None else None
        if path is None or not path.is_file():
            self.status.setText(f"{self.status.text()}; no trajectory")
            return
        try:
            trajectory = read_trajectory(path)
        except (OSError, KeyError, ValueError) as e:
            self.status.setText(f"{self.status.text()}; trajectory not readable: {e}")
            return
        self._show(trajectory)
        if len(trajectory):
            self.slider.setValue(0)
            self.show_frame(0)

    def show_frame(self, k: int) -> None:
        if self.trajectory is None or not 0 <= k < len(self.trajectory):
            return
        labels = self.trajectory.labels
        what = labels[k] if labels is not None else f"{self.trajectory.times_ps[k]:.3f} ps"
        self.frame_label.setText(f"frame {k + 1}/{len(self.trajectory)}, {what}")
        self.frame.emit(self.trajectory.positions_nm[k], self.trajectory.cell(k))

    def toggle_play(self) -> None:
        if self._player.isActive():
            self._player.stop()
            self.play_button.setText("Play")
        else:
            self._player.start()
            self.play_button.setText("Pause")

    def _next_frame(self) -> None:
        if self.trajectory is None:
            return
        self.slider.setValue((self.slider.value() + 1) % len(self.trajectory))


def write_run(package: Path, run_dir: Path, settings: RunSettings, cutoff_nm: float) -> None:
    for name, text in run_files(package, run_dir, settings, cutoff_nm).items():
        (run_dir / name).write_text(text, encoding="utf-8")

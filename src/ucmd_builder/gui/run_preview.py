"""The run preview (design/B2a.md §7): settings dialog, engine process, live plots, playback.

The engine is an external program: `ucmd minimize`, then `ucmd run`, by the absolute path in
the machine profile (`[executables] ucmd`). The GUI reads the thermo table while the run goes
and the H5MD trajectory once it has ended. Cancel sends SIGTERM: the engine finishes its step,
writes a checkpoint, closes its files and exits with status 143.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..profile import executable, load_profile
from .plots import LivePlots
from .runs import THERMOSTATS, RunSettings, preview_cutoff, run_files, set_executable
from .scene import Scene
from .thermo import ThermoTail
from .trajectory import Trajectory, read_trajectory

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

    frame = Signal(object)  # positions in nm
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status = QLabel("No run yet. Export a package, then Run preview.")
        self.cancel_button = QPushButton("Stop run")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.plots = LivePlots()
        self.play_button = QPushButton("Play")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_label = QLabel("–")
        for w in (self.play_button, self.slider):
            w.setEnabled(False)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.cancel_button)
        playback = QHBoxLayout()
        playback.addWidget(self.play_button)
        playback.addWidget(self.slider, 1)
        playback.addWidget(self.frame_label)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.plots, 1)
        layout.addLayout(playback)
        self.tail: ThermoTail | None = None
        self.trajectory: Trajectory | None = None
        self.settings: RunSettings | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self.poll)
        self._player = QTimer(self)
        self._player.setInterval(100)
        self._player.timeout.connect(self._next_frame)
        self.play_button.clicked.connect(self.toggle_play)
        self.slider.valueChanged.connect(self.show_frame)

    def begin(self, run_dir: Path, settings: RunSettings) -> None:
        self.settings = settings
        self.tail = ThermoTail(run_dir / "out" / "thermo.tsv")
        self.trajectory = None
        self._player.stop()
        self.play_button.setEnabled(False)
        self.slider.setEnabled(False)
        self.frame_label.setText("–")
        self.plots.reset(settings.temperature_K)
        self.cancel_button.setEnabled(True)
        self.status.setText(f"starting in {run_dir}")
        self._poll.start()

    def poll(self) -> None:
        if self.tail is None or self.settings is None:
            return
        if self.tail.poll():
            self.plots.update_from(self.tail)
            steps = self.tail.column("step")
            temperature = self.tail.column("temperature_K")
            self.status.setText(
                f"step {int(steps[-1])} of {self.settings.steps}, T = {temperature[-1]:.1f} K"
            )

    def end(self, run_dir: Path, message: str) -> None:
        self._poll.stop()
        self.poll()
        self.cancel_button.setEnabled(False)
        self.status.setText(f"{self.status.text()} — {message}")
        path = run_dir / "out" / "trajectory.h5"
        if path.is_file():
            try:
                self.trajectory = read_trajectory(path)
            except (OSError, KeyError) as e:
                self.status.setText(f"{self.status.text()}; trajectory not readable: {e}")
                return
            if len(self.trajectory):
                self.slider.setRange(0, len(self.trajectory) - 1)
                self.slider.setEnabled(True)
                self.play_button.setEnabled(True)
                self.show_frame(0)

    def show_frame(self, k: int) -> None:
        if self.trajectory is None or not 0 <= k < len(self.trajectory):
            return
        self.frame_label.setText(
            f"frame {k + 1}/{len(self.trajectory)}, {self.trajectory.times_ps[k]:.3f} ps"
        )
        self.frame.emit(self.trajectory.positions_nm[k])

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

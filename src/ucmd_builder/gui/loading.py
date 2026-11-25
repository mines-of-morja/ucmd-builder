"""Read a run from disk, in a thread or not (design/B2d.md §2.6)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from .runs import RunOutputs, run_outputs
from .scene import Scene, read_package
from .thermo import ThermoTail
from .trajectory import Trajectory, XyzTail, is_live_format, read_trajectory


@dataclass(frozen=True, eq=False)
class LoadedRun:
    """What the window needs to show a run; made without touching any widget."""

    outputs: RunOutputs
    scene: Scene
    tail: ThermoTail | None
    frames: XyzTail | None  # of a trajectory that can be read while it grows
    trajectory: Trajectory | None
    problem: str | None  # why there are no frames


def load_run(path: Path | str) -> LoadedRun:
    """The run at `path` (its folder or run.toml); raises OSError or ValueError."""
    outputs = run_outputs(path)
    if not outputs.system.is_dir():
        raise ValueError(
            f"{path}: its package {outputs.system} does not exist "
            "(for a GUI run: did the minimization finish?)"
        )
    scene = read_package(outputs.system)
    tail = ThermoTail(outputs.thermo) if outputs.thermo is not None else None
    last = -1
    if tail is not None and tail.poll():
        steps = tail.column("step")
        last = int(steps[-1]) if len(steps) else -1
    frames, trajectory, problem = None, None, None
    file = outputs.trajectory
    try:
        if file is not None and is_live_format(file):
            frames = XyzTail(file)
            frames.poll()
            trajectory = frames.trajectory()
        elif last >= outputs.steps:  # an H5MD file is read once the run has ended
            if file is not None and file.is_file():
                trajectory = read_trajectory(file)
            else:
                problem = "no trajectory"
    except (OSError, KeyError, ValueError) as e:
        frames, trajectory, problem = None, None, f"trajectory not readable: {e}"
    return LoadedRun(outputs, scene, tail, frames, trajectory, problem)


class LoadThread(QThread):
    """Loads one run; says which request it answers, so that a later one wins."""

    loaded = Signal(int, object)  # request, LoadedRun
    failed = Signal(int, str, str)  # request, path, reason

    def __init__(self, path: Path, request: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path, self.request = path, request

    def run(self) -> None:
        try:
            loaded = load_run(self.path)
        except Exception as e:  # whatever it is, the user is told
            reason = str(e) if isinstance(e, (OSError, ValueError)) else f"{type(e).__name__}: {e}"
            self.failed.emit(self.request, str(self.path), reason)
        else:
            self.loaded.emit(self.request, loaded)

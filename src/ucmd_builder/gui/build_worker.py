"""Building in a background thread with progress and cancel (design/B2a.md §5)."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ..build import build
from ..packing import Cancelled
from ..recipe import Recipe


class BuildWorker(QObject):
    """Runs `build(recipe)`; emits `built` with the BuiltSystem or `failed` with the reason."""

    progress = Signal(str)
    built = Signal(object)
    failed = Signal(str)

    def __init__(self, recipe: Recipe) -> None:
        super().__init__()
        self.recipe = recipe
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = build(self.recipe, progress=self.progress.emit, cancel=lambda: self._cancel)
        except Cancelled:
            self.failed.emit("cancelled")
        except Exception as e:  # reported to the user; the thread must not die silently
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.built.emit(result)


def start_build(recipe: Recipe, parent: QObject) -> tuple[QThread, BuildWorker]:
    """Start a worker in its own thread; the thread ends with the build."""
    thread = QThread(parent)
    worker = BuildWorker(recipe)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.built.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.start()
    return thread, worker

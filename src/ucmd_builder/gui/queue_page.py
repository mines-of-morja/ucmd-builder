"""The queue, read only (design/B2d.md §2.7, ADR 0016): what runs, what waits, on which resource.

The page asks `GET <url>/api/state` of the queue server (schema 1, docs/design/queue.md §3) and
shows the answer. It sends nothing else: no token, no command. The address is the user's choice,
kept in the GUI settings; without one the page asks nothing. A server that does not answer, or
answers in another schema, is shown as such and never blocks the window.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

SCHEMA = 1
LOCAL = "http://127.0.0.1:8765"
TIMEOUT_MS = 4000
EVERY_MS = 5000
MAX_BYTES = 5_000_000
OPEN_STATES = ("queued", "claimed", "running")


class QueueError(ValueError):
    """The answer is not a state document this GUI can read."""


@dataclass(frozen=True)
class QueueState:
    node: str
    time: str
    resources: tuple[dict, ...]
    running: tuple[dict, ...]
    jobs: tuple[dict, ...]
    errors: tuple[str, ...]
    received: float  # time.monotonic() when it arrived


def _text(value) -> str:
    return "" if value is None else str(value)


def parse_state(raw: bytes, received: float | None = None) -> QueueState:
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise QueueError(f"not JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        found = doc.get("schema") if isinstance(doc, dict) else type(doc).__name__
        raise QueueError(f"schema {found!r}, this GUI reads schema {SCHEMA}")

    def rows(key: str) -> tuple[dict, ...]:
        value = doc.get(key) or []
        if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
            raise QueueError(f"{key!r} is not a list of objects")
        return tuple(value)

    return QueueState(
        node=_text(doc.get("node")),
        time=_text(doc.get("time")),
        resources=rows("resources"),
        running=rows("running"),
        jobs=rows("jobs"),
        errors=tuple(_text(e) for e in doc.get("errors") or []),
        received=time.monotonic() if received is None else received,
    )


def cpu_jobs(state: QueueState) -> list[str]:
    """Titles of the running jobs that take the CPU of the server's machine."""
    return [
        _text(j.get("title") or j.get("id"))
        for j in state.jobs
        if j.get("state") in ("claimed", "running") and _text(j.get("needs")).startswith("cpu")
    ]


def _number(value, digits: int, unit: str = "") -> str:
    return f"{value:.{digits}f}{unit}" if isinstance(value, (int, float)) else "–"


def _duration(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "–"
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours} h {rest // 60:02d} min" if hours else f"{rest // 60} min"


class QueuePage(QWidget):
    """Resources, running study runs and the job list, refreshed while the page is seen."""

    run_requested = Signal(str, str)  # study, run
    url_chosen = Signal(str)
    answered = Signal()  # an answer, or the lack of one, has been shown

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url: str | None = None
        self.state: QueueState | None = None
        self.problem: str | None = None
        self.timeout_ms = TIMEOUT_MS
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(EVERY_MS)
        self._timer.timeout.connect(self.fetch)
        self.address = QLineEdit()
        self.address.setPlaceholderText(LOCAL)
        connect = QPushButton("Connect")
        connect.clicked.connect(self._connect)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.resources = QLabel()
        self.resources.setWordWrap(True)
        self.running_table = self._table(("study", "run", "progress", "ns/day", "left"))
        self.running_table.cellDoubleClicked.connect(self._running_chosen)
        self.finished = QCheckBox("finished jobs too")
        self.finished.toggled.connect(lambda _on: self._show())
        self.jobs_table = self._table(("state", "job", "needs", "priority", "submitted by", "on"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Queue server"))
        row.addWidget(self.address, 1)
        row.addWidget(connect)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(self.resources)
        layout.addWidget(QLabel("Running study runs (double-click opens the run)"))
        layout.addWidget(self.running_table, 1)
        jobs = QHBoxLayout()
        jobs.addWidget(QLabel("Jobs"), 1)
        jobs.addWidget(self.finished)
        layout.addLayout(jobs)
        layout.addWidget(self.jobs_table, 2)
        self.set_url(None)

    @staticmethod
    def _table(columns: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        return table

    def set_url(self, url: str | None) -> None:
        self.url = url or None
        self.address.setText(self.url or "")
        self.state, self.problem = None, None
        if self.url is None:
            self.status.setText(
                "Not connected. The page reads the queue server's state and changes nothing. "
                f"Enter its address (on this machine usually {LOCAL}) and press Connect."
            )
            self._show()
            return
        self.status.setText(f"asking {self.url} …")
        self.fetch()

    def _connect(self) -> None:
        url = self.address.text().strip() or LOCAL
        if QUrl(url).scheme() not in ("http", "https"):
            self.status.setText(f"{url!r}: the address must start with http:// or https://")
            return
        self.url_chosen.emit(url.rstrip("/"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()
        self.fetch()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def fetch(self) -> None:
        """Ask once; an answer still awaited is not asked again."""
        if self.url is None or self._reply is not None:
            return
        request = QNetworkRequest(QUrl(f"{self.url}/api/state"))
        request.setTransferTimeout(self.timeout_ms)
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda: self._answered(reply))

    def is_asking(self) -> bool:
        return self._reply is not None

    def _answered(self, reply: QNetworkReply) -> None:
        if reply is self._reply:
            self._reply = None
        reply.deleteLater()
        if reply.url().toString() != f"{self.url}/api/state":
            return  # an answer for an address no longer chosen
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise QueueError(reply.errorString())
            raw = bytes(reply.read(MAX_BYTES + 1))
            if len(raw) > MAX_BYTES:
                raise QueueError("the answer is too large")
            self.state, self.problem = parse_state(raw), None
        except QueueError as e:
            self.problem = f"{self.url}: {e}"
        self._show()
        self.answered.emit()

    def _show(self) -> None:
        state = self.state
        if self.problem:
            old = "; the tables below are from the last answer" if state else ""
            self.status.setText(f"no usable answer: {self.problem}{old}")
        elif state is not None:
            self.status.setText(
                f"{state.node} at {state.time} (read {time.strftime('%H:%M:%S')}); read only"
            )
        if state is None:
            self.resources.setText("")
            self.running_table.setRowCount(0)
            self.jobs_table.setRowCount(0)
            return
        parts = [f"{_text(r.get('name'))}: {_text(r.get('state'))}" for r in state.resources]
        cpu = cpu_jobs(state)
        if cpu:
            parts.append(f"CPU jobs: {', '.join(cpu)}")
        parts += [f"server reports: {e}" for e in state.errors]
        self.resources.setText("\n".join(parts))
        self._fill(
            self.running_table,
            [
                (
                    _text(r.get("study")),
                    _text(r.get("run")),
                    _number(100 * r["fraction"], 0, " %")
                    if isinstance(r.get("fraction"), (int, float))
                    else "–",
                    _number(r.get("ns_per_day"), 1),
                    _duration(r.get("eta_s")),
                )
                for r in state.running
            ],
        )
        jobs = [j for j in state.jobs if self.finished.isChecked() or j.get("state") in OPEN_STATES]
        self._fill(
            self.jobs_table,
            [
                (
                    _text(j.get("state")),
                    _text(j.get("title") or j.get("id")),
                    _text(j.get("needs")),
                    _text(j.get("priority")),
                    _text(j.get("submitted_by")),
                    _text(j.get("claimed_by")),
                )
                for j in jobs
            ],
        )

    @staticmethod
    def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(text))

    def _running_chosen(self, row: int, _column: int) -> None:
        study, run = (self.running_table.item(row, c).text() for c in (0, 1))
        if study and run:
            self.run_requested.emit(study, run)

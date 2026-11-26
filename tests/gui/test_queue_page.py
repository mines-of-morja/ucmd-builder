"""B2d step 5: the read-only queue page (design/B2d.md, ADR 0016), against a local test server."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path

import pytest
from fakes import FakeViewer
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel
from test_project import workspace  # noqa: F401  (fixture)

from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.queue_page import QueueError, cpu_jobs, parse_state
from ucmd_builder.gui.settings import Settings

STATE = {
    "schema": 1,
    "node": "workstation",
    "time": "2026-11-25T21:04:35Z",
    "resources": [
        {"name": "gpu0", "kind": "gpu", "state": "busy", "detail": "x"},
        {"name": "cpu", "kind": "cpu", "state": "idle", "detail": "load 11.8 of 16 cores"},
    ],
    "running": [
        {
            "study": "2026-11-24_scan",
            "run": "base_s1/T20C",
            "fraction": 0.25,
            "ns_per_day": 62.0,
            "eta_s": 7300,
        },
        {"study": "2026-11-24_scan", "run": "../../../secret", "fraction": 0.5},
    ],
    "jobs": [
        {
            "id": "j1",
            "title": "SC-3 CPU arms",
            "needs": "cpu",
            "state": "running",
            "priority": 0,
            "submitted_by": "s1",
            "claimed_by": "workstation-cpu",
        },
        {"id": "j2", "title": "<b>SC-1 water</b>", "needs": "gpu-hip-double", "state": "queued"},
        {"id": "j3", "title": "old", "needs": "cpu", "state": "done"},
    ],
    "errors": ["docker not readable"],
}


class Server:
    """A queue server stand-in: answers GET /api/state with what the test sets; counts methods."""

    def __init__(self) -> None:
        self.body, self.delay, self.requests = json.dumps(STATE).encode(), 0.0, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.requests.append(("GET", self.path, self.headers.get("Authorization")))
                time.sleep(outer.delay)
                self.send_response(200 if self.path == "/api/state" else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

            def do_POST(self) -> None:
                outer.requests.append(("POST", self.path, None))
                self.send_response(403)
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


@pytest.fixture
def window(qtbot):
    w = MainWindow(viewer=WidgetViewer())
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    w.show()
    return w


def answered(qtbot, page) -> None:
    qtbot.waitUntil(lambda: not page.is_asking(), timeout=10_000)


def test_the_state_is_read(server) -> None:
    state = parse_state(server.body)
    assert state.node == "workstation" and len(state.jobs) == 3
    assert cpu_jobs(state) == ["SC-3 CPU arms"]


@pytest.mark.parametrize(
    "raw, message",
    [
        (b"<html>", "not JSON"),
        (b"[]", "schema 'list'"),
        (b'{"schema": 2}', "schema 2"),
        (b'{"schema": 1, "jobs": "many"}', "'jobs' is not a list"),
    ],
)
def test_other_answers_are_refused(raw: bytes, message: str) -> None:
    with pytest.raises(QueueError, match=message):
        parse_state(raw)


def test_without_an_address_nothing_is_asked(qtbot, window, server) -> None:
    window.notebook.setCurrentWidget(window.queue_page)
    assert "Not connected" in window.queue_page.status.text()
    assert server.requests == [] and not window.queue_page.is_asking()


def test_the_page_shows_jobs_and_asks_only_get(qtbot, window, server, tmp_path) -> None:
    window.settings = Settings(path=tmp_path / "gui.json")
    page = window.queue_page
    window.notebook.setCurrentWidget(page)
    page.address.setText(server.url + "/")
    page._connect()
    answered(qtbot, page)
    assert window.settings.queue_url == server.url  # kept for the next start
    assert "read only" in page.status.text()
    assert "CPU jobs: SC-3 CPU arms" in page.resources.text()
    assert "server reports: docker not readable" in page.resources.text()
    assert page.running_table.rowCount() == 2
    assert [page.running_table.item(0, c).text() for c in range(5)] == [
        "2026-11-24_scan", "base_s1/T20C", "25 %", "62.0", "2 h 01 min",
    ]  # fmt: skip
    assert page.jobs_table.rowCount() == 2  # running and queued
    assert page.jobs_table.item(1, 1).text() == "<b>SC-1 water</b>"  # shown as text
    page.finished.setChecked(True)
    assert page.jobs_table.rowCount() == 3
    assert {(m, p, auth) for m, p, auth in server.requests} == {("GET", "/api/state", None)}


@pytest.mark.parametrize(
    "body, delay, message",
    [
        (b'{"schema": 7}', 0.0, "schema 7"),
        (b"{}", 0.0, "schema None"),
        (json.dumps(STATE).encode(), 3.0, ""),  # slower than the time allowed
    ],
)
def test_a_bad_or_slow_server_never_blocks(qtbot, window, server, body, delay, message) -> None:
    server.body, server.delay = body, delay
    page = window.queue_page
    page.timeout_ms = 500
    beats: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    page.set_url(server.url)
    answered(qtbot, page)
    assert "no usable answer" in page.status.text() and message in page.status.text()
    assert max((b - a for a, b in pairwise(beats)), default=0.0) < 0.1


def test_no_server_is_said(qtbot, window) -> None:
    window.queue_page.set_url("http://127.0.0.1:9")  # nothing listens there
    answered(qtbot, window.queue_page)
    assert "no usable answer" in window.queue_page.status.text()


def test_a_wrong_address_is_refused(window) -> None:
    window.queue_page.address.setText("file:///etc/passwd")
    window.queue_page._connect()
    assert "must start with http" in window.queue_page.status.text()
    assert window.queue_page.url is None


def test_a_running_run_opens_inside_the_workspace_only(
    qtbot,
    window,
    server,
    workspace: Path,  # noqa: F811
) -> None:
    window.set_workspace(workspace)
    page = window.queue_page
    page.set_url(server.url)
    answered(qtbot, page)
    page.running_table.cellDoubleClicked.emit(0, 0)
    assert window.shown == "base at 20 °C", window.log.toPlainText()
    page.running_table.cellDoubleClicked.emit(1, 0)
    assert "is not in" in window.log.toPlainText() and window.shown == "base at 20 °C"


def test_a_local_run_asks_while_a_cpu_job_runs(qtbot, window, server) -> None:
    asked = []
    window.ask = lambda q: asked.append(q) or False
    window.queue_page.set_url(server.url)
    assert not window.queue_cpu_ok() and "SC-3 CPU arms" in asked[0]
    server.body = json.dumps({**STATE, "jobs": STATE["jobs"][1:]}).encode()
    window.queue_page.state = None  # an old answer is asked again
    assert window.queue_cpu_ok() and len(asked) == 1
    window.queue_page.set_url("http://127.0.0.1:9")
    answered(qtbot, window.queue_page)
    assert window.queue_cpu_ok()
    assert "cannot tell whether its CPU jobs run" in window.log.toPlainText()

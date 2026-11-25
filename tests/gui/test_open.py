"""B2d step 2: one Open, drag and drop, recent files, the kept layout (design/B2d.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeViewer
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QLabel
from sample_recipes import ELECTROLYTE, STAMP
from test_molecule_file import cluster_xyz
from test_notebook import RUNS

from ucmd_builder.build import build
from ucmd_builder.gui.app import MainWindow
from ucmd_builder.gui.settings import MAX_RECENT, Settings
from ucmd_builder.package import write_package
from ucmd_builder.recipe import parse_recipe


class WidgetViewer(QLabel, FakeViewer):
    def __init__(self) -> None:
        QLabel.__init__(self)
        FakeViewer.__init__(self)


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    """One of every kind the GUI opens."""
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "small.toml").write_text(ELECTROLYTE.replace("{method}", "insertion"))
    (folder / "c.xyz").write_text(cluster_xyz())
    (folder / "tables").mkdir()
    (folder / "tables" / "runs.tsv").write_text(RUNS)
    recipe = parse_recipe((folder / "small.toml").read_text(), path=folder / "small.toml")
    write_package(build(recipe), folder / "p.ucsys", created_utc=STAMP)
    run = folder / "run"
    run.mkdir()
    (run / "run.toml").write_text(
        'spec_version = "0.1.0"\nsystem = "../p.ucsys"\ntitle = "a run"\n[run]\nsteps = 10\n'
        '[output]\n[output.thermo]\nfile = "thermo.tsv"\nevery = 10\n'
    )
    return {
        "recipe": folder / "small.toml",
        "molecule": folder / "c.xyz",
        "table": folder / "tables" / "runs.tsv",
        "tables": folder / "tables",
        "package": folder / "p.ucsys",
        "run": run,
        "run.toml": run / "run.toml",
    }


def make_window(qtbot, settings: Settings | None = None) -> MainWindow:
    w = MainWindow(viewer=WidgetViewer(), settings=settings)
    w.ask = lambda _question: True
    qtbot.addWidget(w)
    w.show()
    return w


class Drop:
    """What the window uses of a drag or drop event."""

    def __init__(self, urls: list[QUrl]) -> None:
        self.data, self.accepted = QMimeData(), False
        self.data.setUrls(urls)

    def mimeData(self) -> QMimeData:
        return self.data

    def acceptProposedAction(self) -> None:
        self.accepted = True


def drop(window: MainWindow, paths: list[Path]) -> None:
    event = Drop([QUrl.fromLocalFile(str(p)) for p in paths])
    window.dragEnterEvent(event)
    assert event.accepted
    window.dropEvent(event)


def test_a_web_address_is_not_accepted(qtbot) -> None:
    event = Drop([QUrl("https://example.org/a.xyz")])
    make_window(qtbot).dragEnterEvent(event)
    assert not event.accepted


KINDS = ("recipe", "molecule", "table", "tables", "package", "run", "run.toml")


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_opens_by_drop_and_from_recent(qtbot, files, tmp_path: Path, kind: str) -> None:
    path = files[kind]
    window = make_window(qtbot, Settings(path=tmp_path / "gui.json"))
    drop(window, [path])
    assert "cannot" not in window.log.toPlainText(), window.log.toPlainText()
    assert window.settings.recent == [str(path.resolve())]
    # a new start, from the settings file
    again = make_window(qtbot, Settings.load(tmp_path / "gui.json"))
    again.open_menu.aboutToShow.emit()
    (action,) = again._recent_actions
    assert action.isEnabled() and path.name in action.text()
    action.trigger()
    assert "cannot" not in again.log.toPlainText(), again.log.toPlainText()
    assert again.log.toPlainText().count("opened") >= 1


def test_recent_keeps_the_newest_first_and_greys_out_missing_files(qtbot, files, tmp_path) -> None:
    settings = Settings()
    for k in range(MAX_RECENT + 3):
        settings.remember(tmp_path / f"f{k}")
    settings.remember(files["molecule"])
    settings.remember(tmp_path / "f5")
    assert len(settings.recent) == MAX_RECENT
    assert settings.recent[:2] == [str(tmp_path / "f5"), str(files["molecule"].resolve())]
    window = make_window(qtbot, settings)
    window.open_menu.aboutToShow.emit()
    window.open_menu.aboutToShow.emit()  # listed once, however often it is opened
    assert len(window._recent_actions) == MAX_RECENT
    assert [a.isEnabled() for a in window._recent_actions[:2]] == [False, True]


def test_what_cannot_be_opened_is_said_and_not_remembered(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot)
    (tmp_path / "notes.txt").write_text("hello")
    window.open_by_user([tmp_path / "notes.txt", tmp_path / "gone.toml"])
    text = window.log.toPlainText()
    assert "notes.txt: not a recipe" in text and "gone.toml: it does not exist" in text
    assert window.settings.recent == []


def test_unsaved_edits_are_asked_about(qtbot, files) -> None:
    window = make_window(qtbot)
    window.open_recipe(files["recipe"])
    window.components.table.item(0, 2).setText("7")
    assert window.document.modified
    asked = []
    window.ask = lambda q: asked.append(q) or False
    window.open_by_user([files["recipe"]])
    assert len(asked) == 1 and window.document.modified  # kept
    window.open_by_user([files["molecule"]])
    assert len(asked) == 1 and window.document.modified  # looking at something loses nothing
    window.ask = lambda _q: True


@pytest.mark.parametrize(
    "text",
    ["{ broken", "[]", '{"schema": 99}', '{"schema": 1, "recent": 3}', '{"schema": 1, "state": 5}'],
)
def test_a_broken_settings_file_is_ignored_with_a_message(qtbot, tmp_path: Path, text) -> None:
    path = tmp_path / "gui.json"
    path.write_text(text)
    window = make_window(qtbot, Settings.load(path))
    assert "not used" in window.log.toPlainText() and not window.log_dock.isHidden()
    assert window.settings.recent == []


def test_a_broken_layout_is_ignored(qtbot) -> None:
    window = make_window(qtbot, Settings(geometry="äö not base64", state="QUJD"))
    assert not window.components_dock.isHidden() and window.width() > 300


KEPT = (780, 640)  # fits the 800 x 800 screen of Qt's offscreen platform


def test_the_layout_is_kept(qtbot, files, tmp_path: Path) -> None:
    path = tmp_path / "gui.json"
    window = make_window(qtbot, Settings(path=path))
    window.resize(*KEPT)
    window.ruler_dock.close()
    window.log_dock.show()
    window.open_path(files["table"])  # closed with a table in front
    window.close()
    assert json.loads(path.read_text())["schema"] == 1
    again = make_window(qtbot, Settings.load(path))
    qtbot.wait(50)
    assert (again.width(), again.height()) == KEPT
    assert again.ruler_dock.isHidden() and not again.properties_dock.isHidden()
    assert not again.log_dock.isHidden()


def test_without_settings_nothing_is_written(qtbot, files, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = make_window(qtbot)
    window.open_by_user([files["molecule"]])
    window.close()
    assert not (tmp_path / "config").exists()


def test_settings_that_cannot_be_written_do_not_stop_the_window(qtbot, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    window = make_window(qtbot, Settings(path=tmp_path / "file" / "gui.json"))
    assert window.close()
    assert "not saved" in window.settings.problem

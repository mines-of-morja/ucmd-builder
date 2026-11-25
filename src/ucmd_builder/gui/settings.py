"""What the GUI remembers between starts (design/B2d.md §2.5): window, recent files, workspace.

One JSON file per user, `~/.config/ucmd/gui.json`. It holds no physics and nothing a package or a
run needs; without it, or if it is broken, the GUI starts with its defaults. Writing it never
fails loudly: a full disk must not stop the program from closing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1
MAX_RECENT = 12


def default_settings_path() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "ucmd" / "gui.json"


@dataclass
class Settings:
    """`path` None: nothing is read or written (tests, embedding)."""

    path: Path | None = None
    recent: list[str] = field(default_factory=list)
    workspace: str | None = None
    queue_url: str | None = None
    geometry: str | None = None  # base64 of QMainWindow.saveGeometry()
    state: str | None = None  # base64 of QMainWindow.saveState()
    problem: str | None = None  # why the file was not used

    @classmethod
    def load(cls, path: Path | None) -> Settings:
        settings = cls(path=path)
        if path is None or not path.is_file():
            return settings
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                raise ValueError(f"not a settings file of schema {SCHEMA}")
            recent = doc.get("recent", [])
            if not isinstance(recent, list) or not all(isinstance(r, str) for r in recent):
                raise ValueError("'recent' is not a list of paths")
            texts = {k: doc.get(k) for k in ("workspace", "queue_url", "geometry", "state")}
            for key, value in texts.items():
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{key!r} is not a text")
        except (OSError, ValueError) as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            settings.problem = f"settings {path} not used: {e}"
            return settings
        settings.recent = recent[:MAX_RECENT]
        for key, value in texts.items():
            setattr(settings, key, value)
        return settings

    def remember(self, path: Path | str) -> None:
        """Put a path at the top of the recent list."""
        text = str(Path(path).resolve())
        self.recent = [text, *(r for r in self.recent if r != text)][:MAX_RECENT]
        self.save()

    def forget(self, path: Path | str) -> None:
        """Take a path that cannot be opened off the recent list."""
        text = str(Path(path).resolve())
        if text in self.recent:
            self.recent.remove(text)
            self.save()

    def save(self) -> None:
        if self.path is None:
            return
        doc = {
            "schema": SCHEMA,
            "recent": self.recent,
            "workspace": self.workspace,
            "queue_url": self.queue_url,
            "geometry": self.geometry,
            "state": self.state,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.problem = None
        except OSError as e:
            self.problem = f"settings not saved: {e}"

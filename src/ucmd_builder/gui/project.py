"""What lies in a workspace folder (design/B2d.md §2): studies, their runs and tables, files.

Plain functions without Qt; they read names, `study.toml` titles and the ends of thermo tables,
and write nothing. A study is a folder with `study.toml` (ADR 0011).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .runs import RunInfo, scan_runs
from .table_file import is_table


@dataclass(frozen=True)
class Study:
    path: Path
    question: str
    runs: tuple[RunInfo, ...]
    tables: tuple[Path, ...]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def done(self) -> int:
        return sum(r.state == "complete" for r in self.runs)


@dataclass(frozen=True)
class Project:
    root: Path
    studies: tuple[Study, ...] = ()
    files: dict[str, tuple[Path, ...]] = field(default_factory=dict)  # kind → paths


def _tables(folder: Path) -> tuple[Path, ...]:
    return tuple(sorted(p for p in folder.glob("*.tsv") if is_table(p)))


def study_tables(path: Path) -> tuple[Path, ...]:
    return _tables(path / "tables")


def read_study(path: Path) -> Study:
    """The study in this folder; an unreadable study.toml shows as such, it hides no runs."""
    try:
        doc = tomllib.loads((path / "study.toml").read_text(encoding="utf-8"))
        study = doc.get("study")
        question = str(study.get("question", "")) if isinstance(study, dict) else ""
    except (OSError, ValueError) as e:
        question = f"study.toml not readable: {e}"
    runs = tuple(scan_runs(path / "runs")) if (path / "runs").is_dir() else ()
    return Study(path, question, runs, study_tables(path))


def _is_recipe(path: Path) -> bool:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("format") == "ucmd-build"
    except (OSError, ValueError):
        return False


def scan_project(root: Path | str) -> Project:
    """The studies of a workspace (newest name first) and the files directly in the folder.

    `root` may be the workspace (it holds `studies/`), a folder of studies, one study, or any
    folder of recipes, packages, molecule files and tables. Only these places are looked at, so
    a large folder stays fast.
    """
    root = Path(root)
    if (root / "study.toml").is_file():
        places = [root]
    else:
        parent = root / "studies" if (root / "studies").is_dir() else root
        places = sorted((p.parent for p in parent.glob("*/study.toml")), reverse=True)
    files = {
        "Recipes": tuple(sorted(p for p in root.glob("*.toml") if _is_recipe(p))),
        "Packages": tuple(sorted(p.parent for p in root.glob("*.ucsys/system.json"))),
        "Molecules": tuple(sorted(root.glob("*.xyz"))),
        "Tables": _tables(root) + (() if places == [root] else _tables(root / "tables")),
    }
    return Project(
        root, tuple(read_study(p) for p in places), {k: v for k, v in files.items() if v}
    )


def study_of(path: Path | str) -> Path | None:
    """The study folder that holds `path` (or is it), if any."""
    path = Path(path).resolve()
    return next((p for p in (path, *path.parents) if (p / "study.toml").is_file()), None)


def find_workspace(path: Path | str) -> Path | None:
    """The folder above `path` that holds a `studies/` folder with studies in it, if any."""
    path = Path(path).resolve()
    for p in (path, *path.parents):
        if next((p / "studies").glob("*/study.toml"), None) is not None:
            return p
    return None


def find_runs(study: Path, name: str) -> list[RunInfo]:
    """The runs `runs/<name>/…` of a study; never anything outside its `runs` folder."""
    base = (study / "runs").resolve()
    target = (base / name).resolve()
    if not name.strip() or target == base or not target.is_relative_to(base):
        return []
    return scan_runs(target) if target.is_dir() else []

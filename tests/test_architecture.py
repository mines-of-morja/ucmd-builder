"""Import-boundary test (CLAUDE.md invariant 2).

UnitcellMD components never import each other's code; they communicate only through
the files and protocols defined in the spec. Importing the spec validator (ucmd_spec)
is allowed.
"""

import ast
from pathlib import Path

FORBIDDEN = ("ucmd_analysis", "ucmd_bridge", "ucmd_engine", "ucmd_launch")
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "ucmd_builder"


def imported_modules(path: Path) -> set[str]:
    """Absolute module names imported by a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_cross_component_imports() -> None:
    violations = [
        f"{path.relative_to(PACKAGE_DIR.parent)} imports {name}"
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        for name in sorted(imported_modules(path))
        if name.split(".")[0] in FORBIDDEN
    ]
    assert not violations, "\n".join(violations)


def test_scanner_detects_forbidden_imports(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text(f"import numpy\nfrom {FORBIDDEN[0]}.sub import thing\n", encoding="utf-8")
    assert f"{FORBIDDEN[0]}.sub" in imported_modules(source)


GUI_STACK = ("PySide6", "pyvista", "pyvistaqt", "vtk", "vtkmodules", "h5py")


def test_only_the_gui_package_uses_the_gui_stack() -> None:
    """The headless builder (design/B2a.md §1) never imports Qt, VTK or h5py."""
    gui = PACKAGE_DIR / "gui"
    violations = [
        f"{path.relative_to(PACKAGE_DIR.parent)} imports {name}"
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if gui not in path.parents
        for name in sorted(imported_modules(path))
        if name.split(".")[0] in GUI_STACK
    ]
    assert not violations, "\n".join(violations)


def test_the_library_does_not_import_the_gui() -> None:
    gui = PACKAGE_DIR / "gui"
    violations = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if gui in path.parents or path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                ("gui", "ucmd_builder.gui")
            ):
                violations.append(f"{path.relative_to(PACKAGE_DIR.parent)} imports the GUI")
    assert not violations, "\n".join(violations)

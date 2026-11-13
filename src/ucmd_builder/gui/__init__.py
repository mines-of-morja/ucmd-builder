"""The GUI preview (design/B2a.md): build, view and run systems without touching files.

Start it with `ucmd-build gui [RECIPE]`; it needs the extra `ucmd-builder[gui]`. The modules
`scene`, `elements` and `thermo` need no Qt; everything else imports PySide6.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Open the main window, with the recipe in `argv[0]` if given."""
    from .app import run_app

    return run_app(list(argv or []))

"""The spec validator finds its schemas by itself (ucmd-spec ≥ 0.1.1); UCMD_SPEC_DIR overrides."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ucmd_spec.system import validate_system_package

from ucmd_builder.package import UNITS


def _broken_package(tmp_path: Path) -> Path:
    package = tmp_path / "box.ucsys"
    package.mkdir()
    doc = {"format": "ucmd-system", "spec_version": "0.1.0", "units": UNITS, "name": 5}
    (package / "system.json").write_text(json.dumps(doc))
    return package


def test_the_bundled_schemas_are_used_without_ucmd_spec_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No environment variable, no checkout: a package is checked against the schemas (the
    verdict on this one is a schema error, not a missing file)."""
    monkeypatch.delenv("UCMD_SPEC_DIR", raising=False)
    report = validate_system_package(_broken_package(tmp_path))
    assert not report.ok
    assert report.error is not None and report.error.code == "E_SCHEMA"


def test_ucmd_spec_dir_still_overrides_the_schemas(tmp_path: Path) -> None:
    """The override is honoured, and a wrong one gives an error that names the fix. In a fresh
    process, because the validator caches the schemas it has loaded."""
    package = _broken_package(tmp_path)
    empty = tmp_path / "no-spec"
    empty.mkdir()
    code = (
        "import sys; from pathlib import Path\n"
        "from ucmd_builder.build import BuildError\n"
        "from ucmd_builder.package import validate\n"
        "try:\n    validate(Path(sys.argv[1]))\n"
        "except BuildError as e:\n    print(e)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(package)],
        env={**os.environ, "UCMD_SPEC_DIR": str(empty)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "cannot find the spec's JSON Schemas" in out and "UCMD_SPEC_DIR" in out

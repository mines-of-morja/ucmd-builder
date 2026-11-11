from importlib.metadata import version

import ucmd_builder


def test_version_matches_installed_metadata() -> None:
    # Catches a stale editable install or a version bumped in only one place.
    assert ucmd_builder.__version__ == version("ucmd-builder")

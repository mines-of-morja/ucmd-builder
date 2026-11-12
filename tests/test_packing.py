"""B1.5: packing under periodic boundary conditions."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import requires_packmol

from ucmd_builder.forcefield.library import load_template
from ucmd_builder.packing import Structure, make_whole
from ucmd_builder.packing.checks import CellGrid, min_intermolecular_distance
from ucmd_builder.packing.insertion import insert, random_rotation


def brute_min_distance(x: np.ndarray, mol: np.ndarray, box: float) -> float:
    d = x[:, None, :] - x[None, :, :]
    d -= box * np.round(d / box)
    r = np.sqrt((d * d).sum(-1))
    r[mol[:, None] == mol[None, :]] = np.inf
    return float(r.min())


def test_cell_grid_matches_brute_force() -> None:
    rng = np.random.default_rng(3)
    for box in (1.0, 2.7, 0.5):
        x = rng.uniform(0, box, size=(150, 3))
        mol = np.repeat(np.arange(50), 3)
        d, i, j = min_intermolecular_distance(x, mol, box, cutoff_nm=box)
        assert d == pytest.approx(brute_min_distance(x, mol, box), rel=1e-12)
        assert mol[i] != mol[j]


def test_rotations_are_proper_and_whole_molecules_survive() -> None:
    rng = np.random.default_rng(1)
    for _ in range(20):
        r = random_rotation(rng)
        assert np.allclose(r @ r.T, np.eye(3)) and np.linalg.det(r) == pytest.approx(1.0)
    x = np.array([[3.95, 0.0, 0.0], [0.05, 0.0, 0.0]])
    whole = make_whole(x, [2], 4.0)
    assert np.linalg.norm(whole[1] - whole[0]) == pytest.approx(0.1)


def water_structure(count: int) -> Structure:
    t = load_template("water-spcfw", "water")
    return Structure("water", t.graph.elements, t.parameters.coordinates_nm, count)


def check(result, structures, box, tolerance) -> None:
    sizes = [len(s.elements) for s in structures for _ in range(s.count)]
    mol = np.repeat(np.arange(len(sizes)), sizes)
    assert result.positions_nm.shape == (sum(sizes), 3)
    assert brute_min_distance(result.positions_nm, mol, box) >= tolerance * 0.9
    # Molecules are whole and rigid: intramolecular distances equal the template's.
    t = structures[0].coordinates_nm
    ref = np.linalg.norm(t[1] - t[0])
    for k in range(structures[0].count):
        m = result.positions_nm[3 * k : 3 * k + 3]
        assert np.linalg.norm(m[1] - m[0]) == pytest.approx(ref, abs=1e-5)


def test_insertion_is_deterministic_and_respects_the_tolerance() -> None:
    s = [water_structure(60)]
    a = insert(s, 2.0, 0.25, seed=11)
    b = insert(s, 2.0, 0.25, seed=11)
    assert np.array_equal(a.positions_nm, b.positions_nm)
    check(a, s, 2.0, 0.25)
    c = insert(s, 2.0, 0.25, seed=12)
    assert not np.array_equal(a.positions_nm, c.positions_nm)


def test_insertion_reports_an_impossible_density() -> None:
    from ucmd_builder.packing import PackingError

    with pytest.raises(PackingError, match="could not place"):
        insert([water_structure(400)], 1.0, 0.25, seed=1, max_attempts=50)


@requires_packmol
def test_packmol_is_deterministic_periodic_and_whole() -> None:
    from ucmd_builder.packing.packmol import pack

    s = [water_structure(200)]
    box = 1.9  # ≈ 0.88 g/cm3
    a = pack(s, box, 0.2, seed=5)
    b = pack(s, box, 0.2, seed=5)
    assert a.program.startswith("Packmol ")
    assert np.array_equal(a.positions_nm, b.positions_nm)
    check(a, s, box, 0.2)


@requires_packmol
def test_packmol_timeout_is_reported() -> None:
    from ucmd_builder.packing import PackingError
    from ucmd_builder.packing.packmol import pack

    with pytest.raises(PackingError, match="did not finish"):
        pack([water_structure(2000)], 1.0, 0.25, seed=1, timeout_s=1.0)


def test_packmol_from_the_machine_profile_and_its_failure(tmp_path, monkeypatch) -> None:
    """A fake Packmol that prints a version but never succeeds must stop the build."""
    from ucmd_builder.packing import PackingError
    from ucmd_builder.packing.packmol import find_packmol, pack

    fake = tmp_path / "fake-packmol"
    fake.write_text("#!/bin/sh\necho 'Version 21.2.3'\nexit 0\n")
    fake.chmod(0o755)
    profile = tmp_path / "machine.toml"
    profile.write_text(f'[executables]\npackmol = "{fake}"\n')
    monkeypatch.setenv("UCMD_MACHINE_PROFILE", str(profile))
    assert find_packmol() == fake
    with pytest.raises(PackingError, match="Packmol failed"):
        pack([water_structure(10)], 2.0, 0.2, seed=1)
    profile.write_text('[executables]\npackmol = "relative/packmol"\n')
    with pytest.raises(PackingError, match="absolute path"):
        find_packmol()


def test_grid_handles_small_boxes() -> None:
    g = CellGrid(0.5, 0.4)
    g.add(np.array([0.01, 0.0, 0.0]), 0)
    d, i = g.nearest(np.array([0.49, 0.0, 0.0]), exclude_owner=1)
    assert i == 0 and d == pytest.approx(0.02)

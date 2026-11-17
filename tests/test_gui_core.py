"""The GUI's Qt-free core (design/B2a.md): scenes from system.json, the thermo reader."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from sample_recipes import ELECTROLYTE, STAMP

from ucmd_builder.build import build
from ucmd_builder.gui.elements import color, radius_nm
from ucmd_builder.gui.scene import Scene, read_package
from ucmd_builder.gui.thermo import ThermoTail
from ucmd_builder.package import system_document, write_package
from ucmd_builder.recipe import parse_recipe


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("scene")
    recipe = parse_recipe(ELECTROLYTE.replace("{method}", "insertion"), path=tmp / "r.toml")
    return build(recipe)


def test_scene_of_a_built_system(built) -> None:
    doc = system_document(built, STAMP)
    scene = Scene.from_document(doc, built.positions_nm)
    assert scene.n_atoms == built.atom_count
    expected_bonds = sum(
        len(doc["molecule_types"][c["molecule_type"]].get("bonds", [])) * c["count"]
        for c in doc["components"]
    )
    assert scene.bonds.shape == (expected_bonds, 2)
    # every bond stays inside one molecule
    assert np.all(scene.molecule[scene.bonds[:, 0]] == scene.molecule[scene.bonds[:, 1]])
    li = scene.atom(0)
    assert (li.element, li.molecule_type, li.molecule) == ("Li", "Li", 0)
    assert li.charge_e == pytest.approx(0.75)  # ζ = 0.75 in the recipe
    assert li.sigma_nm == pytest.approx(0.287) and li.type is not None


def test_display_keeps_molecules_whole_and_in_the_cell(built) -> None:
    doc = system_document(built, STAMP)
    scene = Scene.from_document(doc, built.positions_nm)
    length = scene.cell_nm[0, 0]
    moved = scene.positions_nm.copy()
    moved[scene.molecule == 5] += np.array([length, -2 * length, 0.0])  # one molecule leaves
    shown = scene.display_positions(moved)
    centres = (
        np.stack([np.bincount(scene.molecule, weights=shown[:, k]) for k in range(3)], axis=1)
        / np.bincount(scene.molecule)[:, None]
    )
    assert np.all((centres >= 0.0) & (centres < length))
    i, j = scene.bonds.T
    assert np.allclose(
        np.linalg.norm(shown[i] - shown[j], axis=1), np.linalg.norm(moved[i] - moved[j], axis=1)
    )


def test_a_package_reads_back_as_the_same_scene(built, tmp_path: Path) -> None:
    out = write_package(built, tmp_path / "p.ucsys", created_utc=STAMP)
    disk = read_package(out)
    memory = Scene.from_document(system_document(built, STAMP), built.positions_nm)
    assert disk.elements == memory.elements
    assert np.array_equal(disk.positions_nm, memory.positions_nm)
    assert np.array_equal(disk.bonds, memory.bonds)


def test_element_colours_and_radii() -> None:
    assert color("O") == (255, 13, 13)
    assert color("Xx") == (255, 20, 147)  # unknown elements stand out
    assert 0.03 < radius_nm("H") < radius_nm("C") < radius_nm("P") < 0.07


def test_thermo_tail_reads_only_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "thermo.tsv"
    tail = ThermoTail(path)
    assert tail.poll() == 0  # no file yet
    header = "# step\ttime_ps\ttemperature_K\tconserved_energy_kjmol\n"
    path.write_text(header + "0\t0.0\t300.0\t-5.0\n10\t0.01\t299.0\t-5.1\n20\t0.0")
    assert tail.poll() == 2
    with path.open("a") as fh:
        fh.write("2\t298.0\t-5.2\n30\t0.03\t297.0\t-5.3\n")
    assert tail.poll() == 2
    assert list(tail.column("temperature_K")) == [300.0, 299.0, 298.0, 297.0]
    assert len(tail.column("pressure_bar")) == 0  # not in this table
    path.write_text(header + "0\t0.0\t310.0\t-6.0\n")  # truncated, e.g. by a restart
    tail.poll()
    assert list(tail.column("temperature_K")) == [310.0]


def test_preview_cutoff_follows_the_approved_rule() -> None:
    from ucmd_builder.gui.runs import preview_cutoff

    assert preview_cutoff(np.diag([4.276] * 3)) == (1.4, None)  # Lehnert's box keeps 1.4 nm
    cutoff, warning = preview_cutoff(np.diag([2.6] * 3))  # L/2 − 0.2 = 1.1 nm
    assert cutoff == pytest.approx(1.1) and "1.10 nm" in warning
    cutoff, _ = preview_cutoff(np.diag([2.79] * 3))  # 1.195 → rounded down to 1.15
    assert cutoff == pytest.approx(1.15)
    # triclinic: the face distances are 2.80, 2.60 and 9.0 nm, so 2.6 nm decides
    skew = np.array([[3.0, 0.0, 0.0], [1.0, 2.6, 0.0], [0.0, 0.0, 9.0]])
    assert preview_cutoff(skew)[0] == pytest.approx(1.1)
    # the face distance across a, not the edge length, can be the smallest: 2.5986 nm here
    assert preview_cutoff(np.array([[3.0, 0, 0], [1.5, 2.6, 0], [0, 0, 9.0]]))[0] == 1.05
    with pytest.raises(ValueError):
        preview_cutoff(np.diag([0.3] * 3))


def test_generated_run_files_pass_the_spec_validator(spec: Path, tmp_path: Path) -> None:
    from ucmd_spec.run_config import validate_run_config

    from ucmd_builder.gui.runs import RunSettings, new_run_dir, run_files
    from ucmd_builder.package import _spec_dir

    package = tmp_path / "box.ucsys"
    package.mkdir()
    run_dir = new_run_dir(package)
    assert run_dir.parent == tmp_path / "box.ucsys.runs"  # next to the package, not inside
    for settings in (RunSettings(), RunSettings(thermostat="csvr", minimize=False)):
        files = run_files(package, run_dir, settings, 1.1)
        assert set(files) == ({"minimize.toml", "run.toml"} if settings.minimize else {"run.toml"})
        for name, text in files.items():
            (run_dir / name).write_text(text)
            with _spec_dir():
                report = validate_run_config(run_dir / name)
            assert report.ok, [i.message for i in report.issues]
    assert 'system = "../../box.ucsys"' in files["run.toml"]


def test_set_executable_keeps_the_rest_of_the_profile(tmp_path: Path) -> None:
    from ucmd_builder.gui.runs import set_executable

    profile = tmp_path / "machine.toml"
    profile.write_text(
        '# my workstation\nthreads = 8\n\n[executables]\npackmol = "/opt/packmol"  # keep\n'
    )
    set_executable("ucmd", tmp_path / "bin" / "ucmd", profile)
    text = profile.read_text()
    assert "# my workstation" in text and 'packmol = "/opt/packmol"  # keep' in text
    assert f'ucmd = "{(tmp_path / "bin" / "ucmd").resolve()}"' in text
    set_executable("ucmd", tmp_path / "other", profile)  # replaced, not added twice
    assert profile.read_text().count("ucmd =") == 1
    fresh = tmp_path / "new" / "machine.toml"
    set_executable("ucmd", tmp_path / "u", fresh)
    import tomllib

    assert tomllib.loads(fresh.read_text())["executables"]["ucmd"].endswith("/u")


def test_run_outputs_follow_the_run_toml(tmp_path: Path) -> None:
    from ucmd_builder.gui.runs import run_outputs

    run = tmp_path / "run"
    run.mkdir()
    (run / "run.toml").write_text(
        'spec_version = "0.1.0"\nsystem = "../box.ucsys"\ntitle = "a run"\n'
        "[run]\nsteps = 50\n"
        '[thermostat]\nkind = "csvr"\ntemperature_K = 300.0\ntau_ps = 0.1\n'
        '[output]\ndirectory = "results"\n'
        '[output.thermo]\nfile = "t.tsv"\nevery = 10\n'
    )
    out = run_outputs(run)  # the folder, or the file itself
    assert out == run_outputs(run / "run.toml")
    assert out.run_dir == run and out.title == "a run" and out.steps == 50
    assert out.system == run / "../box.ucsys"
    assert out.thermo == run / "results" / "t.tsv"
    assert out.trajectory is None  # no [output.trajectory]
    assert out.target_K == 300.0
    (run / "run.toml").write_text('system = "p.ucsys"\n[output.trajectory]\nfile = "x.h5"\n')
    out = run_outputs(run)
    assert out.trajectory == run / "out" / "x.h5"  # the spec's default directory
    assert out.target_K is None and out.thermo is None
    (run / "run.toml").write_text("title = 'no system'\n")
    with pytest.raises(ValueError, match="system"):
        run_outputs(run)
    with pytest.raises(OSError):
        run_outputs(tmp_path / "nowhere")


def test_xyz_tail_reads_only_complete_frames(tmp_path: Path) -> None:
    from ucmd_builder.gui.trajectory import XyzTail, read_trajectory

    path = tmp_path / "t.xyz"
    frame = (
        '2\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3 pbc="T T T" '
        "step={s} time_ps={t}\nO 1.0 2.0 3.0\nH 4.0 5.0 {z}\n"
    )
    first, second = frame.format(s=0, t=0.0, z=6.0), frame.format(s=10, t=0.01, z=7.0)
    tail = XyzTail(path)
    assert tail.poll() == 0  # no file yet
    path.write_text(first + second[:30])  # the second frame is still being written
    assert tail.poll() == 1
    path.write_text(first + second)
    assert tail.poll() == 1 and tail.poll() == 0
    traj = tail.trajectory()
    assert traj.steps.tolist() == [0, 10] and traj.times_ps.tolist() == [0.0, 0.01]
    assert np.allclose(traj.positions_nm[1], [[0.1, 0.2, 0.3], [0.4, 0.5, 0.7]])  # Å → nm
    path.write_text(first)  # rewritten (a restart cut it back): read again from the start
    assert tail.poll() == 1 and len(tail.trajectory()) == 1
    assert len(read_trajectory(path)) == 1


def write_run(folder: Path, steps: int = 100, done: int | None = None) -> Path:
    folder.mkdir(parents=True)
    (folder / "run.toml").write_text(
        f'system = "x.ucsys"\ntitle = "{folder.name}"\n[run]\nsteps = {steps}\n'
        '[output.thermo]\nfile = "thermo.tsv"\n[output.trajectory]\nfile = "t.h5"\n'
    )
    if done is not None:
        (folder / "out").mkdir()
        rows = "".join(f"{s}\t{s / 1000}\t300.0\n" for s in range(0, done + 1, 10))
        (folder / "out" / "thermo.tsv").write_text("# step\ttime_ps\ttemperature_K\n" + rows)
    return folder


def test_scan_runs_finds_every_run_below_any_folder(tmp_path: Path) -> None:
    import time

    from ucmd_builder.gui.runs import scan_runs

    # examples/EC/runs: packages with their <package>.runs folders
    ec = tmp_path / "EC" / "runs"
    (ec / "a.ucsys").mkdir(parents=True)
    (ec / "a.ucsys" / "system.json").write_text("{}")
    write_run(ec / "a.ucsys.runs" / "20260101-100000")  # minimized, never ran
    write_run(ec / "a.ucsys.runs" / "20260101-110000", done=100)
    # a study: runs/<system>/<T>, and views/ with symbolic links to them
    study = tmp_path / "study"
    write_run(study / "runs" / "s1" / "T20C", done=40)
    (study / "views" / "by-T" / "T20C").mkdir(parents=True)
    (study / "views" / "by-T" / "T20C" / "s1").symlink_to(study / "runs" / "s1" / "T20C")
    runs = scan_runs(ec)
    assert [r.run_dir.name for r in runs] == ["20260101-100000", "20260101-110000"]
    assert [r.state for r in runs] == ["no output", "complete"]
    assert runs[1].last_step == 100 and runs[1].trajectory == "h5md"
    assert scan_runs(ec / "a.ucsys") == runs  # a package: its runs next to it
    (study_run,) = scan_runs(study)  # the view's link is not a second run
    assert study_run.last_step == 40 and study_run.state == "running?"  # changed just now
    old = time.time() - 3600
    import os

    os.utime(study_run.run_dir / "out" / "thermo.tsv", (old, old))
    os.utime(study_run.run_toml, (old, old))
    assert scan_runs(study)[0].state == "stopped"
    assert scan_runs(tmp_path / "nothing") == []


def test_summary_of_the_thermo_table(tmp_path: Path) -> None:
    from ucmd_builder.gui.thermo import summary

    path = tmp_path / "thermo.tsv"
    rows = [f"{k}\t{k / 100}\t{300 + (-1) ** k}\t{-5.0 + 0.5 * k / 100}" for k in range(0, 11)]
    path.write_text(
        "# step\ttime_ps\ttemperature_K\tconserved_energy_kjmol\n" + "\n".join(rows) + "\n"
    )
    tail = ThermoTail(path)
    tail.poll()
    s = summary(tail)
    assert s["window_ps"] == (0.05, 0.1) and s["rows"] == 6
    assert s["temperature_K"]["mean"] == pytest.approx(300.0)
    assert s["drift_kjmol_per_ps"] == pytest.approx(0.5)  # H grows by 0.5 kJ/mol per ps
    assert summary(ThermoTail(tmp_path / "missing.tsv")) == {}

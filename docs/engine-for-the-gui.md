# Which file to pick at "Locate ucmd…"

The builder GUI does not run MD itself. **Run…** starts the UnitcellMD engine, a separate
program called `ucmd`. The GUI needs to know where that program is. You tell it once with
**Locate ucmd…**, and it remembers the answer in your machine profile.

Paths below are relative to the workspace folder `UnitcellMD/`, which holds `ucmd-builder/`
and `ucmd-engine/` side by side.

## 1. The file to pick

```
UnitcellMD/ucmd-engine/target/release/ucmd
```

- It is a program file with no extension. Its name is exactly `ucmd`.
- It is not a folder and not a text file.
- It exists only after the engine has been built (section 2).

## 2. Build the engine first (once, and after engine updates)

```sh
. "$HOME/.cargo/env"          # only if `cargo` is not found
cd UnitcellMD/ucmd-engine
git switch b2a                # the branch with the graceful Stop (see section 4)
cargo build --release         # a few minutes the first time
target/release/ucmd --version # prints e.g. "ucmd 0.0.0 (7c0e00dbf1fb)"
```

If `--version` prints a line that starts with `ucmd`, that file is the one to pick.

## 3. In the GUI

1. Build a system, or open a package, then **Run…** (Ctrl+R).
2. In the run dialog, the row **Engine** shows the current path, or
   "not configured in the machine profile". Click **Locate ucmd…**.
3. In the file dialog, go to `UnitcellMD/ucmd-engine/target/release/` and select the file
   `ucmd`. Click **Open**.
4. Confirm the question "Write [executables] ucmd = … into the machine profile?" with **Yes**.
5. The **Engine** row now shows `…/ucmd-engine/target/release/ucmd`. Click **Run**.

The next time, the dialog already shows this path, and there is nothing to do.

## 4. Files that look similar but are wrong

| File | Why not |
|---|---|
| `ucmd-engine/README.md`, `ucmd-engine-ff/README.md` or any other `.md` / `.toml` file | text, not a program: the run fails with "could not start …" |
| `ucmd-engine/target/release/` (the folder) | the dialog needs the file inside it |
| `ucmd-engine/target/release/ucmd.d` | a build bookkeeping file |
| `ucmd-engine/target/release/deps/ucmd-<letters and digits>` | intermediate build products; they change with every build |
| `ucmd-engine/target/debug/ucmd` | works, but is an unoptimized build and runs many times slower |
| `ucmd-engine-ff/target/release/ucmd` | the engine with the second force-field provider (ucmd-ff), on a branch made before the graceful Stop. The GUI always uses the engine's own `force-field` provider, so this build gives no extra from the GUI, and **Stop run** kills it without a checkpoint |

## 5. Where the choice is stored, and how to fix a wrong one

The GUI writes one line into the machine profile, the first of these files that exists:

1. the file named by the environment variable `UCMD_MACHINE_PROFILE`;
2. `$XDG_CONFIG_HOME/ucmd/machine.toml`;
3. `~/.config/ucmd/machine.toml` (the usual case).

The entry looks like this, with the absolute path on your machine:

```toml
[executables]
ucmd = "/absolute/path/to/UnitcellMD/ucmd-engine/target/release/ucmd"
```

To fix a wrong choice, either click **Locate ucmd…** again and pick the right file, or edit
this line in a text editor. Other entries in the file stay untouched either way.

## 6. Check that it works

- `start-gui.sh` prints a hint at start-up if the profile has no engine.
- After **Run**, the log shows `$ …/ucmd minimize minimize.toml --out minimized.ucsys`, then
  the engine's progress lines. Temperature and energies start plotting on the right.
- If the log says `could not start …`, the profile points at a wrong file (section 4).

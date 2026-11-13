"""Recipes shared by the build tests and the GUI tests."""

ARGON = """
format = "ucmd-build"
version = "0.1"
name = "argon-small"
seed = 3

[forcefield]
source = "library"
set = "argon-rahman1964"

[[component]]
name = "Ar"
molecule = { library = "Ar" }
count = 108

[box]
density_g_cm3 = { value = 1.374, source = "A. Rahman, Phys. Rev. 136, A405 (1964)" }

[packing]
method = "insertion"
tolerance_nm = 0.25

[velocities]
temperature_K = 94.4
"""

ELECTROLYTE = """
format = "ucmd-build"
version = "0.1"
name = "lipf6-small"
seed = 4

[forcefield]
source = "library"
set = "opls-aa-carbonates"

[[component]]
name = "Li"
molecule = { smiles = "[Li+]" }
count = 3
charge_scale = 0.75

[[component]]
name = "PF6"
molecule = { smiles = "F[P-](F)(F)(F)(F)F" }
count = 3
charge_scale = 0.75

[[component]]
name = "EC"
molecule = { library = "EC" }
count = 10

[[component]]
name = "EMC"
molecule = { smiles = "CCOC(=O)OC" }
count = 20

[box]
length_nm = 2.6

[packing]
method = "{method}"
tolerance_nm = 0.2
"""

STAMP = "2026-11-12T00:00:00Z"

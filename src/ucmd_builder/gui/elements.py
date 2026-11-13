"""Element colours and radii for the 3D view (display only, no physics).

Colours: the CPK scheme of Jmol (https://jmol.sourceforge.net/jscolors/), as in spike S3.
Radii: RDKit's van der Waals radii (Bondi) × 0.3, a ball-and-stick look.
"""

from __future__ import annotations

from functools import cache

from ..constants import ANGSTROM_IN_NM

JMOL_COLORS = {
    "H": "#FFFFFF",
    "He": "#D9FFFF",
    "Li": "#CC80FF",
    "Be": "#C2FF00",
    "B": "#FFB5B5",
    "C": "#909090",
    "N": "#3050F8",
    "O": "#FF0D0D",
    "F": "#90E050",
    "Ne": "#B3E3F5",
    "Na": "#AB5CF2",
    "Mg": "#8AFF00",
    "Al": "#BFA6A6",
    "Si": "#F0C8A0",
    "P": "#FF8000",
    "S": "#FFFF30",
    "Cl": "#1FF01F",
    "Ar": "#80D1E3",
    "K": "#8F40D4",
    "Ca": "#3DFF00",
}
OTHER = "#FF1493"  # elements without an entry stand out
BALL_SCALE = 0.3


def color(element: str) -> tuple[int, int, int]:
    """RGB (0–255) of an element."""
    code = JMOL_COLORS.get(element, OTHER).lstrip("#")
    return int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16)


@cache
def radius_nm(element: str) -> float:
    """Display radius in nm: the van der Waals radius × 0.3."""
    from rdkit import Chem

    try:
        r_angstrom = Chem.GetPeriodicTable().GetRvdw(element)
    except RuntimeError:
        r_angstrom = 1.5
    return BALL_SCALE * r_angstrom * ANGSTROM_IN_NM

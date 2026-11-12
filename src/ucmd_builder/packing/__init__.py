"""Packing molecules into a periodic cubic box (PLAN.md B1.5).

Two methods share one interface: Packmol (the default, for liquids at real densities) and
a built-in random-insertion packer (for tests and dilute systems, without external
programs). Both return positions in canonical order: components in recipe order, then
instances, then atoms in template order. Molecules are whole (unwrapped, PLAN.md D11).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


class PackingError(RuntimeError):
    """Packing failed; the message says why and what to change."""


class Cancelled(RuntimeError):
    """The caller cancelled the build (GUI)."""


Progress = Callable[[str], None]
Cancel = Callable[[], bool]


@dataclass(frozen=True, eq=False)
class Structure:
    """One component to pack: a reference geometry (nm) and how many copies."""

    name: str
    elements: tuple[str, ...]
    coordinates_nm: np.ndarray
    count: int


@dataclass(frozen=True)
class PackResult:
    positions_nm: np.ndarray
    method: str
    program: str
    seed: int
    min_distance_nm: float
    notes: tuple[str, ...] = ()


def make_whole(positions: np.ndarray, sizes: list[int], box_nm: float) -> np.ndarray:
    """Unwrap each molecule relative to its first atom (minimum image in a cubic box)."""
    out = positions.copy()
    start = 0
    for n in sizes:
        ref = out[start]
        d = out[start : start + n] - ref
        d -= box_nm * np.round(d / box_nm)
        out[start : start + n] = ref + d
        start += n
    return out

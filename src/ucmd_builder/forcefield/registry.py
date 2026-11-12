"""Discovery of force-field sources through Python entry points (ADR 0008 §1).

Every source, the built-in ones included, registers in the entry-point group
`ucmd_builder.forcefield_sources`. A third-party package adds a source by declaring, e.g.,

    [project.entry-points."ucmd_builder.forcefield_sources"]
    openff = "ucmd_ff_openff:OpenFFSource"

and nothing in the builder changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from .api import ForceFieldSource, SourceError

GROUP = "ucmd_builder.forcefield_sources"


@dataclass(frozen=True)
class SourceInfo:
    name: str
    target: str
    distribution: str


def available() -> dict[str, SourceInfo]:
    """All registered sources by name. Two sources with the same name are an error."""
    found: dict[str, SourceInfo] = {}
    for ep in metadata.entry_points(group=GROUP):
        dist = ep.dist.name if ep.dist is not None else "?"
        info = SourceInfo(ep.name, ep.value, dist)
        if ep.name in found and found[ep.name].target != ep.value:
            raise SourceError(
                f"force-field source {ep.name!r} is registered twice: by "
                f"{found[ep.name].distribution} ({found[ep.name].target}) "
                f"and by {dist} ({ep.value})"
            )
        found[ep.name] = info
    return dict(sorted(found.items()))


def get(name: str) -> ForceFieldSource:
    """An instance of the source registered under `name`."""
    for ep in metadata.entry_points(group=GROUP):
        if ep.name == name:
            available()  # duplicate check
            cls = ep.load()
            source = cls()
            if not isinstance(source, ForceFieldSource):
                raise SourceError(f"{ep.value} does not implement ForceFieldSource")
            return source
    names = ", ".join(available()) or "none"
    raise SourceError(f"unknown force-field source {name!r}; installed: {names}")

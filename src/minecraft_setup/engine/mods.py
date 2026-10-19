import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from minecraft_setup.engine.gamedir import GameDir

DISABLED = ".disabled"
_PREFIX = re.compile(r"^(\d)(\d) ")


class Profile(Enum):
    """Mod profiles, declared from the least to the most modded."""

    VANILLA = "vanilla"
    FULL = "full"
    SOLO = "solo"


PROFILE_GROUPS: dict[Profile, frozenset[int]] = {
    Profile.VANILLA: frozenset(),
    Profile.FULL: frozenset({4, 5}),
    Profile.SOLO: frozenset({4, 5, 6}),
}
MANAGED: frozenset[int] = frozenset().union(*PROFILE_GROUPS.values())
SOLO_GROUPS = PROFILE_GROUPS[Profile.SOLO] - PROFILE_GROUPS[Profile.FULL]


@dataclass(frozen=True)
class ModFile:
    path: Path
    group: int | None
    enabled: bool

    @property
    def name(self) -> str:
        """The jar name without the disabled suffix."""
        return self.path.name if self.enabled else self.path.name[: -len(DISABLED)]

    @property
    def managed(self) -> bool:
        return self.group in MANAGED


def group_of(name: str) -> int | None:
    """The jar's group digit from its `NN ` prefix, None when unprefixed."""
    prefix = _PREFIX.match(name)
    return int(prefix.group(1)) if prefix else None


def _is_jar(name: str) -> bool | None:
    """True for an enabled jar, False for a disabled one, None for anything else."""
    lower = name.lower()
    if lower.endswith(".jar"):
        return True
    if lower.endswith(".jar" + DISABLED):
        return False
    return None


def list_mods(game: GameDir) -> list[ModFile]:
    if not game.mods.is_dir():
        return []
    mods = []
    for path in sorted(game.mods.iterdir(), key=lambda p: p.name):
        enabled = _is_jar(path.name)
        if enabled is None or not path.is_file():
            continue
        mods.append(ModFile(path, group_of(path.name), enabled))
    return mods


def _matches(mods: list[ModFile], profile: Profile) -> bool:
    groups = PROFILE_GROUPS[profile]
    return all(m.enabled == (m.group in groups) for m in mods if m.managed)


def detect(mods: list[ModFile]) -> Profile | Literal["mixed"]:
    # first match wins, so a pack without 6x jars reads as full rather than solo
    for profile in Profile:
        if _matches(mods, profile):
            return profile
    return "mixed"


def available(mods: list[ModFile]) -> list[Profile]:
    has_solo = any(m.group in SOLO_GROUPS for m in mods)
    return [p for p in Profile if p is not Profile.SOLO or has_solo]


def leaving(mods: list[ModFile]) -> Profile:
    """The profile whose worlds the current mods may have touched."""
    current = detect(mods)
    if current != "mixed":
        return current
    top = max(m.group for m in mods if m.managed and m.enabled)
    return next(p for p in Profile if top in PROFILE_GROUPS[p])

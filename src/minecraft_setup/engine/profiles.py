from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import DISABLED, PROFILE_GROUPS, ModFile, Profile, available, detect, leaving, list_mods
from minecraft_setup.engine.state import read_since, write_since
from minecraft_setup.engine.worlds import marker, opened_since, tag, world_dirs, world_profile


class GuardError(Exception):
    """A refusal raised before anything in the game dir was changed."""


class GameRunningError(GuardError):
    def __init__(self) -> None:
        super().__init__("Minecraft is running - close the game first.")


@dataclass
class SwitchReport:
    tagged: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


def _label(profile: Profile | Literal["mixed"]) -> str:
    return profile.value if isinstance(profile, Profile) else profile


def _refuse_while_running(running: Callable[[], bool]) -> None:
    if running():
        raise GameRunningError()


def _refuse_unavailable(mods: list[ModFile], profile: Profile) -> None:
    if profile not in available(mods):
        raise GuardError(f"'{profile.value}' is not available: there are no solo mods (6x jars) in mods.")


def _since(game: GameDir) -> datetime:
    try:
        return read_since(game)
    except ValueError as error:
        raise GuardError(str(error)) from error


def _hide_plan(
    game: GameDir, active: Profile | Literal["mixed"], tags: dict[Path, Profile]
) -> list[tuple[Path, Path]]:
    """Moves of foreign worlds out of saves, counting the tags about to be written."""
    plan = []
    for world in world_dirs(game.saves):
        profile = tags.get(world) or world_profile(world)
        if profile is not None and profile != active:
            plan.append((world, game.vault(profile) / world.name))
    for world, dest in plan:
        if dest.exists():
            raise GuardError(
                f"World '{world.name}' already exists in {dest.parent.name} - rename one of them and retry."
            )
    return plan


def _rename_plan(mods: list[ModFile], target: Profile) -> list[tuple[ModFile, Path]]:
    groups = PROFILE_GROUPS[target]
    plan = []
    for mod in mods:
        if not mod.managed or mod.enabled == (mod.group in groups):
            continue
        new = mod.path.with_name(mod.path.name + DISABLED if mod.enabled else mod.name)
        if new.exists():
            raise GuardError(f"Both '{mod.name}' and '{mod.name}{DISABLED}' exist in mods - remove one and retry.")
        plan.append((mod, new))
    return plan


def _hide(plan: list[tuple[Path, Path]]) -> list[str]:
    for world, dest in plan:
        dest.parent.mkdir(exist_ok=True)
        world.rename(dest)
    return [world.name for world, _ in plan]


def _restore(game: GameDir, active: Profile) -> tuple[list[str], list[str]]:
    """Move the active profile's vault back into saves, keeping worlds whose name is taken there."""
    restored, kept = [], []
    for world in world_dirs(game.vault(active)):
        dest = game.saves / world.name
        if dest.exists():
            kept.append(world.name)
            continue
        game.saves.mkdir(exist_ok=True)
        world.rename(dest)
        restored.append(world.name)
    return restored, kept


def _find_world(game: GameDir, name: str) -> Path:
    for folder in [game.saves] + [game.vault(p) for p in Profile]:
        for world in world_dirs(folder):
            if world.name == name:
                return world
    raise GuardError(f"World '{name}' not found in saves or profile vaults.")


def switch(
    game: GameDir, target: Profile, *, running: Callable[[], bool], now: Callable[[], datetime]
) -> SwitchReport:
    """Enable exactly the target's mod groups, guarding worlds against the wrong mods."""
    _refuse_while_running(running)
    mods = list_mods(game)
    _refuse_unavailable(mods, target)
    same = detect(mods) == target
    tags: dict[Path, Profile] = {}
    if not same:
        since = _since(game)
        left = leaving(mods)
        tags = {w: left for w in world_dirs(game.saves) if world_profile(w) is None and opened_since(w, since)}
    hides = _hide_plan(game, target, tags)
    renames = _rename_plan(mods, target)

    report = SwitchReport()
    for world, profile in tags.items():
        tag(world, profile)
        report.tagged.append(world.name)
    report.hidden = _hide(hides)
    for mod, new in renames:
        mod.path.rename(new)
        (report.disabled if mod.enabled else report.enabled).append(mod.name)
    report.restored, report.kept = _restore(game, target)
    if not same:
        write_since(game, now())
    return report


def assign(game: GameDir, profile: Profile, names: list[str], *, running: Callable[[], bool]) -> list[str]:
    """Tag untouched worlds with `profile`, then hide and restore worlds for the current mods."""
    _refuse_while_running(running)
    mods = list_mods(game)
    _refuse_unavailable(mods, profile)
    current = detect(mods)
    since = _since(game)
    tags: dict[Path, Profile] = {}
    for name in names:
        world = _find_world(game, name)
        owner = world_profile(world)
        if owner == profile:
            continue
        if owner is not None:
            raise GuardError(
                f"'{name}' belongs to '{owner.value}' - delete its {marker(owner)} file by hand"
                f" only if you are sure it has no {owner.value} content."
            )
        if current != profile and opened_since(world, since):
            label = _label(current)
            raise GuardError(f"'{name}' was opened on '{label}' - it may already contain {label} content.")
        tags[world] = profile
    hides = _hide_plan(game, current, tags)

    for world in tags:
        tag(world, profile)
    _hide(hides)
    if current != "mixed":
        _restore(game, current)
    return [world.name for world in tags]

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import DISABLED, PROFILE_GROUPS, ModFile, Profile, available, detect, leaving, list_mods
from minecraft_setup.engine.process import is_minecraft_running
from minecraft_setup.engine.state import read_since, write_since
from minecraft_setup.engine.worlds import marker, opened_since, tag, world_dirs, world_profile

Move = tuple[Path, Path]


class GuardError(Exception):
    """A refusal raised before anything in the game dir was changed."""


class GameRunningError(GuardError):
    def __init__(self) -> None:
        super().__init__("Minecraft is running - close the game first.")


@dataclass
class SwitchReport:
    tagged: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    relocated: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


class SwitchInterrupted(Exception):
    """A file operation failed part-way; `report` lists what had already changed."""

    def __init__(self, report: SwitchReport, error: OSError) -> None:
        super().__init__(f"Stopped part-way, some changes were already made: {error}")
        self.report = report


def local_now() -> datetime:
    return datetime.now().astimezone()


def _label(profile: Profile | Literal["mixed"]) -> str:
    return profile.value if isinstance(profile, Profile) else profile


def _refuse_while_running(running: Callable[[], bool]) -> None:
    if running():
        raise GameRunningError()


def refuse_unavailable(mods: list[ModFile], profile: Profile) -> None:
    if profile not in available(mods):
        raise GuardError(f"'{profile.value}' is not available: there are no solo mods (6x jars) in mods.")


def _refuse_non_folder(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise GuardError(f"'{path.name}' exists but is not a folder - move it out of the way and retry.")


def _since(game: GameDir) -> datetime:
    try:
        return read_since(game)
    except ValueError as error:
        raise GuardError(str(error)) from error


def _owner(world: Path, tags: dict[Path, Profile]) -> Profile | None:
    """The world's profile, counting the tags about to be written."""
    return tags.get(world) or world_profile(world)


def _hide_plan(game: GameDir, active: Profile | Literal["mixed"], tags: dict[Path, Profile]) -> list[Move]:
    """Moves of foreign worlds out of saves into their own vaults."""
    plan = []
    for world in world_dirs(game.saves):
        profile = _owner(world, tags)
        if profile is not None and profile != active:
            plan.append((world, game.vault(profile) / world.name))
    return plan


def _relocate_plan(game: GameDir, active: Profile | Literal["mixed"], tags: dict[Path, Profile]) -> list[Move]:
    """Moves of tagged worlds out of vaults that are not their own."""
    plan = []
    for vault in Profile:
        for world in world_dirs(game.vault(vault)):
            profile = _owner(world, tags)
            if profile is not None and profile != vault:
                home = game.saves if profile == active else game.vault(profile)
                plan.append((world, home / world.name))
    return plan


def _restore_plan(game: GameDir, active: Profile | Literal["mixed"], tags: dict[Path, Profile]) -> list[Path]:
    """Worlds in the active vault that are its own or untagged."""
    if active == "mixed":
        return []
    return [w for w in world_dirs(game.vault(active)) if _owner(w, tags) in (active, None)]


def _check_moves(game: GameDir, moves: list[Move], restores: list[Path]) -> None:
    """Refuse moves that would land on a file, a taken name or another planned move."""
    if restores:
        _refuse_non_folder(game.saves)
    taken = set()
    for world, dest in moves:
        _refuse_non_folder(dest.parent)
        if dest.exists():
            raise GuardError(
                f"World '{world.name}' already exists in {dest.parent.name} - rename one of them and retry."
            )
        key = str(dest).casefold()
        if key in taken:
            raise GuardError(
                f"Two worlds named '{world.name}' belong in {dest.parent.name} - rename one of them and retry."
            )
        taken.add(key)


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


@contextmanager
def _mutating(report: SwitchReport) -> Iterator[None]:
    try:
        yield
    except OSError as error:
        raise SwitchInterrupted(report, error) from error


def _tag(tags: dict[Path, Profile], report: SwitchReport) -> None:
    for world, profile in tags.items():
        tag(world, profile)
        report.tagged.append(world.name)


def _move(moves: list[Move], done: list[str]) -> None:
    for world, dest in moves:
        dest.parent.mkdir(exist_ok=True)
        world.rename(dest)
        done.append(world.name)


def _restore(game: GameDir, worlds: list[Path], report: SwitchReport) -> None:
    """Move own worlds back into saves, keeping the vault copy when the name is taken there."""
    for world in worlds:
        dest = game.saves / world.name
        if dest.exists():
            report.kept.append(world.name)
            continue
        game.saves.mkdir(exist_ok=True)
        world.rename(dest)
        report.restored.append(world.name)


def _find_world(game: GameDir, name: str) -> Path:
    # casefold like the Windows filesystem and the old script's -eq
    key = name.casefold()
    folders = [game.saves] + [game.vault(p) for p in Profile]
    found = [w for folder in folders for w in world_dirs(folder) if w.name.casefold() == key]
    if not found:
        raise GuardError(f"World '{name}' not found in saves or profile vaults.")
    if len(found) > 1:
        where = ", ".join(f"{w.parent.name}/{w.name}" for w in found)
        raise GuardError(f"'{name}' matches more than one world ({where}) - rename all but one and retry.")
    return found[0]


@dataclass
class SwitchPlan:
    same: bool
    tags: dict[Path, Profile]
    hides: list[Move]
    relocations: list[Move]
    restores: list[Path]
    renames: list[tuple[ModFile, Path]]


def plan_switch(game: GameDir, target: Profile, mods: list[ModFile]) -> SwitchPlan:
    """Run every switch refusal for `mods` (real or projected) without changing anything."""
    refuse_unavailable(mods, target)
    same = detect(mods) == target
    tags: dict[Path, Profile] = {}
    if not same:
        since = _since(game)
        left = leaving(mods)
        tags = {w: left for w in world_dirs(game.saves) if world_profile(w) is None and opened_since(w, since)}
    hides = _hide_plan(game, target, tags)
    relocations = _relocate_plan(game, target, tags)
    restores = _restore_plan(game, target, tags)
    _check_moves(game, hides + relocations, restores)
    return SwitchPlan(same, tags, hides, relocations, restores, _rename_plan(mods, target))


def switch(
    game: GameDir,
    target: Profile,
    *,
    running: Callable[[], bool] = is_minecraft_running,
    now: Callable[[], datetime] = local_now,
) -> SwitchReport:
    """Enable exactly the target's mod groups, guarding worlds against the wrong mods."""
    _refuse_while_running(running)
    plan = plan_switch(game, target, list_mods(game))

    report = SwitchReport()
    with _mutating(report):
        _tag(plan.tags, report)
        _move(plan.hides, report.hidden)
        for mod, new in plan.renames:
            mod.path.rename(new)
            (report.disabled if mod.enabled else report.enabled).append(mod.name)
        # after the renames, since some of these land in saves
        _move(plan.relocations, report.relocated)
        _restore(game, plan.restores, report)
        if not plan.same:
            write_since(game, now())
    return report


def assign(
    game: GameDir, profile: Profile, names: list[str], *, running: Callable[[], bool] = is_minecraft_running
) -> list[str]:
    """Tag untouched worlds with `profile`, then hide, relocate and restore worlds for the current mods."""
    _refuse_while_running(running)
    mods = list_mods(game)
    refuse_unavailable(mods, profile)
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
    relocations = _relocate_plan(game, current, tags)
    restores = _restore_plan(game, current, tags)
    _check_moves(game, hides + relocations, restores)

    report = SwitchReport()
    with _mutating(report):
        _tag(tags, report)
        _move(hides, report.hidden)
        _move(relocations, report.relocated)
        _restore(game, restores, report)
    return report.tagged

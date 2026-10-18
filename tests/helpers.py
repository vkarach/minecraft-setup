import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import PROFILE_GROUPS, ModFile, Profile, list_mods
from minecraft_setup.engine.state import write_since

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BEFORE = T0 - timedelta(hours=1)
AFTER = T0 + timedelta(hours=1)
LATER = T0 + timedelta(days=1)

CLIENT = ["01 lib - cloth.jar", "fabric-api.jar"]
FULL = ["40 world - terralith.jar", "50 mobs - friends.jar"]
SOLO = ["60 rpg - spell engine.jar", "61 rpg [needs tl-skins-off] - wizards.jar"]


def make_jar(game: GameDir, name: str, enabled: bool = True) -> Path:
    path = game.mods / (name if enabled else name + ".disabled")
    path.touch()
    return path


def make_world(
    game: GameDir,
    name: str,
    marker: str | None = None,
    opened_at: datetime | None = None,
    where: Path | None = None,
) -> Path:
    world = (where if where is not None else game.saves) / name
    world.mkdir(parents=True)
    level = world / "level.dat"
    level.touch()
    if marker is not None:
        (world / f".{marker}-profile").touch()
    if opened_at is not None:
        stamp = opened_at.timestamp()
        os.utime(level, (stamp, stamp))
    return world


def idle() -> bool:
    return False


def busy() -> bool:
    return True


def clock() -> datetime:
    return LATER


def fill(game: GameDir, on=(), off=()) -> list[ModFile]:
    for path in list(game.mods.iterdir()):
        path.unlink()
    for name in on:
        make_jar(game, name)
    for name in off:
        make_jar(game, name, enabled=False)
    return list_mods(game)


def pack(game: GameDir, profile: Profile, solo: bool = True) -> list[ModFile]:
    """Install CLIENT, FULL and optionally SOLO jars switched to `profile`, with `since` at T0."""
    managed = FULL + (SOLO if solo else [])
    on = [n for n in managed if int(n[0]) in PROFILE_GROUPS[profile]]
    off = [n for n in managed if n not in on]
    write_since(game, T0)
    return fill(game, on=CLIENT + on, off=off)


def files(game: GameDir) -> list[str]:
    return sorted(p.name for p in game.mods.iterdir())


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

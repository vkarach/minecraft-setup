from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import Profile


@dataclass(frozen=True)
class World:
    path: Path
    profile: Profile | None
    visible: bool

    @property
    def name(self) -> str:
        return self.path.name


def marker(profile: Profile) -> str:
    return f".{profile.value}-profile"


def world_dirs(folder: Path) -> list[Path]:
    """World folders (those holding level.dat) directly in `folder`, by name."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if (p / "level.dat").is_file()), key=lambda p: p.name)


def world_profile(path: Path) -> Profile | None:
    # the most modded marker wins, as the old script checked full before vanilla
    for profile in reversed(Profile):
        if (path / marker(profile)).is_file():
            return profile
    return None


def tag(path: Path, profile: Profile) -> None:
    (path / marker(profile)).touch()


def opened_since(path: Path, since: datetime) -> bool:
    opened = datetime.fromtimestamp((path / "level.dat").stat().st_mtime, timezone.utc)
    return opened >= since


def list_worlds(game: GameDir) -> list[World]:
    worlds = [World(p, world_profile(p), True) for p in world_dirs(game.saves)]
    for profile in Profile:
        worlds += [World(p, world_profile(p), False) for p in world_dirs(game.vault(profile))]
    return worlds

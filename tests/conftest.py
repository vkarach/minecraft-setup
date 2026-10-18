import os
from datetime import datetime
from pathlib import Path

import pytest

from minecraft_setup.engine.gamedir import GameDir


@pytest.fixture
def game(tmp_path: Path) -> GameDir:
    root = tmp_path / ".minecraft"
    (root / "mods").mkdir(parents=True)
    (root / "saves").mkdir()
    return GameDir(root)


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

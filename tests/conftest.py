from pathlib import Path

import pytest

from minecraft_setup.engine.gamedir import GameDir


@pytest.fixture
def game(tmp_path: Path) -> GameDir:
    root = tmp_path / ".minecraft"
    (root / "mods").mkdir(parents=True)
    (root / "saves").mkdir()
    return GameDir(root)

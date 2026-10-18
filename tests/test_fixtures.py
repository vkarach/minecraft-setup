from datetime import datetime

from helpers import make_jar, make_world


def test_game_has_empty_mods_and_saves(game):
    assert game.mods.is_dir()
    assert game.saves.is_dir()
    assert list(game.mods.iterdir()) == []
    assert game.state_file == game.root / "mods-profile.state.json"


def test_make_jar_enabled_and_disabled(game):
    on = make_jar(game, "40 a.jar")
    off = make_jar(game, "41 b[1].jar", enabled=False)
    assert on == game.mods / "40 a.jar" and on.is_file()
    assert off == game.mods / "41 b[1].jar.disabled" and off.is_file()


def test_make_world_with_marker_and_mtime(game):
    when = datetime(2026, 1, 2, 3, 4, 5)
    world = make_world(game, "W [1]", marker="solo", opened_at=when)
    assert (world / "level.dat").is_file()
    assert (world / ".solo-profile").is_file()
    assert datetime.fromtimestamp((world / "level.dat").stat().st_mtime) == when


def test_make_world_custom_dir_without_marker(game, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    world = make_world(game, "V", where=vault)
    assert world.parent == vault
    assert not any(p.name.startswith(".") for p in world.iterdir())

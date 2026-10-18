import re
import zipfile
from pathlib import Path

import pytest

from helpers import CLIENT, FULL, SOLO, busy, idle, make_jar
from minecraft_setup.engine.export import PARTS, export
from minecraft_setup.engine.profiles import GameRunningError


def populate(game):
    for name in CLIENT + FULL + SOLO:
        make_jar(game, name)
    make_jar(game, "62 rpg - off.jar", enabled=False)
    (game.mods / "_README.txt").write_text("readme")
    (game.root / "config").mkdir()
    (game.root / "config" / "mod.json").write_text("{}")
    (game.root / "defaultconfigs").mkdir()
    (game.root / "defaultconfigs" / "d.toml").write_text("")
    (game.root / "resourcepacks").mkdir()
    (game.root / "resourcepacks" / "pack.zip").write_text("z")
    (game.root / "shaderpacks").mkdir()
    (game.root / "shaderpacks" / "s.zip").write_text("z")
    (game.root / "options.txt").write_text("o")
    (game.root / "servers.dat").write_text("s")
    skin = game.root / "CustomSkinLoader"
    (skin / "LocalSkin" / "skins").mkdir(parents=True)
    (skin / "CustomSkinLoader.json").write_text("{}")
    (skin / "LocalSkin" / "skins" / "me.png").write_text("p")
    (skin / "Other.txt").write_text("x")


def run(game, tmp_path, parts=None, *, include_solo=False, running=idle, app_exe=None):
    return export(
        game, parts, include_solo=include_solo, out_dir=tmp_path / "out", app_exe=app_exe, running=running
    )


def entries(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        return sorted(archive.namelist())


def mod_entries(zip_path):
    return [n for n in entries(zip_path) if n.startswith("mods/")]


def test_default_excludes_6x(game, tmp_path):
    populate(game)
    path = run(game, tmp_path)
    assert re.fullmatch(r"modpack-all-\d{4}-\d\d-\d\d_\d\d-\d\d\.zip", path.name)
    assert path.parent == tmp_path / "out"
    assert mod_entries(path) == [
        "mods/01 lib - cloth.jar",
        "mods/40 world - terralith.jar",
        "mods/50 mobs - friends.jar",
        "mods/_README.txt",
        "mods/fabric-api.jar",
    ]
    names = entries(path)
    assert "CustomSkinLoader/LocalSkin/skins/me.png" in names
    assert "CustomSkinLoader/Other.txt" not in names
    assert {"config/mod.json", "defaultconfigs/d.toml", "options.txt", "servers.dat"} <= set(names)
    assert "resourcepacks/pack.zip" in names and "shaderpacks/s.zip" in names


def test_include_solo_adds_6x(game, tmp_path):
    populate(game)
    path = run(game, tmp_path, include_solo=True)
    assert path.name.startswith("modpack-all+solo-")
    mods = mod_entries(path)
    assert "mods/62 rpg - off.jar.disabled" in mods
    assert "mods/60 rpg - spell engine.jar" in mods
    assert len(mods) == 8


def test_mods_only_excludes_6x(game, tmp_path):
    populate(game)
    path = run(game, tmp_path, {"mods", "shaders"})
    assert path.name.startswith("modpack-mods+shaders-")
    assert not any(n.startswith("mods/6") for n in entries(path))
    assert all(n.startswith(("mods/", "shaderpacks/")) for n in entries(path))
    solo = run(game, tmp_path, {"mods"}, include_solo=True)
    assert solo.name.startswith("modpack-mods+solo-")


def test_running_blocks_config_parts_only(game, tmp_path):
    populate(game)
    for part in ("config", "options", "servers"):
        with pytest.raises(GameRunningError):
            run(game, tmp_path, {part}, running=busy)
    with pytest.raises(GameRunningError):
        run(game, tmp_path, running=busy)
    assert not (tmp_path / "out").exists()
    assert mod_entries(run(game, tmp_path, {"mods"}, running=busy))
    assert run(game, tmp_path, {"skins", "resourcepacks", "shaders"}, running=busy).exists()


def test_running_check_failure_propagates(game, tmp_path):
    populate(game)

    def broken():
        raise OSError("query failed")

    with pytest.raises(OSError):
        run(game, tmp_path, {"config"}, running=broken)


def test_app_part_ships_exe(game, tmp_path):
    populate(game)
    exe = tmp_path / "built" / "minecraft-setup.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    path = run(game, tmp_path, {"app"}, app_exe=exe)
    assert path.name.startswith("modpack-app-")
    assert entries(path) == ["minecraft-setup.exe"]
    assert "minecraft-setup.exe" in entries(run(game, tmp_path, app_exe=exe))
    with pytest.raises(ValueError, match="app"):
        run(game, tmp_path, {"app"}, app_exe=tmp_path / "missing.exe")
    with pytest.raises(ValueError, match="app"):
        run(game, tmp_path, {"app"})
    assert "minecraft-setup.exe" not in entries(run(game, tmp_path))
    assert "minecraft-setup.exe" not in entries(run(game, tmp_path, app_exe=tmp_path / "missing.exe"))


def test_never_packs_deny_list(game, tmp_path):
    populate(game)
    config = game.root / "config"
    for name in ("usercache.json", "TlauncherProfiles.json", "launcher_profiles.json"):
        (config / name).write_text("x")
    for folder in ("logs", "crash-reports", "xaero", "saves-full-profile"):
        (config / folder).mkdir()
        (config / folder / "f.txt").write_text("x")
    (config / "stray.jar").write_text("x")
    (game.root / "shaderpacks" / "saves").mkdir()
    (game.root / "shaderpacks" / "saves" / "f.txt").write_text("x")
    names = entries(run(game, tmp_path, include_solo=True))
    assert "config/mod.json" in names
    assert not [n for n in names if re.search(r"usercache|Profiles\.json|logs|crash-reports|xaero|saves", n)]
    assert "config/stray.jar" not in names
    assert "mods/fabric-api.jar" in names


def test_parts_constant():
    assert PARTS == ("mods", "config", "resourcepacks", "shaders", "options", "servers", "skins", "app")


def test_unknown_part_rejected(game, tmp_path):
    with pytest.raises(ValueError, match="nope"):
        run(game, tmp_path, {"nope"})


def test_bracket_names_survive(game, tmp_path):
    make_jar(game, "40 world [x] - a.jar")
    assert mod_entries(run(game, tmp_path, {"mods"})) == ["mods/40 world [x] - a.jar"]
    assert isinstance(Path(run(game, tmp_path, {"mods"})), Path)

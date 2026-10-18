import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from helpers import BEFORE, CLIENT, T0, files, make_jar, make_world, pack
from minecraft_setup import cli
from minecraft_setup.engine.mods import Profile, detect, list_mods


@pytest.fixture(autouse=True)
def game_stopped(monkeypatch):
    monkeypatch.setattr(cli, "is_minecraft_running", lambda: False)


def run(game, *args):
    return cli.main([args[0], "--game-dir", str(game.root), *args[1:]])


def test_status_prints_profile_and_worlds(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "Main", marker="full")
    make_world(game, "S Darinkoj", marker="solo", where=game.vault(Profile.SOLO))
    assert run(game, "status") == 0
    out = capsys.readouterr().out
    assert "Current profile: full (2/4 managed mods enabled)" in out
    assert "visible  full     Main" in out
    assert "hidden   solo     S Darinkoj" in out


def test_profile_switch_exit_codes(game, capsys):
    pack(game, Profile.FULL)
    world = make_world(game, "W", marker="full")
    assert run(game, "profile", "solo") == 0
    assert "Switched to 'solo'." in capsys.readouterr().out
    assert detect(list_mods(game)) is Profile.SOLO
    assert not world.exists()
    assert run(game, "profile", "vanilla") == 0
    assert detect(list_mods(game)) is Profile.VANILLA
    with pytest.raises(SystemExit) as bad:
        run(game, "profile", "nope")
    assert bad.value.code == 2


def test_guard_error_exit_1(game, capsys):
    pack(game, Profile.FULL, solo=False)
    before = files(game)
    assert run(game, "profile", "solo") == 1
    assert "no solo mods" in capsys.readouterr().out
    assert files(game) == before


def test_running_game_exit_1(game, capsys, monkeypatch):
    pack(game, Profile.FULL)
    monkeypatch.setattr(cli, "is_minecraft_running", lambda: True)
    assert run(game, "profile", "vanilla") == 1
    assert "Minecraft is running" in capsys.readouterr().out


def test_failed_running_check_fails_closed(game, capsys, monkeypatch):
    pack(game, Profile.FULL)
    before = files(game)

    def broken():
        raise subprocess.CalledProcessError(1, "powershell.exe")

    monkeypatch.setattr(cli, "is_minecraft_running", broken)
    assert run(game, "profile", "vanilla") == 1
    assert "Cannot tell whether Minecraft is running" in capsys.readouterr().out
    assert files(game) == before
    assert run(game, "status") == 0


def test_assign_tags_worlds(game, capsys):
    pack(game, Profile.FULL)
    world = make_world(game, "W", opened_at=BEFORE)
    assert run(game, "assign", "full", "W") == 0
    assert "tag  world 'W' -> full" in capsys.readouterr().out
    assert (world / ".full-profile").is_file()
    assert run(game, "assign", "full", "Missing") == 1
    assert "not found" in capsys.readouterr().out


def test_export_command(game, tmp_path, capsys):
    pack(game, Profile.FULL)
    (game.root / "options.txt").write_text("o")
    repo = tmp_path / "repo"
    assert cli.main(["export", "--game-dir", str(game.root), "--repo", str(repo), "--parts", "mods", "options"]) == 0
    out = capsys.readouterr().out
    assert "Exported [mods, options]: 5 files" in out
    [zip_path] = (game.root / "exports").glob("modpack-mods+options-*.zip")
    with zipfile.ZipFile(zip_path) as archive:
        assert "options.txt" in archive.namelist()
        assert not any(n.startswith("mods/6") for n in archive.namelist())


def test_export_solo_flag_and_missing_app(game, tmp_path, capsys):
    pack(game, Profile.FULL)
    repo = tmp_path / "repo"
    assert cli.main(["export", "--game-dir", str(game.root), "--repo", str(repo), "--parts", "mods", "--solo"]) == 0
    assert list((game.root / "exports").glob("modpack-mods+solo-*.zip"))
    capsys.readouterr()
    assert cli.main(["export", "--game-dir", str(game.root), "--repo", str(repo), "--parts", "app"]) == 1
    assert "app" in capsys.readouterr().out


def test_export_blocked_while_running(game, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "is_minecraft_running", lambda: True)
    assert cli.main(["export", "--game-dir", str(game.root), "--repo", str(tmp_path), "--parts", "config"]) == 1
    assert "Minecraft is running" in capsys.readouterr().out


def test_game_dir_from_env(game, capsys, monkeypatch):
    make_jar(game, CLIENT[0])
    monkeypatch.setenv("MINECRAFT_DIR", str(game.root))
    assert cli.main(["status"]) == 0
    assert "Current profile: vanilla" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_install_shortcut_creates_lnk(tmp_path, capsys):
    menu = tmp_path / "Programs"
    assert cli.main(["install-shortcut", "--start-menu", str(menu)]) == 0
    lnk = menu / "Minecraft Setup.lnk"
    assert lnk.is_file()
    read = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK) | ForEach-Object { $_.TargetPath; $_.WindowStyle }"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", read],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LNK": str(lnk)},
    )
    target, style = result.stdout.splitlines()
    assert Path(target) == Path(sys.executable).parent / "minecraft-setup-gui.exe"
    assert style == "7"

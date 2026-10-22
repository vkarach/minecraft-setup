import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from helpers import AFTER, BEFORE, CLIENT, files, make_jar, make_world, pack
from minecraft_setup import cli
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.profiles import SwitchInterrupted, SwitchReport


@pytest.fixture(autouse=True)
def game_stopped(monkeypatch):
    monkeypatch.setattr(cli, "running_in", lambda game_dir: False)


def run(game, *args):
    return cli.main([args[0], "--game-dir", str(game.root), *args[1:]])


def test_status_prints_profile_and_worlds(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "Main", marker="full")
    make_world(game, "Hardcore", marker="vanilla", where=game.vault(Profile.VANILLA))
    assert run(game, "status") == 0
    out = capsys.readouterr().out
    assert "Current profile: full (2/2 managed mods enabled)" in out
    assert "visible  full     Main" in out
    assert "hidden   vanilla  Hardcore" in out


def test_profile_switch_exit_codes(game, capsys):
    pack(game, Profile.FULL)
    world = make_world(game, "W", marker="full")
    assert run(game, "profile", "vanilla") == 0
    assert "Switched to 'vanilla'." in capsys.readouterr().out
    assert detect(list_mods(game)) is Profile.VANILLA
    assert not world.exists()
    assert run(game, "profile", "full") == 0
    assert detect(list_mods(game)) is Profile.FULL
    with pytest.raises(SystemExit) as bad:
        run(game, "profile", "nope")
    assert bad.value.code == 2


def test_running_game_exit_1(game, capsys, monkeypatch):
    pack(game, Profile.FULL)
    monkeypatch.setattr(cli, "running_in", lambda game_dir: True)
    assert run(game, "profile", "vanilla") == 1
    assert "Minecraft is running" in capsys.readouterr().out


def test_failed_running_check_fails_closed(game, capsys, monkeypatch):
    pack(game, Profile.FULL)
    before = files(game)

    def broken(game_dir):
        raise subprocess.CalledProcessError(1, "powershell.exe")

    monkeypatch.setattr(cli, "running_in", broken)
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


def test_game_dir_from_env(game, capsys, monkeypatch):
    make_jar(game, CLIENT[0])
    monkeypatch.setenv("MINECRAFT_DIR", str(game.root))
    assert cli.main(["status"]) == 0
    assert "Current profile: vanilla" in capsys.readouterr().out


def fake_scripts(tmp_path, monkeypatch, exists=True):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    if exists:
        (scripts / "minecraft-setup-gui.exe").write_bytes(b"MZ")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(scripts))
    return scripts / "minecraft-setup-gui.exe"


def shortcut_props(lnk):
    read = "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath; $s.WindowStyle"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", read],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LNK": str(lnk)},
    )
    return result.stdout.splitlines()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_install_shortcut_creates_lnk(tmp_path, monkeypatch):
    exe = fake_scripts(tmp_path, monkeypatch)
    menu = tmp_path / "Programs"
    assert cli.main(["install-shortcut", "--start-menu", str(menu)]) == 0
    target, style = shortcut_props(menu / "Minecraft Setup.lnk")
    assert Path(target) == exe
    assert style == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_install_shortcut_frozen_targets_running_exe(tmp_path, monkeypatch):
    fake_scripts(tmp_path, monkeypatch, exists=False)
    app = tmp_path / "friend" / "minecraft-setup.exe"
    app.parent.mkdir()
    app.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app))
    menu = tmp_path / "Programs"
    assert cli.main(["install-shortcut", "--start-menu", str(menu)]) == 0
    assert Path(shortcut_props(menu / "Minecraft Setup.lnk")[0]) == app


def test_install_shortcut_refuses_missing_target(tmp_path, monkeypatch, capsys):
    fake_scripts(tmp_path, monkeypatch, exists=False)
    menu = tmp_path / "Programs"
    assert cli.main(["install-shortcut", "--start-menu", str(menu)]) == 1
    assert "minecraft-setup-gui.exe" in capsys.readouterr().out
    assert not menu.exists()


def test_profile_wording_header_tag_and_hide(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "W", opened_at=AFTER)
    assert run(game, "profile", "vanilla") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Current profile: full (2/2 managed mods enabled)"
    assert "  tag  world 'W' -> full" in out
    assert "  hid  world 'W' (full)" in out
    assert "  off  40 world - terralith.jar" in out
    assert out[-1] == "Switched to 'vanilla'."


def test_profile_already_on_target(game, capsys):
    pack(game, Profile.FULL)
    assert run(game, "profile", "full") == 0
    out = capsys.readouterr().out
    assert "Already on 'full', nothing else to do." in out
    assert "Switched" not in out


def test_profile_back_and_kept_lines(game, capsys):
    pack(game, Profile.FULL)
    vault = game.vault(Profile.FULL)
    make_world(game, "R", marker="full", where=vault)
    make_world(game, "K", marker="full", where=vault)
    make_world(game, "K", marker="full")
    assert run(game, "profile", "full") == 0
    out = capsys.readouterr().out
    assert "  back world 'R'" in out
    assert "  kept world 'K' in saves-full-profile: a world with this name exists in saves" in out


def test_profile_prints_relocated_worlds(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "X", marker="vanilla", where=game.vault(Profile.FULL))
    assert run(game, "profile", "full") == 0
    assert "  moved world 'X' to saves-vanilla-profile" in capsys.readouterr().out
    assert (game.vault(Profile.VANILLA) / "X").is_dir()


def test_interrupted_switch_reports_progress(game, capsys, monkeypatch):
    pack(game, Profile.FULL)
    make_world(game, "A", marker="full", where=game.vault(Profile.FULL))
    report = SwitchReport(hidden=["A"], disabled=["40 world - terralith.jar"])

    def interrupted(*args, **kwargs):
        raise SwitchInterrupted(report, OSError("access denied"))

    monkeypatch.setattr(cli, "switch", interrupted)
    assert run(game, "profile", "vanilla") == 1
    out = capsys.readouterr().out
    assert "  hid  world 'A' (full)" in out
    assert "  off  40 world - terralith.jar" in out
    assert "Stopped part-way" in out and "access denied" in out
    assert "Switched" not in out


def test_assign_wording(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "W", marker="full")
    make_world(game, "N", opened_at=BEFORE)
    assert run(game, "assign", "full", "w", "N") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Current profile: full (2/2 managed mods enabled)"
    assert "  'w' already belongs to full" in out
    assert "  tag  world 'N' -> full" in out

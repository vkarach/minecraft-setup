import os
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

from helpers import AFTER, BEFORE, CLIENT, T0, empty_game, files, make_jar, make_pack, make_world, pack, put
from minecraft_setup import cli
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.profiles import SwitchInterrupted, SwitchReport
from minecraft_setup.engine.sync import RestoreInterrupted, RestoreReport


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


def make_repo(folder, name="minecraft-setup", exe=True):
    (folder / "dist").mkdir(parents=True)
    (folder / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    if exe:
        (folder / "dist" / "minecraft-setup.exe").write_bytes(b"MZ")
    return folder


def export_app(game, *extra):
    return cli.main(["export", "--game-dir", str(game.root), "--parts", "app", *extra])


def zip_names(game):
    [path] = (game.root / "exports").glob("*.zip")
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def test_repo_option_beats_env(game, tmp_path, monkeypatch):
    monkeypatch.setenv("MINECRAFT_SETUP_REPO", str(make_repo(tmp_path / "env", exe=False)))
    monkeypatch.setattr(cli, "PACKAGE_ROOT", tmp_path / "nowhere")
    repo = make_repo(tmp_path / "opt")
    assert export_app(game, "--repo", str(repo)) == 0
    assert zip_names(game) == ["minecraft-setup.exe"]


def test_repo_from_env(game, tmp_path, monkeypatch):
    monkeypatch.setenv("MINECRAFT_SETUP_REPO", str(make_repo(tmp_path / "env")))
    monkeypatch.setattr(cli, "PACKAGE_ROOT", tmp_path / "nowhere")
    assert export_app(game) == 0


def test_repo_from_package_root_when_it_is_the_project(game, tmp_path, monkeypatch):
    monkeypatch.delenv("MINECRAFT_SETUP_REPO", raising=False)
    monkeypatch.setattr(cli, "PACKAGE_ROOT", make_repo(tmp_path / "pkg"))
    assert export_app(game) == 0


def test_repo_unresolved_refuses(game, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINECRAFT_SETUP_REPO", raising=False)
    monkeypatch.setattr(cli, "PACKAGE_ROOT", make_repo(tmp_path / "other", name="something-else"))
    assert export_app(game) == 1
    assert "--repo" in capsys.readouterr().out
    assert not (game.root / "exports").exists()
    assert cli.main(["export", "--game-dir", str(game.root), "--parts", "mods"]) == 0


def test_export_unknown_exe_is_guard_error(game, tmp_path, capsys):
    repo = make_repo(tmp_path / "repo", exe=False)
    assert export_app(game, "--repo", str(repo)) == 1
    assert "app exe" in capsys.readouterr().out


def test_profile_wording_header_tag_and_hide(game, capsys):
    pack(game, Profile.FULL)
    make_world(game, "W", opened_at=AFTER)
    assert run(game, "profile", "vanilla") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Current profile: full (2/4 managed mods enabled)"
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
    make_world(game, "X", marker="solo", where=game.vault(Profile.FULL))
    assert run(game, "profile", "full") == 0
    assert "  moved world 'X' to saves-solo-profile" in capsys.readouterr().out
    assert (game.vault(Profile.SOLO) / "X").is_dir()


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
    assert out[0] == "Current profile: full (2/4 managed mods enabled)"
    assert "  'w' already belongs to full" in out
    assert "  tag  world 'N' -> full" in out


def test_export_all_says_when_app_is_left_out(game, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MINECRAFT_SETUP_REPO", raising=False)
    monkeypatch.setattr(cli, "PACKAGE_ROOT", tmp_path / "nowhere")
    assert cli.main(["export", "--game-dir", str(game.root)]) == 0
    out = capsys.readouterr().out
    assert "App exe not included" in out and "--repo" in out
    assert cli.main(["export", "--game-dir", str(game.root), "--parts", "mods"]) == 0
    assert "App exe" not in capsys.readouterr().out
    repo = make_repo(tmp_path / "repo")
    assert cli.main(["export", "--game-dir", str(game.root), "--repo", str(repo)]) == 0
    assert "App exe" not in capsys.readouterr().out


@pytest.fixture
def offline(monkeypatch, modrinth):
    monkeypatch.setattr(cli, "ModrinthClient", lambda: modrinth)
    return modrinth


def cli_sync(game, repo):
    return cli.main(["sync", "--game-dir", str(game.root), "--repo", str(repo)])


def cli_restore(game, repo, profile, *extra):
    return cli.main(["restore", profile, "--game-dir", str(game.root), "--repo", str(repo), *extra])


def test_sync_command(game, tmp_path, offline, capsys):
    make_pack(game, offline)
    repo = tmp_path / "repo"
    assert cli_sync(game, repo) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Locked 7 entries (1 local) -> {repo / 'pack' / 'lock.json'}"
    assert "  local mods/fabric-api.jar - not on Modrinth, restore cannot download it" in out
    assert "  copy options.txt" in out
    assert out[-1] == f"Config set: 7 files in {repo / 'pack' / 'files'} (7 copied, 0 removed)."
    (game.root / "options.txt").unlink()
    assert cli_sync(game, repo) == 0
    assert "  del  options.txt" in capsys.readouterr().out


def test_sync_needs_repo(game, tmp_path, offline, capsys, monkeypatch):
    monkeypatch.delenv("MINECRAFT_SETUP_REPO", raising=False)
    monkeypatch.setattr(cli, "PACKAGE_ROOT", tmp_path / "nowhere")
    assert cli.main(["sync", "--game-dir", str(game.root)]) == 1
    assert "--repo" in capsys.readouterr().out
    assert cli.main(["restore", "full", "--game-dir", str(game.root)]) == 1
    assert "--repo" in capsys.readouterr().out


def test_sync_blocked_while_running(game, tmp_path, offline, capsys, monkeypatch):
    make_pack(game, offline)
    monkeypatch.setattr(cli, "is_minecraft_running", lambda: True)
    assert cli_sync(game, tmp_path / "repo") == 1
    assert "Minecraft is running" in capsys.readouterr().out
    assert not (tmp_path / "repo").exists()


def test_restore_command(game, tmp_path, offline, capsys):
    make_pack(game, offline)
    repo = tmp_path / "repo"
    assert cli_sync(game, repo) == 0
    target = empty_game(tmp_path)
    put(target.mods, "30 extra - mine.jar", b"mine")
    capsys.readouterr()
    assert cli_restore(target, repo, "full") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Current profile: vanilla (0/0 managed mods enabled)"
    assert "  get  40 world - terralith.jar" in out
    assert "  lost mods/fabric-api.jar - not on Modrinth, not restorable: copy it in by hand" in out
    assert "  extra mods/30 extra - mine.jar - not in the lock, left alone (--prune moves it to backup)" in out
    assert "  cfg  options.txt" in out
    assert "  on   40 world - terralith.jar" in out
    assert out[-1] == "Restored onto 'full'."
    assert detect(list_mods(target)) is Profile.FULL
    assert cli_restore(target, repo, "full", "--prune") == 0
    out = capsys.readouterr().out
    assert "  prune mods/30 extra - mine.jar" in out
    assert "  backup " in out


def test_restore_hash_mismatch_exit_1(game, tmp_path, offline, capsys):
    make_pack(game, offline)
    repo = tmp_path / "repo"
    assert cli_sync(game, repo) == 0
    for url in offline.served:
        offline.served[url] = b"evil"
    target = empty_game(tmp_path)
    capsys.readouterr()
    assert cli_restore(target, repo, "full") == 1
    assert "sha512" in capsys.readouterr().out
    assert not target.mods.exists()


def test_restore_interrupted_reports_progress(game, tmp_path, offline, capsys, monkeypatch):
    partial = RestoreReport(downloaded=["40 world - terralith.jar"], configs=["options.txt"], leftovers=[".restore-x"])
    partial.switch = SwitchReport(hidden=["A"])

    def interrupted(*args, **kwargs):
        raise RestoreInterrupted(partial, OSError("access denied"))

    monkeypatch.setattr(cli, "restore", interrupted)
    assert cli_restore(game, tmp_path / "repo", "vanilla") == 1
    out = capsys.readouterr().out
    assert "  get  40 world - terralith.jar" in out
    assert "  cfg  options.txt" in out
    assert "  left .restore-x - a download folder that could not be removed, delete it by hand" in out
    assert "  hid  world 'A'" in out
    assert "some changes were already made" in out and "access denied" in out
    assert "Restored" not in out

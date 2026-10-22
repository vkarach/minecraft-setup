import json
import subprocess

import pytest

from helpers import (
    AFTER,
    BEFORE,
    FakeModrinth,
    files,
    make_world,
    neoforge_install,
    fabric_install,
    pack,
    served_packs,
)
from manifests import entry, info, manifest
from minecraft_setup import cli
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.profiles import SwitchInterrupted, SwitchReport
from minecraft_setup.engine.selfupdate import RELEASES

A = entry("20 visual - a.jar", data=b"a")
OFF = entry("30 ui - off.jar", default=False, data=b"off")
FABRIC = manifest(A, OFF)
SOLO = manifest(pack=info(id="solo", title="Solo", game_version="1.21.1", loader="neoforge",
                          loader_version="21.1.264", profiles=False))


@pytest.fixture
def http():
    return served_packs(FABRIC, SOLO, **{A.url: b"a", OFF.url: b"off"})


@pytest.fixture(autouse=True)
def wired(game, tmp_path, monkeypatch, http):
    fabric_install(game.root)
    monkeypatch.setattr(cli, "running_in", lambda game_dir: False)
    monkeypatch.setattr(cli, "services", lambda: cli.Services(http, FakeModrinth(), tmp_path / "cache", None))


def run(game, *args):
    return cli.main([*args, "--minecraft", str(game.root)])


def test_packs_lists_missing_install(game, capsys):
    assert run(game, "packs") == 0
    out = capsys.readouterr().out
    assert "fabric-26.3  Fabric 26.3 -> Fabric 26.3" in out
    assert "solo  Solo -> not installed: install NeoForge 21.1.264 for Minecraft 1.21.1" in out


def test_packs_offline_uses_the_last_copy(game, capsys, http):
    assert run(game, "packs") == 0
    http.fail.update(http.served)
    assert run(game, "packs") == 0
    out = capsys.readouterr().out
    assert "Offline" in out and "fabric-26.3" in out


def test_update_without_yes_changes_nothing(game, capsys, http):
    assert run(game, "update", "fabric-26.3") == 0
    out = capsys.readouterr().out
    assert f"  add  {A.name}" in out and "--yes" in out
    assert files(game) == []
    assert A.url not in http.requests
    assert run(game, "update", "fabric-26.3", "--yes") == 0
    assert files(game) == [A.name, OFF.name + ".disabled"]


def test_choose_turns_a_mod_on(game, capsys):
    assert run(game, "update", "fabric-26.3", "--yes") == 0
    assert run(game, "choose", "fabric-26.3", "--on", OFF.name, "--off", A.name) == 0
    assert files(game) == [A.name + ".disabled", OFF.name]


def test_choose_lists_choices(game, capsys):
    assert run(game, "choose", "fabric-26.3") == 0
    out = capsys.readouterr().out
    assert f"[x] {A.name}" in out and f"[ ] {OFF.name}" in out


def test_choose_unknown_name_errors(game, capsys):
    assert run(game, "choose", "fabric-26.3", "--on", "nope.jar") == 1
    assert "nope.jar" in capsys.readouterr().out


def test_unknown_pack_errors(game, capsys):
    assert run(game, "update", "nope") == 1
    assert "nope" in capsys.readouterr().out


def test_profile_refused_on_pack_without_profiles(game, capsys):
    neoforge_install(game.root)
    assert run(game, "profile", "vanilla", "--pack", "solo") == 1
    assert "no profiles" in capsys.readouterr().out


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


def test_publish_without_repo_errors(game, capsys):
    assert run(game, "publish", "fabric-26.3") == 1
    assert "clone" in capsys.readouterr().out


def test_self_update_up_to_date(game, capsys, http):
    http.served[RELEASES] = json.dumps({"tag_name": "v0.0.1", "assets": []}).encode()
    assert run(game, "self-update") == 0
    assert "up to date" in capsys.readouterr().out.lower()

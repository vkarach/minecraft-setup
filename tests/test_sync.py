from pathlib import Path

import pytest

from helpers import AFTER, PACK_JARS, T0, busy, clock, empty_game, files, idle, make_pack, make_world, names, put
from minecraft_setup.engine import sync as sync_module
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.lock import Lock
from minecraft_setup.engine.modrinth import HashMismatch
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError, SwitchInterrupted, SwitchReport
from minecraft_setup.engine.state import write_since
from minecraft_setup.engine.sync import RestoreInterrupted, restore, sync


def synced(tmp_path: Path, game: GameDir, modrinth) -> Path:
    make_pack(game, modrinth)
    repo = tmp_path / "repo"
    sync(game, repo, modrinth, running=idle)
    return repo


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


CONFIG_SET = [
    "CustomSkinLoader/CustomSkinLoader.json",
    "config/[cfg]/a.json",
    "config/mod.json",
    "defaultconfigs/d.toml",
    "options.txt",
    "servers.dat",
    "shaderpacks/Comp.zip.txt",
]


def test_sync_mirrors_config_set(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    assert tree(repo / "pack" / "files") == CONFIG_SET
    assert (repo / "pack" / "files" / "options.txt").read_text() == "fov:90"
    lock = Lock.load(repo / "pack" / "lock.json")
    assert len(lock.entries) == 7
    assert [e.name for e in lock.entries if e.source == "local"] == ["fabric-api.jar"]

    (game.root / "config" / "mod.json").unlink()
    (game.root / "options.txt").write_text("fov:70")
    put(game.root, "config/new.json", "{}")
    report = sync(game, repo, modrinth, running=idle)
    assert tree(repo / "pack" / "files") == sorted(set(CONFIG_SET) - {"config/mod.json"} | {"config/new.json"})
    assert (repo / "pack" / "files" / "options.txt").read_text() == "fov:70"
    assert report.removed == ["config/mod.json"]
    assert sorted(report.copied) == ["config/new.json", "options.txt"]
    assert report.local == ["mods/fabric-api.jar"]


def test_sync_never_copies_deny_list(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    for name in ("usercache.json", "TlauncherProfiles.json", "launcher_profiles.json"):
        put(game.root, name, "secret")
        put(game.root, f"config/{name.upper()}", "secret")
    for folder in ("logs", "crash-reports", "xaero", "saves", "saves-full-profile"):
        put(game.root, f"{folder}/f.txt", "x")
    put(game.root, "config/stray.jar", "x")
    repo = tmp_path / "repo"
    put(repo, "pack/files/usercache.json", "planted")
    put(repo, "pack/files/logs/latest.log", "planted")
    sync(game, repo, modrinth, running=idle)
    assert tree(repo / "pack" / "files") == CONFIG_SET
    assert tree(repo / "pack") == sorted(["lock.json"] + [f"files/{p}" for p in CONFIG_SET])


def test_sync_refused_while_running(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    repo = tmp_path / "repo"
    with pytest.raises(GameRunningError):
        sync(game, repo, modrinth, running=busy)
    assert not repo.exists()
    assert modrinth.lookups == []


def run_restore(repo, target, modrinth, profile=Profile.FULL, *, prune=False, running=idle):
    return restore(repo, target, profile, modrinth, prune=prune, running=running, now=clock)


def test_restore_downloads_and_verifies(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    report = run_restore(repo, target, modrinth)
    assert sorted(report.downloaded) == sorted([*PACK_JARS, "Fresh.zip", "Comp.zip"])
    assert report.not_restorable == ["mods/fabric-api.jar"]
    assert files(target) == [
        "01 lib - cloth.jar",
        "40 world - terralith.jar",
        "50 mobs - friends.jar",
        "60 rpg - spell engine.jar.disabled",
    ]
    for name, data in PACK_JARS.items():
        found = target.mods / name if (target.mods / name).exists() else target.mods / (name + ".disabled")
        assert found.read_bytes() == data
    assert (target.root / "resourcepacks" / "Fresh.zip").read_bytes() == b"fresh"
    assert tree(target.root) == sorted(
        CONFIG_SET
        + [f"mods/{n}" for n in files(target)]
        + ["mods-profile.state.json", "resourcepacks/Fresh.zip", "shaderpacks/Comp.zip"]
    )
    assert detect(list_mods(target)) is Profile.FULL
    assert report.switch is not None and report.backup is None

    modrinth.downloads.clear()
    again = run_restore(repo, target, modrinth)
    assert again.downloaded == [] and modrinth.downloads == []


def test_restore_hash_mismatch_aborts_before_switch(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, "options.txt", "mine")
    lock = Lock.load(repo / "pack" / "lock.json")
    tampered = next(e for e in lock.entries if e.name == "50 mobs - friends.jar")
    modrinth.served[tampered.url] = b"evil"
    with pytest.raises(HashMismatch):
        run_restore(repo, target, modrinth)
    assert tree(target.root) == ["options.txt"]
    assert (target.root / "options.txt").read_text() == "mine"


def test_restore_runs_world_guard(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.mods, "40 world - terralith.jar", b"terralith")
    put(target.mods, "50 mobs - friends.jar", b"friends")
    write_since(target, T0)
    opened = make_world(target, "Opened", opened_at=AFTER)
    report = run_restore(repo, target, modrinth, Profile.VANILLA)
    assert report.switch.tagged == ["Opened"]
    assert report.switch.hidden == ["Opened"]
    assert not opened.exists()
    assert (target.vault(Profile.FULL) / "Opened" / ".full-profile").is_file()
    assert detect(list_mods(target)) is Profile.VANILLA


def test_restore_reports_extra_jars_without_prune(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.mods, "30 extra - mine.jar", b"mine")
    put(target.mods, "45 extra - off.jar.disabled", b"off")
    put(target.root, "resourcepacks/Other.zip", b"other")
    report = run_restore(repo, target, modrinth)
    assert report.extra == ["mods/30 extra - mine.jar", "mods/45 extra - off.jar.disabled", "resourcepacks/Other.zip"]
    assert report.pruned == []
    assert (target.mods / "30 extra - mine.jar").is_file()

    # the switch toggles extra managed jars like any other, it only never deletes them
    assert (target.mods / "45 extra - off.jar").is_file()
    pruned = run_restore(repo, target, modrinth, prune=True)
    assert pruned.pruned == pruned.extra == ["mods/30 extra - mine.jar", "mods/45 extra - off.jar", "resourcepacks/Other.zip"]
    assert not (target.mods / "30 extra - mine.jar").exists()
    assert not (target.root / "resourcepacks" / "Other.zip").exists()
    assert (pruned.backup / "mods" / "30 extra - mine.jar").read_bytes() == b"mine"


def test_restore_backs_up_replaced_configs(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, "options.txt", "fov:30")
    put(target.root, "config/mod.json", "{}")
    put(target.root, "config/own.json", "kept")
    put(target.mods, "40 world - terralith.jar", b"old build")
    report = run_restore(repo, target, modrinth)
    assert report.backup == target.root / "backup" / "restore-2026-10-02_12-00-00"
    assert tree(report.backup) == ["mods/40 world - terralith.jar", "options.txt"]
    assert (report.backup / "options.txt").read_text() == "fov:30"
    assert (target.root / "options.txt").read_text() == "fov:90"
    assert (target.root / "config" / "own.json").read_text() == "kept"
    assert report.replaced == ["40 world - terralith.jar"]
    assert (target.mods / "40 world - terralith.jar").read_bytes() == b"terralith"
    assert "options.txt" in report.configs and "config/mod.json" not in report.configs


def test_restore_refused_while_running(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    with pytest.raises(GameRunningError):
        run_restore(repo, target, modrinth, running=busy)
    assert tree(target.root) == []


def test_restore_refuses_unavailable_profile_before_downloading(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    lock_path = repo / "pack" / "lock.json"
    lock = Lock.load(lock_path)
    Lock(lock.game_version, lock.loader, [e for e in lock.entries if not e.name.startswith("6")]).save(lock_path)
    target = empty_game(tmp_path)
    with pytest.raises(GuardError, match="no solo mods"):
        run_restore(repo, target, modrinth, Profile.SOLO)
    assert modrinth.downloads == [] and tree(target.root) == []


def test_restore_without_lock_refuses(tmp_path):
    target = empty_game(tmp_path)
    with pytest.raises(GuardError, match="sync"):
        restore(tmp_path / "repo", target, Profile.FULL, None, prune=False, running=idle, now=clock)


def test_restore_skips_files_outside_config_set(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    put(repo, "pack/files/saves/World/level.dat", "planted")
    put(repo, "pack/files/mods/x.jar", "planted")
    target = empty_game(tmp_path)
    report = run_restore(repo, target, modrinth)
    assert report.skipped == ["mods/x.jar", "saves/World/level.dat"]
    assert names(target.root / "saves") == []
    assert not (target.mods / "x.jar").exists()


def test_restore_failure_after_downloads_reports_progress(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, "options.txt/blocker", "a folder where options.txt goes")
    with pytest.raises(GuardError, match="options.txt"):
        run_restore(repo, target, modrinth)
    assert modrinth.downloads == []

    (target.root / "options.txt" / "blocker").unlink()
    (target.root / "options.txt").rmdir()
    make_world(target, "W", marker="full")
    make_world(target, "W", marker="full", where=target.vault(Profile.FULL))
    with pytest.raises(RestoreInterrupted) as stopped:
        run_restore(repo, target, modrinth, Profile.VANILLA)
    report = stopped.value.report
    assert sorted(report.downloaded) == sorted([*PACK_JARS, "Fresh.zip", "Comp.zip"])
    assert "options.txt" in report.configs
    assert report.switch is None
    assert "some changes were already made" in str(stopped.value) and "already exists" in str(stopped.value)


def test_restore_keeps_partial_switch_report(tmp_path, game, modrinth, monkeypatch):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    partial = SwitchReport(hidden=["A"])

    def interrupted(*args, **kwargs):
        raise SwitchInterrupted(partial, OSError("access denied"))

    monkeypatch.setattr(sync_module, "switch", interrupted)
    with pytest.raises(RestoreInterrupted) as stopped:
        run_restore(repo, target, modrinth)
    assert stopped.value.report.switch is partial
    assert stopped.value.report.downloaded
    assert "access denied" in str(stopped.value)

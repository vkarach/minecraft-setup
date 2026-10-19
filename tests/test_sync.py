import subprocess
import sys
from pathlib import Path

import pytest

from helpers import AFTER, PACK_JARS, T0, busy, clock, empty_game, files, idle, make_pack, make_world, names, put
from minecraft_setup.engine import sync as sync_module
from minecraft_setup.engine.export import is_config
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
    assert report.extra == ["mods/30 extra - mine.jar", "mods/45 extra - off.jar", "resourcepacks/Other.zip"]
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


def test_restore_refusals_happen_before_any_change(tmp_path, game, modrinth):
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
    before = tree(target.root)
    with pytest.raises(GuardError, match="already exists"):
        run_restore(repo, target, modrinth, Profile.VANILLA)
    assert modrinth.downloads == [] and tree(target.root) == before

    put(target.root, "mods-profile.state.json", "not json")
    (target.vault(Profile.FULL) / "W" / "level.dat").unlink()
    with pytest.raises(GuardError, match="since"):
        run_restore(repo, target, modrinth, Profile.FULL)
    assert modrinth.downloads == []


def test_restore_failure_after_downloads_reports_progress(tmp_path, game, modrinth, monkeypatch):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    real = sync_module.shutil.copyfile

    def failing(src, dest, *args, **kwargs):
        if Path(dest).name == "servers.dat":
            raise OSError("disk full")
        return real(src, dest, *args, **kwargs)

    monkeypatch.setattr(sync_module.shutil, "copyfile", failing)
    with pytest.raises(RestoreInterrupted) as stopped:
        run_restore(repo, target, modrinth)
    report = stopped.value.report
    assert sorted(report.downloaded) == sorted([*PACK_JARS, "Fresh.zip", "Comp.zip"])
    assert "options.txt" in report.configs and "servers.dat" not in report.configs
    assert report.switch is None
    assert "some changes were already made" in str(stopped.value) and "disk full" in str(stopped.value)
    assert not [p for p in target.root.iterdir() if p.name.startswith(".restore-")]


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


def solo_target(tmp_path: Path, modrinth) -> tuple[Path, GameDir]:
    """A repo locking only 4x/5x jars, and a game dir on solo with an untagged world opened since."""
    src = empty_game(tmp_path / "src")
    for name, data in {"40 a.jar": b"a", "50 b.jar": b"b"}.items():
        modrinth.publish(data, name, project=name)
        put(src.mods, name, data)
    repo = tmp_path / "repo"
    sync(src, repo, modrinth, running=idle)
    target = empty_game(tmp_path / "dst")
    for name, data in {"40 a.jar": b"a", "50 b.jar": b"b", "60 solo.jar": b"solo"}.items():
        put(target.mods, name, data)
    write_since(target, T0)
    make_world(target, "SoloWorld", opened_at=AFTER)
    return repo, target


@pytest.mark.parametrize("profile", [Profile.FULL, Profile.VANILLA])
def test_restore_prunes_only_after_the_guard(tmp_path, modrinth, profile):
    repo, target = solo_target(tmp_path, modrinth)
    report = run_restore(repo, target, modrinth, profile, prune=True)
    assert report.switch.tagged == ["SoloWorld"]
    assert (target.vault(Profile.SOLO) / "SoloWorld" / ".solo-profile").is_file()
    assert not (target.saves / "SoloWorld").exists()
    assert report.pruned == report.extra == ["mods/60 solo.jar"]
    assert (report.backup / "mods" / "60 solo.jar.disabled").is_file()
    assert files(target) == (["40 a.jar", "50 b.jar"] if profile is Profile.FULL else ["40 a.jar.disabled", "50 b.jar.disabled"])
    assert detect(list_mods(target)) is profile


def test_restore_refuses_solo_when_prune_drops_every_6x(tmp_path, modrinth):
    repo, target = solo_target(tmp_path, modrinth)
    before = tree(target.root)
    with pytest.raises(GuardError, match="no solo mods"):
        run_restore(repo, target, modrinth, Profile.SOLO, prune=True)
    assert tree(target.root) == before


def staging_dirs(game: GameDir) -> list[str]:
    return [p.name for p in game.root.iterdir() if p.name.startswith(".restore-")]


def test_restore_rechecks_running_after_downloads(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    with pytest.raises(GameRunningError):
        run_restore(repo, target, modrinth, running=lambda: bool(modrinth.downloads))
    assert modrinth.downloads
    assert tree(target.root) == [] and staging_dirs(target) == []


def test_restore_os_error_before_changes_is_guard_error(tmp_path, game, modrinth, monkeypatch):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)

    def unreadable(path):
        raise OSError("read error")

    monkeypatch.setattr(sync_module, "sha512_of", unreadable)
    with pytest.raises(GuardError, match="failed before changing anything: read error"):
        run_restore(repo, target, modrinth)
    assert modrinth.downloads and tree(target.root) == [] and staging_dirs(target) == []
    put(target.mods, "40 world - terralith.jar", b"terralith")
    modrinth.downloads.clear()
    with pytest.raises(GuardError, match="failed before changing anything: read error"):
        run_restore(repo, target, modrinth)
    assert modrinth.downloads == []


def test_restore_cleans_stale_staging(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, ".restore-abc123/0", b"left by a crash")
    run_restore(repo, target, modrinth)
    assert staging_dirs(target) == []


def test_sync_refuses_missing_mods_folder(tmp_path, game, modrinth):
    game.mods.rmdir()
    put(game.root, "options.txt", "o")
    with pytest.raises(GuardError, match="mods"):
        sync(game, tmp_path / "repo", modrinth, running=idle)
    assert not (tmp_path / "repo").exists()


def test_sync_config_set_is_spec_exact(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    for rel in (
        "resourcepacks/notes.txt",
        "resourcepacks/[pack] one/pack.mcmeta",
        "shaderpacks/sub/a.txt",
        "shaderpacks/Comp.zip.json",
        "CustomSkinLoader/LocalSkin/skins/me.png",
        "CustomSkinLoader/ExtraList/x.json",
        "config/modCache.json",
        "config/[cfg]/CACHE.bin",
        "defaultconfigs/cache",
        "config/Caches/a.json",
        "config/sub/webcache/b.json",
        "servers.dat_old",
    ):
        put(game.root, rel, "x")
    repo = tmp_path / "repo"
    sync(game, repo, modrinth, running=idle)
    assert tree(repo / "pack" / "files") == CONFIG_SET
    put(repo, "pack/files/resourcepacks/notes.txt", "planted")
    put(repo, "pack/files/CustomSkinLoader/LocalSkin/skins/me.png", "planted")
    report = run_restore(repo, empty_game(tmp_path), modrinth)
    assert report.skipped == ["CustomSkinLoader/LocalSkin/skins/me.png", "resourcepacks/notes.txt"]


def junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    link.parent.mkdir(parents=True, exist_ok=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode != 0 or not link.is_junction():
        pytest.skip("cannot create a junction here")


def test_mirror_refuses_links_and_never_deletes_through_them(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    outside = put(tmp_path, "outside/precious.txt", "keep me")
    repo = tmp_path / "repo"
    junction(repo / "pack" / "files" / "config" / "link", outside.parent)
    with pytest.raises(GuardError, match="link"):
        sync(game, repo, modrinth, running=idle)
    assert outside.read_text() == "keep me"
    with pytest.raises(GuardError, match="link"):
        run_restore(repo, empty_game(tmp_path), modrinth)
    assert outside.read_text() == "keep me"


def test_sync_does_not_follow_links_in_the_game(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    outside = put(tmp_path, "outside/secret.json", "{}")
    junction(game.root / "config" / "linked", outside.parent)
    repo = tmp_path / "repo"
    sync(game, repo, modrinth, running=idle)
    assert tree(repo / "pack" / "files") == CONFIG_SET


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("options.txt", True),
        ("OPTIONS.TXT", True),
        ("servers.dat", True),
        ("CustomSkinLoader/CustomSkinLoader.json", True),
        ("config/a.json", True),
        ("defaultconfigs/x/y.toml", True),
        ("shaderpacks/a.txt", True),
        ("options.txt/x", False),
        ("servers.dat/a/b", False),
        ("CustomSkinLoader/CustomSkinLoader.json/x", False),
        ("config", False),
        ("defaultconfigs", False),
        ("config/Caches/a.json", False),
        ("config/sub/webCACHE/b.json", False),
        ("shaderpacks/sub/a.txt", False),
    ],
)
def test_config_set_roots(rel, expected):
    assert is_config(Path(rel)) is expected


def test_sync_refuses_a_file_root_that_is_a_folder(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    (game.root / "options.txt").unlink()
    put(game.root, "options.txt/x", "x")
    with pytest.raises(GuardError, match="options.txt"):
        sync(game, tmp_path / "repo", modrinth, running=idle)
    assert not (tmp_path / "repo").exists()


def test_sync_skips_a_folder_root_that_is_a_file(tmp_path, game, modrinth):
    make_pack(game, modrinth)
    for path in sorted((game.root / "defaultconfigs").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (game.root / "defaultconfigs").rmdir()
    put(game.root, "defaultconfigs", "a file")
    repo = tmp_path / "repo"
    sync(game, repo, modrinth, running=idle)
    assert tree(repo / "pack" / "files") == [p for p in CONFIG_SET if not p.startswith("defaultconfigs")]


def test_restore_skips_repo_files_below_file_roots(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    (repo / "pack" / "files" / "options.txt").unlink()
    put(repo, "pack/files/options.txt/x", "planted")
    report = run_restore(repo, empty_game(tmp_path), modrinth)
    assert report.skipped == ["options.txt/x"]


def test_restore_cleans_stale_staging_only_after_planning(tmp_path, game, modrinth):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, ".restore-abc123/0", b"left by a crash")
    put(target.root, "options.txt/blocker", "x")
    with pytest.raises(GuardError):
        run_restore(repo, target, modrinth)
    assert staging_dirs(target) == [".restore-abc123"]


def test_restore_reports_staging_it_cannot_remove(tmp_path, game, modrinth, monkeypatch):
    repo = synced(tmp_path, game, modrinth)
    target = empty_game(tmp_path)
    put(target.root, ".restore-abc123/0", b"left by a crash")

    def stuck(path, *args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(sync_module.shutil, "rmtree", stuck)
    report = run_restore(repo, target, modrinth)
    assert ".restore-abc123" in report.leftovers
    assert detect(list_mods(target)) is Profile.FULL

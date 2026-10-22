import json

import pytest

from helpers import put, sha512
from minecraft_setup.engine.lock import Lock, LockEntry, build_lock
from minecraft_setup.engine.profiles import GuardError


def test_build_lock_marks_unknown_local(game, modrinth):
    cloth = modrinth.publish(b"cloth", "cloth-config-1.0.jar", project="cloth")
    spell = modrinth.publish(b"spell", "spell_engine-2.0.jar", project="spell")
    fresh = modrinth.publish(b"fresh", "FreshAnimations_v1.10.5.zip", project="fresh")
    put(game.mods, "01 lib - cloth.jar", b"cloth")
    put(game.mods, "60 rpg - spell engine.jar.disabled", b"spell")
    put(game.mods, "fabric-api.jar", b"unknown build")
    put(game.mods, "_README.txt", "readme")
    put(game.root, "resourcepacks/FreshAnimations_v1.10.5.zip", b"fresh")
    put(game.root, "shaderpacks/Comp.zip", b"own shader")
    put(game.root, "shaderpacks/Comp.zip.txt", "settings")

    lock = build_lock(game, modrinth)

    assert (lock.game_version, lock.loader) == ("26.3", "fabric")
    by_name = {e.name: e for e in lock.entries}
    assert sorted(by_name) == [
        "01 lib - cloth.jar",
        "60 rpg - spell engine.jar",
        "Comp.zip",
        "FreshAnimations_v1.10.5.zip",
        "fabric-api.jar",
    ]
    assert by_name["01 lib - cloth.jar"] == LockEntry(
        "01 lib - cloth.jar", "mods", "modrinth", "cloth", cloth.version_id, cloth.file_name, cloth.url, cloth.sha512
    )
    assert by_name["60 rpg - spell engine.jar"].version_id == spell.version_id
    assert by_name["FreshAnimations_v1.10.5.zip"].dir == "resourcepacks"
    assert by_name["FreshAnimations_v1.10.5.zip"].url == fresh.url
    local = by_name["fabric-api.jar"]
    assert local == LockEntry("fabric-api.jar", "mods", "local", None, None, "fabric-api.jar", None, sha512(b"unknown build"))
    assert by_name["Comp.zip"].source == "local" and by_name["Comp.zip"].dir == "shaderpacks"
    assert len(modrinth.lookups) == 1


def test_build_lock_refuses_jar_in_both_states(game, modrinth):
    put(game.mods, "40 a.jar", b"a")
    put(game.mods, "40 a.jar.disabled", b"a")
    with pytest.raises(GuardError, match="40 a.jar"):
        build_lock(game, modrinth)


def test_lock_roundtrip(tmp_path):
    lock = Lock(
        "26.3",
        "fabric",
        [
            LockEntry("40 world - t.jar", "mods", "modrinth", "P1", "V1", "t-1.jar", "https://cdn/t-1.jar", "aa"),
            LockEntry("fabric-api.jar", "mods", "local", None, None, "fabric-api.jar", None, "bb"),
        ],
    )
    path = tmp_path / "pack" / "lock.json"
    lock.save(path)
    assert Lock.load(path) == lock
    data = json.loads(path.read_text(encoding="ascii"))
    assert data["game_version"] == "26.3" and data["loader"] == "fabric"
    assert data["entries"][0] == {
        "name": "40 world - t.jar",
        "dir": "mods",
        "source": "modrinth",
        "project_id": "P1",
        "version_id": "V1",
        "file_name": "t-1.jar",
        "url": "https://cdn/t-1.jar",
        "sha512": "aa",
    }
    assert path.read_bytes().endswith(b"\n")
    assert [p.name for p in path.parent.iterdir()] == ["lock.json"]


@pytest.mark.parametrize(
    "name, folder",
    [("../evil.jar", "mods"), ("sub\\x.jar", "mods"), ("x.jar", "saves"), ("notes.txt", "mods"), ("pack.txt", "shaderpacks")],
)
def test_lock_load_rejects_unsafe_entries(tmp_path, name, folder):
    entry = {"name": name, "dir": folder, "source": "local", "project_id": None, "version_id": None,
             "file_name": name, "url": None, "sha512": "aa"}
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({"game_version": "26.3", "loader": "fabric", "entries": [entry]}))
    with pytest.raises(ValueError):
        Lock.load(path)


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("name", 5, "must be a string"),
        ("sha512", None, "must be a string"),
        ("url", 7, "must be a string or null"),
        ("name", "a<b.jar", "character"),
        ("name", 'a"b.jar', "character"),
        ("name", "a?.jar", "character"),
        ("name", "a\x01.jar", "character"),
        ("name", "CON.jar", "reserved"),
        ("name", "con.foo.jar", "reserved"),
        ("name", "LPT1.jar", "reserved"),
        ("name", "Com9.jar", "reserved"),
        ("name", "nul", "reserved"),
        ("name", "x.jar.", "trailing"),
        ("name", "x.jar ", "trailing"),
    ],
)
def test_lock_load_rejects_bad_fields(tmp_path, field, value, reason):
    entry = {"name": "40 a.jar", "dir": "mods", "source": "local", "project_id": None, "version_id": None,
             "file_name": "a.jar", "url": None, "sha512": "aa"}
    entry[field] = value
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({"game_version": "26.3", "loader": "fabric", "entries": [entry]}))
    with pytest.raises(ValueError, match=reason):
        Lock.load(path)


def test_lock_load_accepts_device_like_prefixes(tmp_path):
    entry = {"name": "40 console - a.jar", "dir": "mods", "source": "local", "project_id": None, "version_id": None,
             "file_name": "a.jar", "url": None, "sha512": "aa"}
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({"game_version": "26.3", "loader": "fabric", "entries": [entry, dict(entry, name="COM10.jar")]}))
    assert len(Lock.load(path).entries) == 2

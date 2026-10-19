import io
import json
import urllib.request

import pytest

from helpers import put, sha512
from minecraft_setup import __version__
from minecraft_setup.engine.lock import Lock, LockEntry, build_lock
from minecraft_setup.engine.modrinth import HashMismatch, ModrinthClient, ModrinthError, VersionFile
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


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def version_json(sha="ab" * 64):
    return {
        "id": "VID",
        "project_id": "PID",
        "version_number": "1.2.3",
        "files": [
            {
                "hashes": {"sha512": "ff" * 64},
                "url": "https://cdn.modrinth.com/x-sources.jar",
                "filename": "x-sources.jar",
                "primary": False,
            },
            {"hashes": {"sha512": sha}, "url": "https://cdn.modrinth.com/x.jar", "filename": "x.jar", "primary": True},
        ],
    }


def test_client_lookup_posts_hashes_with_user_agent(monkeypatch):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        return FakeResponse(json.dumps({"ab" * 64: version_json()}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    found = ModrinthClient().lookup_sha512(["ab" * 64, "cd" * 64])
    assert found == {"ab" * 64: VersionFile("PID", "VID", "x.jar", "https://cdn.modrinth.com/x.jar", "ab" * 64)}
    [request] = sent
    assert request.full_url == "https://api.modrinth.com/v2/version_files"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"hashes": ["ab" * 64, "cd" * 64], "algorithm": "sha512"}
    assert request.get_header("User-agent") == f"minecraft-setup/{__version__} (github.com/vkarach/minecraft-setup)"


def test_client_find_version_picks_primary_file(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        other = dict(version_json(), version_number="9.9", id="OTHER")
        return FakeResponse(json.dumps([other, version_json()]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    found = ModrinthClient().find_version("fabric-api", "1.2.3", "fabric", "26.3")
    assert found == VersionFile("PID", "VID", "x.jar", "https://cdn.modrinth.com/x.jar", "ab" * 64)
    assert seen[0].startswith("https://api.modrinth.com/v2/project/fabric-api/version?")
    assert "loaders=%5B%22fabric%22%5D" in seen[0] and "game_versions=%5B%2226.3%22%5D" in seen[0]
    assert ModrinthClient().find_version("fabric-api", "0.0", "fabric", "26.3") is None


def test_client_download_verifies_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"payload"))
    dest = tmp_path / "x.jar"
    ModrinthClient().download("https://cdn.modrinth.com/x.jar", dest, sha512(b"payload"))
    assert dest.read_bytes() == b"payload"
    bad = tmp_path / "y.jar"
    with pytest.raises(HashMismatch):
        ModrinthClient().download("https://cdn.modrinth.com/y.jar", bad, sha512(b"other"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.jar"]
    with pytest.raises(ModrinthError, match="https"):
        ModrinthClient().download("file:///c:/x.jar", tmp_path / "z.jar", sha512(b"payload"))


def test_client_wraps_network_errors(monkeypatch):
    def urlopen(request, timeout):
        raise OSError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(ModrinthError, match="no route"):
        ModrinthClient().lookup_sha512(["ab"])


@pytest.mark.network
def test_modrinth_lookup_live(tmp_path):
    client = ModrinthClient()
    found = client.find_version("fabric-api", "0.92.2+1.20.1", "fabric", "1.20.1")
    assert found is not None and found.file_name == "fabric-api-0.92.2+1.20.1.jar"
    assert client.lookup_sha512([found.sha512])[found.sha512] == found
    dest = tmp_path / found.file_name
    client.download(found.url, dest, found.sha512)
    assert sha512(dest.read_bytes()) == found.sha512

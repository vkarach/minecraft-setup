import io
import json
import urllib.request

import pytest

from helpers import sha512
from minecraft_setup import __version__
from minecraft_setup.engine.modrinth import HashMismatch, ModrinthClient, ModrinthError, VersionFile


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

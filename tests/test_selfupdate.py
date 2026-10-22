import hashlib
import json

import pytest

from helpers import FakeHttp
from minecraft_setup.engine.http import HttpError
from minecraft_setup.engine.selfupdate import RELEASES, Release, cleanup, latest_release, newer, stage, swap

EXE_URL = "https://github.com/vkarach/minecraft-setup/releases/download/v0.3.0/minecraft-setup.exe"
SHA_URL = EXE_URL + ".sha256"
NEW = b"new exe"


def release_json(*assets):
    return json.dumps({
        "tag_name": "v0.3.0",
        "assets": [{"name": url.rsplit("/", 1)[1], "browser_download_url": url} for url in assets],
    }).encode()


def test_newer_numeric():
    assert newer("0.2.0", "v0.10.0")
    assert not newer("0.2.0", "v0.2.0")
    assert not newer("0.3.0", "v0.2.9")
    assert not newer("0.2.0", "nightly")


def test_latest_release_reads_the_sha_asset():
    sha = hashlib.sha256(NEW).hexdigest()
    http = FakeHttp({RELEASES: release_json(EXE_URL, SHA_URL), SHA_URL: f"{sha}  minecraft-setup.exe\n".encode()})
    assert latest_release(http) == Release("v0.3.0", EXE_URL, sha)


def test_release_without_sha_asset_is_none():
    assert latest_release(FakeHttp({RELEASES: release_json(EXE_URL)})) is None


def test_stage_bad_hash_leaves_nothing(tmp_path):
    exe = tmp_path / "minecraft-setup.exe"
    exe.write_bytes(b"old exe")
    release = Release("v0.3.0", EXE_URL, hashlib.sha256(b"other").hexdigest())
    with pytest.raises(HttpError):
        stage(release, exe, FakeHttp({EXE_URL: NEW}))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["minecraft-setup.exe"]


def test_swap_and_cleanup(tmp_path):
    exe = tmp_path / "minecraft-setup.exe"
    exe.write_bytes(b"old exe")
    staged = stage(Release("v0.3.0", EXE_URL, hashlib.sha256(NEW).hexdigest()), exe, FakeHttp({EXE_URL: NEW}))
    assert staged == tmp_path / "minecraft-setup.exe.new"
    swap(exe)
    assert exe.read_bytes() == NEW
    assert (tmp_path / "minecraft-setup.exe.old").read_bytes() == b"old exe"
    assert not staged.exists()
    cleanup(exe)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["minecraft-setup.exe"]


def test_cleanup_without_old_is_quiet(tmp_path):
    cleanup(tmp_path / "minecraft-setup.exe")

import io
import json
import shutil
import subprocess
import zipfile

import pytest

from helpers import busy, idle, put
from manifests import info
from minecraft_setup.engine.installs import Install
from minecraft_setup.engine.manifest import Manifest
from minecraft_setup.engine.profiles import GameRunningError
from minecraft_setup.engine.publish import PublishError, build_manifest, find_repo, git_publish, publish_pack

SODIUM = "10 perf - sodium.jar"
IRIS = "11 perf - iris.jar"
WORLD = "40 world - terralith.jar"


def fabric_jar(mod_id: str, *depends: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps({"id": mod_id, "depends": {d: "*" for d in depends}}))
    return buffer.getvalue()


def install(game) -> Install:
    return Install("Fabric 26.3", game.root, "26.3", "fabric", "0.19.5")


def add(game, modrinth, rel, data, project, sides=("required", "optional")):
    put(game.root, rel, data)
    modrinth.publish(data, rel.rsplit("/", 1)[1].removesuffix(".disabled"), project, sides)


@pytest.fixture
def live(game, modrinth):
    add(game, modrinth, f"mods/{SODIUM}", fabric_jar("sodium"), "sodium")
    add(game, modrinth, f"mods/{IRIS}.disabled", fabric_jar("iris", "sodium"), "iris")
    add(game, modrinth, f"mods/{WORLD}", fabric_jar("terralith"), "terralith", ("optional", "required"))
    add(game, modrinth, "shaderpacks/Comp.zip", b"comp", "comp")
    put(game.root, "config/sodium.json", "{}")
    put(game.root, "options.txt", "fov:90")
    return game


def by_name(m: Manifest):
    return {e.name: e for e in m.entries}


def test_default_reflects_enabled_state(live, modrinth):
    entries = by_name(build_manifest(install(live), info(), modrinth, {}))
    assert entries[SODIUM].default and not entries[IRIS].default
    assert entries[IRIS].depends == ["sodium"] and entries[IRIS].ids == ["iris"]
    assert entries[WORLD].side == "required" and entries[SODIUM].side == "client"
    assert entries["Comp.zip"].dir == "shaderpacks" and entries["Comp.zip"].default


def test_override_wins(live, modrinth):
    entries = by_name(build_manifest(install(live), info(), modrinth, {SODIUM: "required"}))
    assert entries[SODIUM].side == "required"


def test_unmatched_jar_refuses_with_names(live, modrinth):
    put(live.root, "mods/30 ui - local.jar", fabric_jar("local"))
    put(live.root, "resourcepacks/Mine.zip", b"mine")
    with pytest.raises(PublishError, match="30 ui - local.jar") as caught:
        build_manifest(install(live), info(), modrinth, {})
    assert "Mine.zip" in str(caught.value)


def test_secret_config_refuses(live, modrinth):
    put(live.root, "config/auth.json", '{"accessToken": "abc"}')
    with pytest.raises(PublishError, match="config/auth.json"):
        build_manifest(install(live), info(), modrinth, {})


def test_other_version_refused(live, modrinth):
    with pytest.raises(PublishError, match="1.21.1"):
        build_manifest(install(live), info(game_version="1.21.1"), modrinth, {})


def test_publish_writes_manifest_and_configs(live, modrinth, tmp_path):
    repo = tmp_path / "repo"
    report = publish_pack(install(live), info(), repo, modrinth, running=idle)
    pack = repo / "packs" / "fabric-26.3"
    m = Manifest.load(pack / "manifest.json")
    assert sorted(c.path for c in m.configs) == ["config/sodium.json", "options.txt"]
    assert (pack / "files" / "options.txt").read_text() == "fov:90"
    assert (report.entries, report.configs, report.changed) == (4, 2, True)
    assert not publish_pack(install(live), info(), repo, modrinth, running=idle).changed


def test_stale_config_removed_from_repo(live, modrinth, tmp_path):
    repo = tmp_path / "repo"
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    (live.root / "config" / "sodium.json").unlink()
    report = publish_pack(install(live), info(), repo, modrinth, running=idle)
    assert report.changed
    assert not (repo / "packs" / "fabric-26.3" / "files" / "config").exists()


def test_overrides_file_is_read(live, modrinth, tmp_path):
    repo = tmp_path / "repo"
    put(repo, "packs/fabric-26.3/overrides.json", json.dumps({SODIUM: "required"}))
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    m = Manifest.load(repo / "packs" / "fabric-26.3" / "manifest.json")
    assert by_name(m)[SODIUM].side == "required"


def test_bad_override_refused(live, modrinth, tmp_path):
    repo = tmp_path / "repo"
    put(repo, "packs/fabric-26.3/overrides.json", json.dumps({SODIUM: "server"}))
    with pytest.raises(PublishError, match="overrides.json"):
        publish_pack(install(live), info(), repo, modrinth, running=idle)


def test_index_lists_pack_once(live, modrinth, tmp_path):
    repo = tmp_path / "repo"
    put(repo, "packs/index.json", json.dumps({"packs": ["solo"]}))
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    assert json.loads((repo / "packs" / "index.json").read_text()) == {"packs": ["solo", "fabric-26.3"]}


def test_running_game_refused(live, modrinth, tmp_path):
    with pytest.raises(GameRunningError):
        publish_pack(install(live), info(), tmp_path / "repo", modrinth, running=busy)
    assert not (tmp_path / "repo").exists()


class FakeRun:
    def __init__(self, staged: bool) -> None:
        self.staged = staged
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        code = 1 if argv[1:3] == ["diff", "--cached"] and self.staged else 0
        return subprocess.CompletedProcess(argv, code, "", "")


def test_git_publish_commands(tmp_path):
    run = FakeRun(staged=True)
    assert git_publish(tmp_path, "solo", run=run)
    assert run.calls == [
        ["git", "add", "--", "packs"],
        ["git", "add", "--renormalize", "--", "packs"],
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", "packs"],
        ["git", "diff", "--cached", "--quiet", "--", "packs"],
        ["git", "commit", "-m", "publish solo", "--", "packs"],
        ["git", "push"],
    ]


def test_git_publish_nothing_to_commit(tmp_path):
    run = FakeRun(staged=False)
    assert not git_publish(tmp_path, "solo", run=run)
    assert ["git", "push"] not in run.calls


def test_find_repo_walks_up_to_the_clone(tmp_path):
    repo = tmp_path / "minecraft-setup"
    put(repo, "pyproject.toml", 'name = "minecraft-setup"\n')
    (repo / ".git").mkdir()
    (repo / "src" / "pkg").mkdir(parents=True)
    other = tmp_path / "other"
    put(other, "pyproject.toml", 'name = "other"\n')
    (other / ".git").mkdir()
    assert find_repo([other, repo / "src" / "pkg"]) == repo
    assert find_repo([other]) is None


def git_repo(path):
    path.mkdir()
    for args in (["init", "-q"], ["config", "core.autocrlf", "true"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


def no_push(argv, **kwargs):
    if argv[1] == "push":
        return subprocess.CompletedProcess(argv, 0, "", "")
    return subprocess.run(argv, **kwargs)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_published_bytes_survive_line_ending_conversion(live, modrinth, tmp_path):
    repo = git_repo(tmp_path / "repo")
    put(live.root, "config/crlf.toml", b"a = 1\r\nb = 2\r\n")
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    assert git_publish(repo, "fabric-26.3", run=no_push)
    shown = subprocess.run(["git", "show", "HEAD:packs/fabric-26.3/files/config/crlf.toml"], cwd=repo,
                           check=True, capture_output=True).stdout
    assert shown == b"a = 1\r\nb = 2\r\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_ignored_pack_file_refuses_publish(live, modrinth, tmp_path):
    repo = git_repo(tmp_path / "repo")
    put(repo, ".gitignore", "xaero/\n")
    put(live.root, "config/xaero/client.cfg", "x")
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    with pytest.raises(PublishError, match="client.cfg"):
        git_publish(repo, "fabric-26.3", run=no_push)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_republish_fixes_bytes_stored_before_the_attributes(live, modrinth, tmp_path):
    repo = git_repo(tmp_path / "repo")
    put(repo, "packs/fabric-26.3/files/config/crlf.toml", b"a = 1\r\n")
    for args in (["add", "--", "packs"], ["commit", "-q", "-m", "old"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    put(live.root, "config/crlf.toml", b"a = 1\r\n")
    publish_pack(install(live), info(), repo, modrinth, running=idle)
    assert git_publish(repo, "fabric-26.3", run=no_push)
    shown = subprocess.run(["git", "show", "HEAD:packs/fabric-26.3/files/config/crlf.toml"], cwd=repo,
                           check=True, capture_output=True).stdout
    assert shown == b"a = 1\r\n"

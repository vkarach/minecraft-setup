import json
from pathlib import Path

from helpers import fabric_install as fabric, neoforge_install as neoforge, version
from manifests import info
from minecraft_setup.engine.installs import Install, find_installs, find_minecraft, match


def test_neoforge_from_plain_string_arguments(tmp_path):
    data = {"id": "nf", "arguments": {"game": ["--fml.neoForgeVersion", "21.1.264"]}}
    version(tmp_path, "nf", data, {"jar": "1.21.1"})
    assert find_installs(tmp_path) == [Install("nf", tmp_path, "1.21.1", "neoforge", "21.1.264")]


def test_fabric_root_install(tmp_path):
    fabric(tmp_path)
    assert find_installs(tmp_path) == [Install("Fabric 26.3", tmp_path, "26.3", "fabric", "0.19.5")]


def test_neoforge_modpack_install(tmp_path):
    folder = neoforge(tmp_path)
    assert find_installs(tmp_path) == [Install("Solo", folder, "1.21.1", "neoforge", "21.1.264")]


def test_vanilla_version_skipped(tmp_path):
    version(tmp_path, "26.3", {"id": "26.3", "libraries": [{"name": "org.lwjgl:lwjgl:3.3.3"}]})
    assert find_installs(tmp_path) == []


def test_broken_json_skipped(tmp_path):
    folder = tmp_path / "versions" / "Broken"
    folder.mkdir(parents=True)
    (folder / "Broken.json").write_text("{nope", encoding="utf-8")
    fabric(tmp_path)
    assert [i.name for i in find_installs(tmp_path)] == ["Fabric 26.3"]


def test_inherits_from_fallback(tmp_path):
    data = {"id": "fabric-loader-0.19.5-26.3", "inheritsFrom": "26.3", "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}]}
    version(tmp_path, "fabric-loader-0.19.5-26.3", data)
    assert find_installs(tmp_path)[0].game_version == "26.3"


def test_no_versions_folder(tmp_path):
    assert find_installs(tmp_path) == []


def test_match_prefers_loader_version(tmp_path):
    fabric(tmp_path, "A old", loader="0.18.0")
    fabric(tmp_path, "B same", loader="0.19.5")
    neoforge(tmp_path)
    installs = find_installs(tmp_path)
    assert match(info(), installs).name == "B same"
    assert match(info(loader_version="9.9"), installs).name == "A old"
    assert match(info(id="solo", game_version="1.21.1", loader="neoforge", loader_version="21.1.264"), installs).name == "Solo"
    assert match(info(game_version="1.20.1"), installs) is None


def test_find_minecraft(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert find_minecraft() is None
    (tmp_path / ".minecraft" / "versions").mkdir(parents=True)
    assert find_minecraft() == tmp_path / ".minecraft"

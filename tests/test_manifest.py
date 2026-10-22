import dataclasses
import json

import pytest

from manifests import entry, manifest
from minecraft_setup.engine.manifest import Manifest


def test_manifest_round_trip(tmp_path):
    m = manifest(entry("10 perf - sodium.jar", ids=["sodium"]), configs=[("config/a.json", "aa" * 64)])
    assert Manifest.loads(m.dumps()) == m
    m.save(tmp_path / "packs" / "p" / "manifest.json")
    assert Manifest.load(tmp_path / "packs" / "p" / "manifest.json") == m


@pytest.mark.parametrize("name", ["CON.jar", "a:b.jar", "../x.jar", "x.jar ", "lpt1.jar"])
def test_rejects_reserved_and_bad_names(name):
    with pytest.raises(ValueError, match="name"):
        Manifest.loads(manifest(entry(name)).dumps())


def test_accepts_device_like_prefixes():
    m = manifest(entry("40 console - a.jar"), entry("COM10.jar"))
    assert len(Manifest.loads(m.dumps()).entries) == 2


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("side", "server", "side"),
        ("dir", "saves", "folder"),
        ("url", "http://cdn.test/x.jar", "https"),
        ("sha512", "abc", "sha512"),
        ("default", "yes", "default"),
        ("depends", "a", "depends"),
    ],
)
def test_rejects_bad_entry_fields(field, value, reason):
    data = json.loads(manifest(entry("10 perf - x.jar")).dumps())
    data["entries"][0][field] = value
    with pytest.raises(ValueError, match=reason):
        Manifest.loads(json.dumps(data))


def test_rejects_pack_in_mods_and_jar_in_shaderpacks():
    with pytest.raises(ValueError, match="jar or pack"):
        Manifest.loads(manifest(entry("Comp.zip")).dumps())
    with pytest.raises(ValueError, match="jar or pack"):
        Manifest.loads(manifest(entry("x.jar", dir="shaderpacks")).dumps())


def test_rejects_duplicate_names():
    with pytest.raises(ValueError, match="twice"):
        Manifest.loads(manifest(entry("A.jar"), entry("a.jar")).dumps())


def test_rejects_config_outside_the_config_set():
    for path in ("../evil.txt", "mods/x.jar", "TlauncherProfiles.json"):
        with pytest.raises(ValueError, match="config"):
            Manifest.loads(manifest(configs=[(path, "aa" * 64)]).dumps())


def test_rejects_newer_schema():
    data = json.loads(manifest().dumps())
    data["schema"] = 2
    with pytest.raises(ValueError, match="newer app"):
        Manifest.loads(json.dumps(data))


def test_rejects_bad_pack_info():
    for field, value in (("id", "Bad Id"), ("loader", "quilt"), ("profiles", "no")):
        data = json.loads(manifest().dumps())
        data["pack"][field] = value
        with pytest.raises(ValueError, match=field):
            Manifest.loads(json.dumps(data))


def test_library_flag_and_group():
    lib = entry("01 lib [for x] - y.jar")
    mod = entry("10 perf - y.jar")
    assert lib.library and not mod.library
    assert (lib.group, mod.group, entry("y.jar").group) == (0, 1, None)
    assert not dataclasses.replace(lib, name="01 library - y.jar").library

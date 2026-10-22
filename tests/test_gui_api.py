import time

import pytest

from helpers import FakeModrinth, fabric_install, files, make_world, served_packs
from manifests import entry, manifest
from minecraft_setup.engine.mods import Profile
from minecraft_setup.engine.profiles import GuardError
from minecraft_setup.gui import api as api_module
from minecraft_setup.gui.api import Api

LIB = entry("01 lib [for a] - lib.jar", ids=["lib"], data=b"lib")
A = entry("20 visual - a.jar", ids=["a"], depends=["lib"], data=b"a")
OFF = entry("30 ui - off.jar", ids=["off"], default=False, data=b"off")
WORLD = entry("40 world - terralith.jar", side="required", ids=["terralith"], data=b"terralith")
FABRIC = manifest(LIB, A, OFF, WORLD)


@pytest.fixture
def http():
    return served_packs(FABRIC, **{LIB.url: b"lib", A.url: b"a", OFF.url: b"off", WORLD.url: b"terralith"})


@pytest.fixture
def make(game, tmp_path, http):
    fabric_install(game.root)

    def build(repo=None):
        return Api(game.root, http, FakeModrinth(), repo, None, tmp_path / "cache", probe=lambda game_dir: False)

    return build


def wait(api, job):
    deadline = time.monotonic() + 10
    while not (status := api.job(job["job"]))["done"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return status


def mods(state):
    return {m["name"]: m for m in state["packs"][0]["mods"]}


def test_state_offline(make, http):
    api = make()
    assert not api.state()["offline"]
    http.fail.update(http.served)
    api.refresh()
    state = api.state()
    assert state["offline"]
    assert set(mods(state)) == {LIB.name, A.name, OFF.name, WORLD.name}


def test_state_without_any_copy_reports_the_error(make, http):
    http.fail.update(http.served)
    state = make().state()
    assert state["offline"] and state["packs"] == [] and "index" in state["error"]


def test_state_lists_needed_by(make):
    found = mods(make().state())
    assert found[LIB.name]["library"] and found[LIB.name]["needed_by"] == [A.name]
    assert found[WORLD.name]["side"] == "required" and not found[WORLD.name]["choosable"]
    assert found[A.name]["choosable"] and found[A.name]["enabled"]
    assert not found[OFF.name]["enabled"]


def test_state_shows_install_and_update(make):
    pack = make().state()["packs"][0]
    assert pack["install"] == "Fabric 26.3" and pack["needs"] is None
    assert sorted(pack["update"]["added"]) == sorted([LIB.name, A.name, OFF.name, WORLD.name])
    assert pack["profile"] == "vanilla"


def test_apply_installs_then_choice_renames(make, game):
    api = make()
    assert wait(api, api.apply("fabric-26.3"))["error"] is None
    assert api.state()["packs"][0]["update"] is None
    api.set_choice("fabric-26.3", A.name, False)
    found = mods(api.state())
    assert not found[A.name]["enabled"] and not found[LIB.name]["enabled"]
    assert wait(api, api.apply("fabric-26.3"))["error"] is None
    assert A.name + ".disabled" in files(game)


def test_apply_job_reports_error(make, monkeypatch):
    def refuse(*args, **kwargs):
        raise GuardError("nope")

    monkeypatch.setattr(api_module, "apply_update", refuse)
    api = make()
    status = wait(api, api.apply("fabric-26.3"))
    assert status["error"] == "nope"


def test_unknown_pack_is_an_error_not_an_exception(make):
    assert "nope" in make().apply("nope")["error"]


def test_hidden_worlds_of_the_current_profile_can_come_back(make, game):
    make_world(game, "Lost", marker="vanilla", where=game.vault(Profile.VANILLA))
    make_world(game, "Twin", marker="vanilla", where=game.vault(Profile.VANILLA))
    make_world(game, "Twin")
    make_world(game, "Other", marker="full", where=game.vault(Profile.FULL))
    api = make()
    pack = api.state()["packs"][0]
    assert pack["profile"] == "vanilla" and pack["restorable"] == ["Lost"]
    states = {w["name"] + "/" + (w["profile"] or "-"): w["state"] for w in pack["worlds"]}
    assert states == {"Lost/vanilla": "restorable", "Twin/vanilla": "stuck", "Twin/-": "visible", "Other/full": "hidden"}
    assert wait(api, api.switch_profile("vanilla"))["error"] is None
    assert api.state()["packs"][0]["restorable"] == []


def test_owner_only_with_repo(make, tmp_path):
    assert not make().state()["owner"]
    assert make(repo=tmp_path).state()["owner"]

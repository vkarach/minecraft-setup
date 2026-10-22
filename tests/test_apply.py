import hashlib

import pytest

from helpers import FakeHttp, busy, clock, files, idle, names, put
from manifests import entry, info, manifest
from minecraft_setup.engine.apply import STATE_FILE, InstallState, UpdateInterrupted, apply_update, plan_update
from minecraft_setup.engine.http import HttpError
from minecraft_setup.engine.profiles import GameRunningError, GuardError
from minecraft_setup.engine.remote import config_url

LIB = entry("01 lib [for a] - lib.jar", ids=["lib"], data=b"lib")
A = entry("20 visual - a.jar", ids=["a"], depends=["lib"], data=b"a")
OFF = entry("30 ui - off.jar", ids=["off"], default=False, data=b"off")
WORLD = entry("40 world - terralith.jar", side="required", ids=["terralith"], data=b"terralith")
SHADER = entry("Comp.zip", dir="shaderpacks", data=b"comp")
CONFIG = ("config/a.json", hashlib.sha512(b"{}").hexdigest())
SOLO = info(id="solo", game_version="1.21.1", loader="neoforge", loader_version="21.1.264", profiles=False)


def pack(*entries, configs=(CONFIG,), pack_info=None):
    return manifest(*entries, configs=configs, pack=pack_info)


PAYLOADS = {LIB.name: b"lib", A.name: b"a", OFF.name: b"off", WORLD.name: b"terralith", SHADER.name: b"comp"}


def serve(m, **overrides):
    served = {e.url: PAYLOADS[e.name] for e in m.entries}
    served[config_url(m.pack.id, CONFIG[0])] = b"{}"
    served.update(overrides)
    return FakeHttp(served)


def apply(game, m, http, chosen=None, running=idle):
    return apply_update(game, m, chosen, http, running=running, now=clock)


def test_first_install_downloads_and_writes_configs(game):
    m = pack(LIB, A, OFF, SHADER, pack_info=SOLO)
    done = apply(game, m, serve(m))
    assert sorted(done.added) == sorted([LIB.name, A.name, OFF.name, SHADER.name])
    assert files(game) == sorted([LIB.name, A.name, OFF.name + ".disabled"])
    assert (game.root / "shaderpacks" / "Comp.zip").read_bytes() == b"comp"
    assert (game.root / "config" / "a.json").read_text() == "{}"
    assert done.configs == ["config/a.json"]
    state = InstallState.load(game)
    assert state.pack_id == "solo"
    assert state.applied[f"mods/{A.name}"] == A.sha512
    assert sorted(state.chosen) == [A.name]


def test_unchosen_client_mod_lands_disabled(game):
    m = pack(LIB, A, pack_info=SOLO)
    apply(game, m, serve(m), chosen=frozenset())
    assert files(game) == sorted([LIB.name + ".disabled", A.name + ".disabled"])


def test_new_required_jar_follows_vanilla_profile(game):
    put(game.mods, "41 world - old.jar.disabled", b"old")
    m = pack(LIB, A, WORLD)
    apply(game, m, serve(m))
    assert WORLD.name + ".disabled" in files(game)


def test_new_required_jar_lands_enabled_on_full(game):
    put(game.mods, "41 world - old.jar", b"old")
    m = pack(LIB, A, WORLD)
    apply(game, m, serve(m))
    assert WORLD.name in files(game)


def test_mixed_profile_refused(game):
    put(game.mods, "41 world - old.jar", b"old")
    put(game.mods, "50 world - other.jar.disabled", b"other")
    m = pack(WORLD)
    with pytest.raises(GuardError, match="switch to a profile first"):
        apply(game, m, serve(m))
    assert files(game) == ["41 world - old.jar", "50 world - other.jar.disabled"]


def test_changed_jar_replaced_and_backed_up(game):
    m = pack(LIB, A, pack_info=SOLO)
    apply(game, m, serve(m))
    newer = entry(A.name, ids=["a"], depends=["lib"], data=b"a2")
    m2 = pack(LIB, newer, pack_info=SOLO)
    done = apply(game, m2, serve(m2, **{newer.url: b"a2"}))
    assert done.updated == [A.name]
    assert (game.mods / A.name).read_bytes() == b"a2"
    [backup] = (game.root / "backup").iterdir()
    assert (backup / "mods" / A.name).read_bytes() == b"a"


def test_removed_entry_moved_to_backup(game):
    m = pack(LIB, A, OFF, pack_info=SOLO)
    apply(game, m, serve(m))
    m2 = pack(LIB, A, pack_info=SOLO)
    done = apply(game, m2, serve(m2))
    assert done.removed == [OFF.name]
    assert OFF.name + ".disabled" not in files(game)
    [backup] = (game.root / "backup").iterdir()
    assert names(backup / "mods") == [OFF.name + ".disabled"]
    assert f"mods/{OFF.name}" not in InstallState.load(game).applied


def test_own_jar_untouched(game):
    put(game.mods, "35 sound - mine.jar", b"mine")
    m = pack(LIB, A, pack_info=SOLO)
    done = apply(game, m, serve(m))
    assert done.own == ["35 sound - mine.jar"]
    assert (game.mods / "35 sound - mine.jar").read_bytes() == b"mine"


def test_existing_config_not_overwritten(game):
    put(game.root, "config/a.json", "mine")
    m = pack(LIB, A, pack_info=SOLO)
    done = apply(game, m, serve(m))
    assert done.configs == []
    assert (game.root / "config" / "a.json").read_text() == "mine"


def test_missing_applied_jar_is_redownloaded(game):
    m = pack(LIB, A, pack_info=SOLO)
    apply(game, m, serve(m))
    (game.mods / A.name).unlink()
    plan = plan_update(game, m, None)
    assert plan.added == [A.name]
    apply(game, m, serve(m))
    assert (game.mods / A.name).read_bytes() == b"a"


def test_download_failure_changes_nothing(game):
    m = pack(LIB, A, pack_info=SOLO)
    http = serve(m)
    http.fail.add(A.url)
    with pytest.raises(HttpError):
        apply(game, m, http)
    assert files(game) == []
    assert not (game.root / STATE_FILE).exists()
    assert not (game.root / "config").exists()
    assert [p.name for p in game.root.iterdir() if p.name.startswith(".update-")] == []


def test_hash_mismatch_changes_nothing(game):
    m = pack(LIB, A, pack_info=SOLO)
    with pytest.raises(HttpError, match="sha512"):
        apply(game, m, serve(m, **{A.url: b"evil"}))
    assert files(game) == []


def test_running_game_refused(game):
    m = pack(LIB, A, pack_info=SOLO)
    with pytest.raises(GameRunningError):
        apply(game, m, serve(m), running=busy)
    assert files(game) == []


def test_choice_change_only_renames(game):
    m = pack(LIB, A, OFF, pack_info=SOLO)
    apply(game, m, serve(m))
    http = serve(m)
    done = apply(game, m, http, chosen=frozenset({OFF.name}))
    assert http.requests == []
    assert sorted(done.enable) == [OFF.name]
    assert sorted(done.disable) == sorted([LIB.name, A.name])
    assert files(game) == sorted([LIB.name + ".disabled", A.name + ".disabled", OFF.name])
    assert InstallState.load(game).chosen == [OFF.name]


def test_failure_part_way_reports_what_changed(game, monkeypatch):
    m = pack(LIB, A, OFF, pack_info=SOLO)
    apply(game, m, serve(m))
    real = type(game.mods).rename
    calls = []

    def flaky(self, target):
        calls.append(self)
        if len(calls) > 1:
            raise PermissionError("locked")
        return real(self, target)

    monkeypatch.setattr(type(game.mods), "rename", flaky)
    with pytest.raises(UpdateInterrupted) as caught:
        apply(game, m, serve(m), chosen=frozenset({OFF.name}))
    assert len(caught.value.done.enable + caught.value.done.disable) == 1
    assert InstallState.load(game).chosen == [A.name]


def test_saved_choice_is_reused(game):
    m = pack(LIB, A, OFF, pack_info=SOLO)
    apply(game, m, serve(m), chosen=frozenset({OFF.name}))
    assert plan_update(game, m, None).empty


def test_other_pack_state_refused(game):
    m = pack(LIB, A, pack_info=SOLO)
    apply(game, m, serve(m))
    other = pack(LIB, A)
    with pytest.raises(GuardError, match="solo"):
        plan_update(game, other, None)


def test_both_jar_states_refused(game):
    put(game.mods, A.name, b"a")
    put(game.mods, A.name + ".disabled", b"a")
    m = pack(LIB, A, pack_info=SOLO)
    with pytest.raises(GuardError, match="Both"):
        plan_update(game, m, None)

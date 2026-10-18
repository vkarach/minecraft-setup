import pytest

from helpers import BEFORE, CLIENT, FULL, LATER, SOLO, T0, busy, clock, files, fill, idle, make_jar, make_world, pack
from minecraft_setup.engine.mods import Profile, available, detect, leaving, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError, assign, switch
from minecraft_setup.engine.state import read_since


def disabled(names):
    return [n + ".disabled" for n in names]


def test_detect_each_profile_and_mixed(game):
    (game.mods / "_README.txt").touch()
    mods = fill(game, on=CLIENT, off=FULL + SOLO)
    assert detect(mods) is Profile.VANILLA
    assert sorted((m.path.name, m.group, m.enabled) for m in mods) == [
        ("01 lib - cloth.jar", 0, True),
        ("40 world - terralith.jar.disabled", 4, False),
        ("50 mobs - friends.jar.disabled", 5, False),
        ("60 rpg - spell engine.jar.disabled", 6, False),
        ("61 rpg [needs tl-skins-off] - wizards.jar.disabled", 6, False),
        ("fabric-api.jar", None, True),
    ]
    assert detect(fill(game, on=CLIENT + FULL, off=SOLO)) is Profile.FULL
    assert detect(fill(game, on=FULL + SOLO, off=CLIENT)) is Profile.SOLO
    assert detect(fill(game, on=FULL)) is Profile.FULL
    assert detect(fill(game, on=FULL[:1], off=FULL[1:] + SOLO)) == "mixed"
    assert detect(fill(game, on=SOLO, off=FULL)) == "mixed"
    assert detect(fill(game, on=FULL + SOLO[:1], off=SOLO[1:])) == "mixed"
    fill(game)
    game.mods.rmdir()
    assert list_mods(game) == []
    assert detect([]) is Profile.VANILLA


def test_available_and_leaving(game):
    assert available(pack(game, Profile.FULL, solo=False)) == [Profile.VANILLA, Profile.FULL]
    assert available(pack(game, Profile.VANILLA)) == [Profile.VANILLA, Profile.FULL, Profile.SOLO]
    assert leaving(fill(game, on=FULL[:1], off=FULL[1:] + SOLO)) is Profile.FULL
    assert leaving(fill(game, on=SOLO[:1], off=FULL + SOLO[1:])) is Profile.SOLO
    assert leaving(pack(game, Profile.VANILLA)) is Profile.VANILLA


def test_full_to_solo_enables_6x(game):
    pack(game, Profile.FULL)
    report = switch(game, Profile.SOLO, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + FULL + SOLO)
    assert report.enabled == SOLO
    assert report.disabled == []
    assert detect(list_mods(game)) is Profile.SOLO
    assert read_since(game) == LATER


def test_solo_to_full_disables_only_6x(game):
    pack(game, Profile.SOLO)
    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + FULL + disabled(SOLO))
    assert report.disabled == SOLO
    assert report.enabled == []


def test_solo_to_vanilla_disables_4x_to_6x(game):
    pack(game, Profile.SOLO)
    report = switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + disabled(FULL + SOLO))
    assert report.disabled == FULL + SOLO
    assert detect(list_mods(game)) is Profile.VANILLA


def test_mixed_to_profile_enables_exactly_its_groups(game):
    fill(game, on=CLIENT + FULL[:1] + SOLO[:1], off=FULL[1:] + SOLO[1:])
    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + FULL + disabled(SOLO))
    assert report.enabled == FULL[1:]
    assert report.disabled == SOLO[:1]


def test_solo_unavailable_without_6x(game):
    pack(game, Profile.FULL, solo=False)
    before = files(game)
    with pytest.raises(GuardError, match="no solo mods"):
        switch(game, Profile.SOLO, running=idle, now=clock)
    with pytest.raises(GuardError, match="no solo mods"):
        assign(game, Profile.SOLO, ["W"], running=idle)
    assert files(game) == before
    assert read_since(game) == T0


def test_rename_collision_refused_before_any_change(game):
    pack(game, Profile.FULL)
    make_jar(game, FULL[0], enabled=False)
    world = make_world(game, "W", opened_at=LATER)
    before = files(game)
    with pytest.raises(GuardError, match="Both"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert sorted(p.name for p in world.iterdir()) == ["level.dat"]


def test_running_blocks_switch_and_assign(game):
    pack(game, Profile.FULL)
    world = make_world(game, "W", opened_at=BEFORE)
    before = files(game)
    with pytest.raises(GameRunningError):
        switch(game, Profile.VANILLA, running=busy, now=clock)
    with pytest.raises(GameRunningError):
        assign(game, Profile.VANILLA, ["W"], running=busy)
    assert files(game) == before
    assert sorted(p.name for p in world.iterdir()) == ["level.dat"]
    assert read_since(game) == T0

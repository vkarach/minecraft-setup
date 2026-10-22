import inspect
from datetime import datetime, timedelta

import pytest

from helpers import BEFORE, CLIENT, FULL, LATER, T0, busy, clock, files, fill, idle, make_jar, make_world, pack
from minecraft_setup.engine.mods import Profile, detect, leaving, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError, assign, switch
from minecraft_setup.engine.state import read_since


RPG = "60 rpg - spell engine.jar"


def disabled(names):
    return [n + ".disabled" for n in names]


def test_detect_each_profile_and_mixed(game):
    (game.mods / "_README.txt").touch()
    mods = fill(game, on=CLIENT, off=FULL)
    assert detect(mods) is Profile.VANILLA
    assert sorted((m.path.name, m.group, m.enabled) for m in mods) == [
        ("01 lib - cloth.jar", 0, True),
        ("40 world - terralith.jar.disabled", 4, False),
        ("50 mobs - friends.jar.disabled", 5, False),
        ("fabric-api.jar", None, True),
    ]
    assert detect(fill(game, on=CLIENT + FULL)) is Profile.FULL
    assert detect(fill(game, on=FULL)) is Profile.FULL
    assert detect(fill(game, on=FULL[:1], off=FULL[1:])) == "mixed"
    fill(game)
    game.mods.rmdir()
    assert list_mods(game) == []
    assert detect([]) is Profile.VANILLA


def test_profiles_are_vanilla_and_full():
    assert [p.value for p in Profile] == ["vanilla", "full"]


def test_leaving(game):
    assert leaving(fill(game, on=FULL[:1], off=FULL[1:])) is Profile.FULL
    assert leaving(pack(game, Profile.VANILLA)) is Profile.VANILLA
    assert leaving(pack(game, Profile.FULL)) is Profile.FULL


def test_6x_jar_is_not_managed(game):
    pack(game, Profile.FULL)
    make_jar(game, RPG)
    switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + disabled(FULL) + [RPG])
    switch(game, Profile.FULL, running=idle, now=lambda: LATER + timedelta(days=1))
    assert files(game) == sorted(CLIENT + FULL + [RPG])


def test_mixed_to_profile_enables_exactly_its_groups(game):
    fill(game, on=CLIENT + FULL[:1], off=FULL[1:])
    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + FULL)
    assert report.enabled == FULL[1:]
    assert report.disabled == []
    assert read_since(game) == LATER


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


def test_switch_and_assign_need_an_explicit_running_check():
    for func in (switch, assign):
        assert inspect.signature(func).parameters["running"].default is inspect.Parameter.empty
    now = inspect.signature(switch).parameters["now"].default()
    assert now.utcoffset() == datetime.now().astimezone().utcoffset()

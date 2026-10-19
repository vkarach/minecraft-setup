import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers import AFTER, BEFORE, CLIENT, FULL, LATER, SOLO, T0, clock, files, fill, idle, make_world, names, pack
from minecraft_setup.engine import state
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.profiles import GuardError, SwitchInterrupted, assign, switch
from minecraft_setup.engine.state import read_since, write_since
from minecraft_setup.engine.worlds import World, list_worlds


def marks(world):
    return sorted(p.name for p in world.iterdir() if p.name.endswith("-profile"))


def test_untagged_opened_world_tagged_with_leaving_profile(game):
    pack(game, Profile.FULL)
    make_world(game, "Old", opened_at=BEFORE)
    make_world(game, "New", opened_at=AFTER)
    make_world(game, "Edge", opened_at=T0)
    report = switch(game, Profile.VANILLA, running=idle, now=clock)
    assert report.tagged == ["Edge", "New"]
    assert report.hidden == ["Edge", "New"]
    assert names(game.saves) == ["Old"]
    assert marks(game.saves / "Old") == []
    assert names(game.vault(Profile.FULL)) == ["Edge", "New"]
    assert marks(game.vault(Profile.FULL) / "New") == [".full-profile"]


def test_mixed_with_6x_tags_solo(game):
    write_since(game, T0)
    fill(game, on=CLIENT + FULL[:1] + SOLO[:1], off=FULL[1:] + SOLO[1:])
    make_world(game, "Mixed", opened_at=AFTER)
    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert report.tagged == ["Mixed"]
    assert names(game.vault(Profile.SOLO)) == ["Mixed"]
    assert marks(game.vault(Profile.SOLO) / "Mixed") == [".solo-profile"]
    assert detect(list_mods(game)) is Profile.FULL


def test_foreign_world_hidden_and_restored(game):
    pack(game, Profile.FULL)
    make_world(game, "F", marker="full", opened_at=BEFORE)
    make_world(game, "V", marker="vanilla", opened_at=BEFORE)
    make_world(game, "S", marker="solo", opened_at=BEFORE)

    report = switch(game, Profile.VANILLA, running=idle, now=clock)
    assert report.hidden == ["F", "S"]
    assert report.restored == []
    assert names(game.saves) == ["V"]
    assert names(game.vault(Profile.FULL)) == ["F"]
    assert names(game.vault(Profile.SOLO)) == ["S"]

    report = switch(game, Profile.SOLO, running=idle, now=lambda: LATER + timedelta(days=1))
    assert report.hidden == ["V"]
    assert report.restored == ["S"]
    assert report.tagged == []
    assert names(game.saves) == ["S"]
    assert names(game.vault(Profile.VANILLA)) == ["V"]
    assert names(game.vault(Profile.FULL)) == ["F"]
    assert names(game.vault(Profile.SOLO)) == []
    assert (game.saves / "S" / "level.dat").is_file()


def test_hide_refuses_on_vault_name_clash(game):
    pack(game, Profile.FULL)
    make_world(game, "W", marker="full", opened_at=BEFORE)
    make_world(game, "W", marker="full", where=game.vault(Profile.FULL))
    make_world(game, "Opened", opened_at=AFTER)
    make_world(game, "Mine", marker="vanilla", where=game.vault(Profile.VANILLA))
    before = files(game)
    with pytest.raises(GuardError, match="already exists"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert names(game.saves) == ["Opened", "W"]
    assert marks(game.saves / "Opened") == []
    assert names(game.vault(Profile.FULL)) == ["W"]
    assert names(game.vault(Profile.VANILLA)) == ["Mine"]
    assert read_since(game) == T0


def test_hide_refuses_on_clash_of_a_world_it_would_tag(game):
    pack(game, Profile.FULL)
    make_world(game, "W", opened_at=AFTER)
    make_world(game, "W", marker="full", where=game.vault(Profile.FULL))
    before = files(game)
    with pytest.raises(GuardError, match="already exists"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert marks(game.saves / "W") == []


def test_restore_keeps_on_saves_name_clash(game):
    pack(game, Profile.VANILLA)
    make_world(game, "W", opened_at=BEFORE)
    make_world(game, "W", marker="full", where=game.vault(Profile.FULL))
    make_world(game, "X", marker="full", where=game.vault(Profile.FULL))
    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert report.kept == ["W"]
    assert report.restored == ["X"]
    assert names(game.vault(Profile.FULL)) == ["W"]
    assert marks(game.saves / "W") == []
    assert names(game.saves) == ["W", "X"]


def test_assign_rules(game):
    pack(game, Profile.FULL)
    make_world(game, "Fresh", opened_at=BEFORE)
    make_world(game, "Opened", opened_at=AFTER)
    make_world(game, "Solo", marker="solo", where=game.vault(Profile.SOLO))
    make_world(game, "Vanilla", marker="vanilla", where=game.vault(Profile.VANILLA))

    with pytest.raises(GuardError, match="belongs to 'solo'"):
        assign(game, Profile.FULL, ["Solo"], running=idle)
    with pytest.raises(GuardError, match="was opened on 'full'"):
        assign(game, Profile.VANILLA, ["Fresh", "Opened"], running=idle)
    with pytest.raises(GuardError, match="not found"):
        assign(game, Profile.VANILLA, ["Fresh", "Nope"], running=idle)
    assert marks(game.saves / "Fresh") == []
    assert marks(game.saves / "Opened") == []

    assert assign(game, Profile.VANILLA, ["Fresh", "Vanilla"], running=idle) == ["Fresh"]
    assert names(game.saves) == ["Opened"]
    assert names(game.vault(Profile.VANILLA)) == ["Fresh", "Vanilla"]
    assert marks(game.vault(Profile.VANILLA) / "Fresh") == [".vanilla-profile"]

    assert assign(game, Profile.FULL, ["Opened"], running=idle) == ["Opened"]
    assert names(game.saves) == ["Opened"]
    assert marks(game.saves / "Opened") == [".full-profile"]


def test_assign_on_mixed_hides_every_tagged_world(game):
    write_since(game, T0)
    fill(game, on=CLIENT + FULL[:1], off=FULL[1:] + SOLO)
    make_world(game, "A", opened_at=BEFORE)
    make_world(game, "Back", marker="full", where=game.vault(Profile.FULL))
    assert assign(game, Profile.FULL, ["A"], running=idle) == ["A"]
    assert names(game.saves) == []
    assert names(game.vault(Profile.FULL)) == ["A", "Back"]


def test_bracket_names(game):
    pack(game, Profile.FULL)
    make_world(game, "World [1]", marker="full", opened_at=BEFORE)
    make_world(game, "New [a-z]", opened_at=AFTER)
    make_world(game, "Solo [2]", marker="solo", where=game.vault(Profile.SOLO))

    report = switch(game, Profile.SOLO, running=idle, now=clock)
    assert files(game) == sorted(CLIENT + FULL + SOLO)
    assert report.enabled == SOLO
    assert report.tagged == ["New [a-z]"]
    assert report.hidden == ["New [a-z]", "World [1]"]
    assert report.restored == ["Solo [2]"]
    assert names(game.saves) == ["Solo [2]"]

    report = switch(game, Profile.FULL, running=idle, now=lambda: LATER + timedelta(days=1))
    assert report.disabled == SOLO
    assert report.restored == ["New [a-z]", "World [1]"]
    assert names(game.vault(Profile.SOLO)) == ["Solo [2]"]

    make_world(game, "Plain [x]", opened_at=BEFORE)
    assert assign(game, Profile.VANILLA, ["Plain [x]"], running=idle) == ["Plain [x]"]
    assert names(game.vault(Profile.VANILLA)) == ["Plain [x]"]


def test_legacy_state_and_markers(game):
    assert read_since(game) == datetime.min.replace(tzinfo=timezone.utc)

    game.state_file.write_text('{\n    "fullSince":  "2026-10-17T17:40:18.3056254+02:00"\n}', encoding="ascii")
    plus2 = timezone(timedelta(hours=2))
    since = datetime(2026, 10, 17, 17, 40, 18, 305625, tzinfo=plus2)
    assert read_since(game) == since

    game.state_file.write_text(
        json.dumps({"since": "2026-10-18T09:00:00+02:00", "fullSince": "2026-10-17T17:40:18+02:00"}),
        encoding="ascii",
    )
    assert read_since(game) == datetime(2026, 10, 18, 9, 0, tzinfo=plus2)

    when = datetime(2026, 10, 19, 8, 30, 15, 123456, tzinfo=plus2)
    write_since(game, when)
    raw = json.loads(game.state_file.read_text(encoding="ascii"))
    assert list(raw) == ["since"]
    assert datetime.fromisoformat(raw["since"]) == when
    assert read_since(game) == when

    full = make_world(game, "Old full", marker="full", where=game.root / "saves-full-profile")
    vanilla = make_world(game, "Old vanilla", marker="vanilla")
    untagged = make_world(game, "Untagged")
    assert list_worlds(game) == [
        World(vanilla, Profile.VANILLA, True),
        World(untagged, None, True),
        World(full, Profile.FULL, False),
    ]


def test_same_profile_sweeps_worlds(game):
    pack(game, Profile.FULL)
    make_world(game, "Copied", marker="vanilla", opened_at=BEFORE)
    make_world(game, "Back", marker="full", where=game.vault(Profile.FULL))
    make_world(game, "Opened", opened_at=AFTER)
    state = game.state_file.read_text(encoding="ascii")
    before = files(game)

    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert report.hidden == ["Copied"]
    assert report.restored == ["Back"]
    assert report.tagged == []
    assert report.enabled == [] and report.disabled == []
    assert files(game) == before
    assert names(game.saves) == ["Back", "Opened"]
    assert names(game.vault(Profile.VANILLA)) == ["Copied"]
    assert marks(game.saves / "Opened") == []
    assert game.state_file.read_text(encoding="ascii") == state


def test_unreadable_state_refuses_before_any_change(game):
    pack(game, Profile.FULL)
    game.state_file.write_text('{"since": ""}', encoding="ascii")
    world = make_world(game, "W", opened_at=AFTER)
    before = files(game)
    with pytest.raises(GuardError, match="no readable 'since'"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    with pytest.raises(GuardError, match="no readable 'since'"):
        assign(game, Profile.FULL, ["W"], running=idle)
    assert files(game) == before
    assert marks(world) == []


def fail_rename_on(monkeypatch, call):
    original = Path.rename
    count = 0

    def rename(self, target):
        nonlocal count
        count += 1
        if count == call:
            raise PermissionError("locked")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_restore_skips_foreign_worlds_and_relocates_them(game):
    pack(game, Profile.VANILLA)
    make_world(game, "Own", marker="full", where=game.vault(Profile.FULL))
    make_world(game, "Bare", where=game.vault(Profile.FULL))
    make_world(game, "Stray", marker="solo", where=game.vault(Profile.FULL))
    make_world(game, "Lost", marker="full", where=game.vault(Profile.VANILLA))
    make_world(game, "Home", marker="solo", where=game.vault(Profile.SOLO))

    report = switch(game, Profile.FULL, running=idle, now=clock)
    assert report.enabled == FULL
    assert report.relocated == ["Lost", "Stray"]
    assert report.restored == ["Bare", "Own"]
    assert names(game.saves) == ["Bare", "Lost", "Own"]
    assert marks(game.saves / "Lost") == [".full-profile"]
    assert names(game.vault(Profile.FULL)) == []
    assert names(game.vault(Profile.VANILLA)) == []
    assert names(game.vault(Profile.SOLO)) == ["Home", "Stray"]


def test_relocation_clash_refuses_before_any_change(game):
    pack(game, Profile.FULL)
    make_world(game, "W", marker="solo", where=game.vault(Profile.FULL))
    make_world(game, "W", marker="solo", where=game.vault(Profile.SOLO))
    make_world(game, "Opened", opened_at=AFTER)
    before = files(game)
    with pytest.raises(GuardError, match="already exists"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert marks(game.saves / "Opened") == []
    assert names(game.saves) == ["Opened"]
    assert names(game.vault(Profile.FULL)) == ["W"]
    assert names(game.vault(Profile.SOLO)) == ["W"]
    assert read_since(game) == T0


def test_relocation_refuses_two_worlds_bound_for_one_vault(game):
    pack(game, Profile.FULL)
    make_world(game, "D", marker="solo", where=game.vault(Profile.FULL))
    make_world(game, "D", marker="solo", where=game.vault(Profile.VANILLA))
    before = files(game)
    with pytest.raises(GuardError, match="Two worlds named 'D'"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert names(game.vault(Profile.FULL)) == ["D"]
    assert names(game.vault(Profile.VANILLA)) == ["D"]
    assert names(game.vault(Profile.SOLO)) == []


def test_assign_relocates_world_found_in_another_vault(game):
    pack(game, Profile.FULL)
    make_world(game, "A", opened_at=BEFORE, where=game.vault(Profile.VANILLA))
    make_world(game, "B", opened_at=BEFORE, where=game.vault(Profile.SOLO))

    assert assign(game, Profile.SOLO, ["A"], running=idle) == ["A"]
    assert names(game.vault(Profile.VANILLA)) == []
    assert names(game.vault(Profile.SOLO)) == ["A", "B"]
    assert marks(game.vault(Profile.SOLO) / "A") == [".solo-profile"]

    assert assign(game, Profile.FULL, ["B"], running=idle) == ["B"]
    assert names(game.saves) == ["B"]
    assert marks(game.saves / "B") == [".full-profile"]
    assert names(game.vault(Profile.SOLO)) == ["A"]


def test_assign_relocation_clash_refuses_before_any_change(game):
    pack(game, Profile.FULL)
    make_world(game, "A", opened_at=BEFORE, where=game.vault(Profile.VANILLA))
    (game.vault(Profile.SOLO) / "A").mkdir(parents=True)
    with pytest.raises(GuardError, match="already exists"):
        assign(game, Profile.SOLO, ["A"], running=idle)
    assert marks(game.vault(Profile.VANILLA) / "A") == []
    assert names(game.vault(Profile.VANILLA)) == ["A"]


def test_assign_matches_names_case_insensitively(game):
    pack(game, Profile.FULL)
    make_world(game, "Fresh", opened_at=BEFORE)
    assert assign(game, Profile.VANILLA, ["fresh"], running=idle) == ["Fresh"]
    assert names(game.vault(Profile.VANILLA)) == ["Fresh"]

    make_world(game, "W", opened_at=BEFORE)
    make_world(game, "w", marker="solo", where=game.vault(Profile.SOLO))
    with pytest.raises(GuardError, match="more than one world"):
        assign(game, Profile.FULL, ["W"], running=idle)
    assert marks(game.saves / "W") == []


def test_switch_without_state_tags_every_untagged_world(game):
    fill(game, on=CLIENT + FULL, off=SOLO)
    make_world(game, "Old", opened_at=BEFORE)
    make_world(game, "New", opened_at=AFTER)
    make_world(game, "Tagged", marker="vanilla", opened_at=BEFORE)
    report = switch(game, Profile.VANILLA, running=idle, now=clock)
    assert report.tagged == ["New", "Old"]
    assert report.hidden == ["New", "Old"]
    assert names(game.saves) == ["Tagged"]
    assert names(game.vault(Profile.FULL)) == ["New", "Old"]
    assert marks(game.vault(Profile.FULL) / "Old") == [".full-profile"]
    assert read_since(game) == LATER


def test_file_in_place_of_a_vault_refuses_before_any_change(game):
    pack(game, Profile.FULL)
    make_world(game, "F", marker="full", opened_at=BEFORE)
    make_world(game, "Opened", opened_at=AFTER)
    game.vault(Profile.FULL).touch()
    before = files(game)
    with pytest.raises(GuardError, match="not a folder"):
        switch(game, Profile.VANILLA, running=idle, now=clock)
    assert files(game) == before
    assert names(game.saves) == ["F", "Opened"]
    assert marks(game.saves / "Opened") == []
    assert read_since(game) == T0


def test_file_in_place_of_saves_refuses_before_any_change(game):
    pack(game, Profile.VANILLA)
    make_world(game, "X", marker="full", where=game.vault(Profile.FULL))
    game.saves.rmdir()
    game.saves.touch()
    before = files(game)
    with pytest.raises(GuardError, match="not a folder"):
        switch(game, Profile.FULL, running=idle, now=clock)
    assert files(game) == before
    assert names(game.vault(Profile.FULL)) == ["X"]
    assert read_since(game) == T0


def test_failure_mid_hide_reports_what_moved(game, monkeypatch):
    pack(game, Profile.FULL)
    make_world(game, "A", marker="full", opened_at=BEFORE)
    make_world(game, "B", marker="full", opened_at=BEFORE)
    before = files(game)
    fail_rename_on(monkeypatch, 2)
    with pytest.raises(SwitchInterrupted) as caught:
        switch(game, Profile.VANILLA, running=idle, now=clock)
    monkeypatch.undo()
    assert not isinstance(caught.value, GuardError)
    assert isinstance(caught.value.__cause__, PermissionError)
    assert caught.value.report.hidden == ["A"]
    assert caught.value.report.disabled == []
    assert files(game) == before
    assert names(game.saves) == ["B"]
    assert names(game.vault(Profile.FULL)) == ["A"]
    assert read_since(game) == T0


def test_assign_failure_reports_what_was_tagged(game, monkeypatch):
    pack(game, Profile.FULL)
    make_world(game, "Fresh", opened_at=BEFORE)
    fail_rename_on(monkeypatch, 1)
    with pytest.raises(SwitchInterrupted) as caught:
        assign(game, Profile.VANILLA, ["Fresh"], running=idle)
    monkeypatch.undo()
    assert isinstance(caught.value.__cause__, PermissionError)
    assert caught.value.report.tagged == ["Fresh"]
    assert caught.value.report.hidden == []
    assert marks(game.saves / "Fresh") == [".vanilla-profile"]


def test_write_since_replaces_the_file_atomically(game, monkeypatch):
    write_since(game, T0)
    calls = []
    original = state.os.replace

    def replace(src, dst):
        calls.append((Path(src), Path(dst)))
        return original(src, dst)

    monkeypatch.setattr(state.os, "replace", replace)
    write_since(game, LATER)
    assert read_since(game) == LATER
    assert [(src.parent, dst) for src, dst in calls] == [(game.root, game.state_file)]

    def broken(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(state.os, "replace", broken)
    with pytest.raises(PermissionError):
        write_since(game, T0)
    monkeypatch.undo()
    assert read_since(game) == LATER
    assert names(game.root) == ["mods", "mods-profile.state.json", "saves"]

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.http import Http, sha512_of
from minecraft_setup.engine.manifest import Manifest, ModEntry
from minecraft_setup.engine.mods import DISABLED, MANAGED, Profile, detect, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError
from minecraft_setup.engine.remote import config_url
from minecraft_setup.engine.selection import default_choice, resolve

STATE_FILE = "minecraft-setup.json"
_STAGING = ".update-"


@dataclass
class InstallState:
    pack_id: str
    applied: dict[str, str]
    chosen: list[str] | None

    @classmethod
    def load(cls, game: GameDir) -> InstallState | None:
        path = game.root / STATE_FILE
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = cls(data["pack_id"], dict(data["applied"]), data["chosen"])
            if state.chosen is not None:
                state.chosen = [str(name) for name in state.chosen]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise GuardError(f"{path} is unreadable ({error}) - delete it to let the app start over.") from error
        return state

    def save(self, game: GameDir) -> None:
        path = game.root / STATE_FILE
        handle, temp = tempfile.mkstemp(dir=game.root, prefix=STATE_FILE + ".", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"pack_id": self.pack_id, "applied": self.applied, "chosen": self.chosen}, indent=2))
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


@dataclass
class UpdatePlan:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    own: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.updated or self.removed or self.enable or self.disable or self.configs)


class UpdateInterrupted(Exception):
    """A file operation failed part-way; `done` lists what had already changed."""

    def __init__(self, done: UpdatePlan, error: Exception) -> None:
        super().__init__(f"Stopped part-way, some changes were already made: {error}")
        self.done = done


@dataclass(frozen=True)
class _Jar:
    entry: ModEntry
    current: Path | None
    dest: Path
    download: bool


@dataclass
class _Work:
    plan: UpdatePlan
    state: InstallState | None
    chosen: frozenset[str]
    jars: list[_Jar]
    removals: list[tuple[str, Path]]
    configs: list[tuple[str, str, Path]]


def _key(entry: ModEntry) -> str:
    return f"{entry.dir}/{entry.name}"


def _present(game: GameDir, entry: ModEntry) -> Path | None:
    folder = game.root / entry.dir
    if folder.exists() and not folder.is_dir():
        raise GuardError(f"'{entry.dir}' exists but is not a folder - move it out of the way and retry.")
    found = [p for p in (folder / entry.name, folder / (entry.name + DISABLED)) if p.is_file()]
    if len(found) > 1:
        raise GuardError(f"Both '{entry.name}' and '{entry.name}{DISABLED}' exist in {entry.dir} - remove one and retry.")
    return found[0] if found else None


def _profile_on(game: GameDir, m: Manifest) -> bool | None:
    """Whether managed jars should be on; None when the pack has no profiles."""
    if not m.pack.profiles:
        return None
    current = detect(list_mods(game))
    if current == "mixed":
        raise GuardError("Some server mods are on and some are off - switch to a profile first.")
    return current is Profile.FULL


def _wanted(entry: ModEntry, enabled: frozenset[str], profile_on: bool | None) -> bool:
    if entry.dir != "mods":
        return True
    if profile_on is not None and entry.group in MANAGED:
        return profile_on
    return entry.name in enabled


def _work(game: GameDir, m: Manifest, chosen: frozenset[str] | None) -> _Work:
    state = InstallState.load(game)
    if state is not None and state.pack_id != m.pack.id:
        raise GuardError(f"{game.root} holds pack '{state.pack_id}', not '{m.pack.id}'.")
    if chosen is None:
        chosen = frozenset(state.chosen) if state and state.chosen is not None else default_choice(m)
    enabled = resolve(m, chosen)
    profile_on = _profile_on(game, m)
    plan, jars = UpdatePlan(), []
    for entry in m.entries:
        current = _present(game, entry)
        on = _wanted(entry, enabled, profile_on)
        dest = game.root / entry.dir / (entry.name if on else entry.name + DISABLED)
        stale = current is None or sha512_of(current) != entry.sha512
        if stale:
            (plan.added if current is None else plan.updated).append(entry.name)
        elif current != dest:
            (plan.enable if on else plan.disable).append(entry.name)
        if stale or current != dest:
            jars.append(_Jar(entry, current, dest, stale))
    keys = {_key(e) for e in m.entries}
    removals = []
    for key in sorted(state.applied if state else {}):
        folder, _, name = key.partition("/")
        found = [p for p in (game.root / folder / name, game.root / folder / (name + DISABLED)) if p.is_file()]
        if key not in keys and found:
            plan.removed.append(name)
            removals += [(key, p) for p in found]
    known = keys | set(state.applied if state else {})
    plan.own = [mod.name for mod in list_mods(game) if f"mods/{mod.name}" not in known]
    configs = []
    for config in m.configs:
        dest = game.root / config.path
        if not dest.exists() and not dest.is_symlink():
            plan.configs.append(config.path)
            configs.append((config.path, config.sha512, dest))
    return _Work(plan, state, chosen, jars, removals, configs)


def plan_update(game: GameDir, m: Manifest, chosen: frozenset[str] | None) -> UpdatePlan:
    """What applying `m` with this choice would change; nothing is touched."""
    return _work(game, m, chosen).plan


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _stage(game: GameDir, m: Manifest, work: _Work, http: Http) -> tuple[Path | None, dict[str, Path]]:
    """Download and verify everything first, so a failure leaves the game dir untouched."""
    needed = [j for j in work.jars if j.download]
    if not needed and not work.configs:
        return None, {}
    game.root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=_STAGING, dir=game.root))
    staged = {}
    try:
        for n, jar in enumerate(needed):
            path = staging / f"j{n}"
            http.download(jar.entry.url, path, sha512=jar.entry.sha512)
            staged[_key(jar.entry)] = path
        for n, (rel, sha, _) in enumerate(work.configs):
            path = staging / f"c{n}"
            http.download(config_url(m.pack.id, rel), path, sha512=sha)
            staged[rel] = path
    except BaseException:
        _remove(staging)
        raise
    return staging, staged


class _Backup:
    def __init__(self, game: GameDir, stamp: datetime) -> None:
        self.base = game.root / "backup" / f"update-{stamp:%Y-%m-%d_%H-%M-%S}"
        self.folder: Path | None = None

    def move(self, path: Path, rel: str) -> None:
        if self.folder is None:
            self.folder, n = self.base, 1
            while self.folder.exists():
                n += 1
                self.folder = self.base.with_name(f"{self.base.name}-{n}")
        dest = self.folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest)


def _apply(game: GameDir, m: Manifest, work: _Work, staged: dict[str, Path], done: UpdatePlan, backup: _Backup) -> None:
    for jar in work.jars:
        entry = jar.entry
        if jar.download:
            if jar.current is not None:
                backup.move(jar.current, f"{entry.dir}/{jar.current.name}")
            jar.dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged[_key(entry)], jar.dest)
            (done.updated if jar.current is not None else done.added).append(entry.name)
        else:
            jar.current.rename(jar.dest)
            (done.enable if jar.dest.name == entry.name else done.disable).append(entry.name)
    for key, path in work.removals:
        backup.move(path, f"{key.partition('/')[0]}/{path.name}")
        if path.name.removesuffix(DISABLED) not in done.removed:
            done.removed.append(path.name.removesuffix(DISABLED))
    for rel, _, dest in work.configs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged[rel], dest)
        done.configs.append(rel)
    applied = {}
    for entry in m.entries:
        if _present(game, entry) is not None:
            applied[_key(entry)] = entry.sha512
    InstallState(m.pack.id, applied, sorted(work.chosen)).save(game)


def apply_update(
    game: GameDir,
    m: Manifest,
    chosen: frozenset[str] | None,
    http: Http,
    *,
    running: Callable[[], bool],
    now: Callable[[], datetime],
) -> UpdatePlan:
    """Bring the game dir to `m` with this choice; downloads are verified before anything changes."""
    if running():
        raise GameRunningError()
    work = _work(game, m, chosen)
    staging, staged = _stage(game, m, work, http)
    done = UpdatePlan(own=work.plan.own)
    try:
        if running():
            raise GameRunningError()
        _apply(game, m, work, staged, done, _Backup(game, now()))
    except OSError as error:
        if done.empty:
            raise GuardError(f"Update failed before changing anything: {error}") from error
        raise UpdateInterrupted(done, error) from error
    finally:
        if staging is not None:
            _remove(staging)
    return done

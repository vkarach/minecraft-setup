from __future__ import annotations

import filecmp
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from pathlib import Path

from minecraft_setup.engine.export import config_files, is_config, is_link, walk
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.lock import LOCK_FILE, Client, Lock, LockEntry, build_lock, locked_files
from minecraft_setup.engine.modrinth import HashMismatch, sha512_of
from minecraft_setup.engine.mods import DISABLED, MANAGED, ModFile, Profile, group_of, list_mods
from minecraft_setup.engine.profiles import (
    GameRunningError,
    GuardError,
    SwitchInterrupted,
    SwitchReport,
    plan_switch,
    refuse_unavailable,
    switch,
)

FILES_DIR = Path("pack") / "files"
_STAGING = ".restore-"


@dataclass
class SyncReport:
    entries: int = 0
    files: int = 0
    local: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass
class RestoreReport:
    downloaded: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    not_restorable: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    backup: Path | None = None
    switch: SwitchReport | None = None

    @property
    def changed(self) -> bool:
        return bool(self.downloaded or self.replaced or self.pruned or self.configs or self.switch is not None)


class RestoreInterrupted(Exception):
    """A restore failed after it changed the game dir; `report` lists what had already changed."""

    def __init__(self, report: RestoreReport, error: Exception) -> None:
        super().__init__(f"Restore stopped part-way, some changes were already made: {error}")
        self.report = report


def _refuse_while_running(running: Callable[[], bool]) -> None:
    if running():
        raise GameRunningError()


def _repo_tree(repo: Path) -> list[Path]:
    """Every entry under pack/files, refusing any link so nothing outside the repo is read or deleted."""
    root = repo / FILES_DIR
    for path in (root.parent, root):
        if is_link(path):
            raise GuardError(f"'{path}' is a link or junction - replace it with a plain folder and retry.")
    if not root.is_dir():
        return []
    entries = walk(root)
    links = [p.relative_to(root).as_posix() for p in entries if is_link(p)]
    if links:
        raise GuardError(f"'{FILES_DIR.as_posix()}/{links[0]}' is a link or junction - remove it and retry.")
    return entries


def _mirror(sources: dict[str, Path], root: Path, entries: list[Path], report: SyncReport) -> None:
    """Make `root` hold exactly `sources`, removing stale files before copying so case renames land."""
    # children come after their parents in `entries`, so reversed order empties folders first
    for path in reversed(entries):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
        elif rel not in sources:
            path.unlink()
            report.removed.append(rel)
    report.removed.sort()
    for rel, src in sources.items():
        dest = root / rel
        if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        report.copied.append(rel)


def sync(game: GameDir, repo: Path, client: Client, *, running: Callable[[], bool]) -> SyncReport:
    """Write the lock and mirror the config set into the repo."""
    _refuse_while_running(running)
    if not game.mods.is_dir():
        raise GuardError(f"No mods folder in {game.root} - is this the .minecraft folder?")
    entries = _repo_tree(repo)
    lock = build_lock(game, client)
    sources = {f.relative_to(game.root).as_posix(): f for f in config_files(game)}
    lock.save(repo / LOCK_FILE)
    local = [f"{e.dir}/{e.name}" for e in lock.entries if e.source == "local"]
    report = SyncReport(len(lock.entries), len(sources), local)
    _mirror(sources, repo / FILES_DIR, entries, report)
    return report


@dataclass(frozen=True)
class _Placement:
    entry: LockEntry
    dest: Path
    current: Path | None


class _Backup:
    """The restore's backup folder, created on first use."""

    def __init__(self, game: GameDir, stamp: datetime, report: RestoreReport) -> None:
        self.base = game.root / "backup" / f"restore-{stamp:%Y-%m-%d_%H-%M-%S}"
        self.report = report

    def _dest(self, rel: str) -> Path:
        if self.report.backup is None:
            folder = self.base
            for n in count(2):
                if not folder.exists():
                    break
                folder = self.base.with_name(f"{self.base.name}-{n}")
            self.report.backup = folder
        dest = self.report.backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    def move(self, path: Path, rel: str) -> None:
        os.replace(path, self._dest(rel))

    def copy(self, path: Path, rel: str) -> None:
        shutil.copy2(path, self._dest(rel))


def _read_lock(repo: Path) -> Lock:
    path = repo / LOCK_FILE
    if not path.is_file():
        raise GuardError(f"No lock at {path} - run sync first.")
    try:
        return Lock.load(path)
    except (OSError, ValueError) as error:
        raise GuardError(f"Cannot read {path}: {error}") from error


def _clean_staging(game: GameDir) -> None:
    """Remove download folders that an interrupted restore left behind."""
    if not game.root.is_dir():
        return
    for path in game.root.iterdir():
        if path.name.startswith(_STAGING) and path.is_dir() and not is_link(path):
            shutil.rmtree(path)


def _refuse_non_folder(game: GameDir, path: Path) -> None:
    """Refuse when a file sits where `path` or one of its parent folders must go."""
    for parent in [path, *path.parents]:
        if parent == game.root:
            return
        if parent.exists() and not parent.is_dir():
            rel = parent.relative_to(game.root).as_posix()
            raise GuardError(f"'{rel}' exists but is not a folder - move it out of the way and retry.")


def _jar_plan(game: GameDir, lock: Lock, report: RestoreReport) -> list[_Placement]:
    """Jars and packs to download: missing ones, and ones whose content differs from the lock."""
    plan = []
    for entry in lock.entries:
        folder = game.root / entry.dir
        _refuse_non_folder(game, folder)
        present = [p for p in (folder / entry.name, folder / (entry.name + DISABLED)) if p.is_file()]
        if len(present) > 1:
            raise GuardError(f"Both '{entry.name}' and '{entry.name}{DISABLED}' exist in {entry.dir} - remove one and retry.")
        current = present[0] if present else None
        if current is not None and sha512_of(current) == entry.sha512:
            continue
        if entry.source == "local":
            report.not_restorable.append(f"{entry.dir}/{entry.name}")
            continue
        if current is not None:
            dest = current
        elif entry.dir == "mods" and group_of(entry.name) in MANAGED:
            # disabled until the switch, so the guard still sees the profile being left
            dest = folder / (entry.name + DISABLED)
        else:
            dest = folder / entry.name
        if dest.exists() and not dest.is_file():
            raise GuardError(f"'{entry.dir}/{dest.name}' exists but is not a file - move it out of the way and retry.")
        plan.append(_Placement(entry, dest, current))
    return plan


def _extras(game: GameDir, lock: Lock) -> list[tuple[str, str, Path]]:
    """(dir, name, path) of jars and packs that the lock does not list."""
    keys = {e.key for e in lock.entries}
    return [(d, n, p) for d, n, p in locked_files(game) if (d, n.casefold()) not in keys]


def _config_plan(repo: Path, game: GameDir, report: RestoreReport) -> list[tuple[Path, Path, str]]:
    """(source, destination, relative name) of repo config files that differ from the game's."""
    root = repo / FILES_DIR
    plan = []
    for src in (p for p in _repo_tree(repo) if p.is_file()):
        rel = src.relative_to(root)
        if not is_config(rel):
            report.skipped.append(rel.as_posix())
            continue
        dest = game.root / rel
        _refuse_non_folder(game, dest.parent)
        if dest.exists() and not dest.is_file():
            raise GuardError(f"'{rel.as_posix()}' exists but is not a file - move it out of the way and retry.")
        if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
            continue
        plan.append((src, dest, rel.as_posix()))
    report.skipped.sort()
    return plan


def _plan(
    repo: Path, game: GameDir, lock: Lock, profile: Profile, prune: bool, report: RestoreReport
) -> tuple[list[_Placement], list[tuple[Path, Path, str]]]:
    """Every restore refusal, the switch's included, checked before anything changes."""
    extras = _extras(game, lock)
    report.extra = [f"{d}/{p.name}" for d, _, p in extras]
    jars = _jar_plan(game, lock, report)
    configs = _config_plan(repo, game, report)
    placed = [
        ModFile(p.dest, group_of(p.entry.name), not p.dest.name.lower().endswith(DISABLED))
        for p in jars
        if p.entry.dir == "mods" and p.current is None
    ]
    # the mods as the final switch will see them: pruning only happens after it
    projected = list_mods(game) + placed
    plan_switch(game, profile, projected)
    if prune:
        dropped = {n.casefold() for d, n, _ in extras if d == "mods"}
        refuse_unavailable([m for m in projected if m.name.casefold() not in dropped], profile)
    return jars, configs


def _before_change(error: OSError) -> GuardError:
    return GuardError(f"Restore failed before changing anything: {error}")


def _stage(game: GameDir, client: Client, plan: list[_Placement]) -> tuple[Path | None, list[Path]]:
    """Download and verify every jar into a staging folder, leaving the game dir untouched on failure."""
    if not plan:
        return None, []
    try:
        game.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING, dir=game.root))
    except OSError as error:
        raise _before_change(error) from error
    try:
        staged = []
        for n, placement in enumerate(plan):
            entry = placement.entry
            path = staging / str(n)
            client.download(entry.url, path, entry.sha512)
            actual = sha512_of(path)
            if actual != entry.sha512:
                raise HashMismatch(entry.url, entry.sha512, actual)
            staged.append(path)
        return staging, staged
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise _before_change(error) from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _apply(
    game: GameDir,
    lock: Lock,
    profile: Profile,
    jars: list[_Placement],
    staged: list[Path],
    configs: list[tuple[Path, Path, str]],
    prune: bool,
    report: RestoreReport,
    backup: _Backup,
    running: Callable[[], bool],
    now: Callable[[], datetime],
) -> None:
    for placement, path in zip(jars, staged):
        if placement.current is not None:
            backup.move(placement.current, f"{placement.entry.dir}/{placement.current.name}")
            report.replaced.append(placement.entry.name)
        placement.dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, placement.dest)
        report.downloaded.append(placement.entry.name)
    for src, dest, rel in configs:
        if dest.is_file():
            backup.copy(dest, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        report.configs.append(rel)
    report.switch = switch(game, profile, running=running, now=now)
    if prune:
        # re-listed after the switch, which renames managed extras like any other jar
        for folder, _, path in _extras(game, lock):
            rel = f"{folder}/{path.name}"
            backup.move(path, rel)
            report.pruned.append(rel)


def restore(
    repo: Path,
    game: GameDir,
    profile: Profile,
    client: Client,
    *,
    prune: bool,
    running: Callable[[], bool],
    now: Callable[[], datetime],
) -> RestoreReport:
    """Rebuild the pack from the repo's lock and config set, ending on `profile` through the guarded switch."""
    _refuse_while_running(running)
    report = RestoreReport()
    try:
        _clean_staging(game)
        lock = _read_lock(repo)
        jars, configs = _plan(repo, game, lock, profile, prune, report)
    except OSError as error:
        raise _before_change(error) from error

    staging, staged = _stage(game, client, jars)
    try:
        _refuse_while_running(running)
        backup = _Backup(game, now(), report)
        _apply(game, lock, profile, jars, staged, configs, prune, report, backup, running, now)
    except SwitchInterrupted as error:
        report.switch = error.report
        raise RestoreInterrupted(report, error) from error
    except GuardError as error:
        if not report.changed:
            raise
        raise RestoreInterrupted(report, error) from error
    except OSError as error:
        if not report.changed:
            raise _before_change(error) from error
        raise RestoreInterrupted(report, error) from error
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return report

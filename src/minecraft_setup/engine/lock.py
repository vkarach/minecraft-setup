from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

from minecraft_setup.engine.export import PACK_DIRS, is_pack
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.modrinth import VersionFile, sha512_of
from minecraft_setup.engine.mods import DISABLED, list_mods
from minecraft_setup.engine.profiles import GuardError

GAME_VERSION = "26.3"
LOADER = "fabric"
LOCK_FILE = Path("pack") / "lock.json"
LOCKED_DIRS = ("mods", *PACK_DIRS)
SOURCES = ("modrinth", "local")


class Client(Protocol):
    def lookup_sha512(self, hashes: list[str]) -> dict[str, VersionFile]: ...

    def find_version(self, project: str, version_number: str, loader: str, game_version: str) -> VersionFile | None: ...

    def download(self, url: str, dest: Path, sha512: str) -> None: ...


@dataclass(frozen=True)
class LockEntry:
    name: str
    dir: str
    source: Literal["modrinth", "local"]
    project_id: str | None
    version_id: str | None
    file_name: str
    url: str | None
    sha512: str

    @property
    def key(self) -> tuple[str, str]:
        # casefold like the Windows filesystem the pack lives on
        return self.dir, self.name.casefold()


@dataclass
class Lock:
    game_version: str
    loader: str
    entries: list[LockEntry]

    @classmethod
    def load(cls, path: Path) -> Lock:
        """Read and validate a lock; a bad entry raises ValueError."""
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            entries = [LockEntry(**item) for item in data["entries"]]
            lock = cls(str(data["game_version"]), str(data["loader"]), entries)
        except (KeyError, TypeError) as error:
            raise ValueError(f"{path.name} is malformed: {error}") from error
        for entry in entries:
            _validate(entry)
        return lock

    def save(self, path: Path) -> None:
        """Replace the lock file in one step so a crash never leaves it half-written."""
        data = {"game_version": self.game_version, "loader": self.loader, "entries": [asdict(e) for e in self.entries]}
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="ascii", newline="\n") as stream:
                stream.write(json.dumps(data, indent=2) + "\n")
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def _validate(entry: LockEntry) -> None:
    """Refuse entries that would write outside their folder or into the wrong one."""
    name = entry.name
    if name in ("", ".", "..") or "/" in name or "\\" in name or Path(name).name != name:
        raise ValueError(f"Lock entry name {name!r} is not a plain file name.")
    if entry.dir not in LOCKED_DIRS:
        raise ValueError(f"Lock entry {name!r} has an unknown folder {entry.dir!r}.")
    if entry.source not in SOURCES:
        raise ValueError(f"Lock entry {name!r} has an unknown source {entry.source!r}.")
    kind_ok = name.lower().endswith(".jar") if entry.dir == "mods" else is_pack(Path(entry.dir) / name)
    if not kind_ok:
        raise ValueError(f"Lock entry {name!r} is not a jar or pack archive for {entry.dir}.")
    if entry.source == "modrinth" and not entry.url:
        raise ValueError(f"Lock entry {name!r} comes from Modrinth but has no url.")


def locked_files(game: GameDir) -> list[tuple[str, str, Path]]:
    """(dir, name, path) of every jar and pack archive the lock describes."""
    found = [("mods", m.name, m.path) for m in list_mods(game)]
    for folder in PACK_DIRS:
        root = game.root / folder
        if root.is_dir():
            found += [(folder, p.name, p) for p in sorted(root.iterdir()) if p.is_file() and is_pack(Path(folder) / p.name)]
    seen = set()
    for folder, name, _ in found:
        key = (folder, name.casefold())
        if key in seen:
            raise GuardError(f"Both '{name}' and '{name}{DISABLED}' exist in {folder} - remove one and retry.")
        seen.add(key)
    return found


def build_lock(game: GameDir, client: Client) -> Lock:
    """Describe every jar and pack, identified on Modrinth by sha512; unknown ones stay `local`."""
    files = [(folder, name, sha512_of(path)) for folder, name, path in locked_files(game)]
    known = client.lookup_sha512(sorted({digest for _, _, digest in files}))
    entries = []
    for folder, name, digest in files:
        found = known.get(digest)
        if found is None:
            entries.append(LockEntry(name, folder, "local", None, None, name, None, digest))
        else:
            entries.append(
                LockEntry(name, folder, "modrinth", found.project_id, found.version_id, found.file_name, found.url, digest)
            )
    entries.sort(key=lambda e: (LOCKED_DIRS.index(e.dir), e.name))
    return Lock(GAME_VERSION, LOADER, entries)

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.modrinth import HashMismatch, VersionFile
from minecraft_setup.engine.mods import PROFILE_GROUPS, ModFile, Profile, list_mods
from minecraft_setup.engine.state import write_since

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BEFORE = T0 - timedelta(hours=1)
AFTER = T0 + timedelta(hours=1)
LATER = T0 + timedelta(days=1)

CLIENT = ["01 lib - cloth.jar", "fabric-api.jar"]
FULL = ["40 world - terralith.jar", "50 mobs - friends.jar"]
SOLO = ["60 rpg - spell engine.jar", "61 rpg [needs tl-skins-off] - wizards.jar"]


def make_jar(game: GameDir, name: str, enabled: bool = True) -> Path:
    path = game.mods / (name if enabled else name + ".disabled")
    path.touch()
    return path


def make_world(
    game: GameDir,
    name: str,
    marker: str | None = None,
    opened_at: datetime | None = None,
    where: Path | None = None,
) -> Path:
    world = (where if where is not None else game.saves) / name
    world.mkdir(parents=True)
    level = world / "level.dat"
    level.touch()
    if marker is not None:
        (world / f".{marker}-profile").touch()
    if opened_at is not None:
        stamp = opened_at.timestamp()
        os.utime(level, (stamp, stamp))
    return world


def idle() -> bool:
    return False


def busy() -> bool:
    return True


def clock() -> datetime:
    return LATER


def fill(game: GameDir, on=(), off=()) -> list[ModFile]:
    for path in list(game.mods.iterdir()):
        path.unlink()
    for name in on:
        make_jar(game, name)
    for name in off:
        make_jar(game, name, enabled=False)
    return list_mods(game)


def pack(game: GameDir, profile: Profile, solo: bool = True) -> list[ModFile]:
    """Install CLIENT, FULL and optionally SOLO jars switched to `profile`, with `since` at T0."""
    managed = FULL + (SOLO if solo else [])
    on = [n for n in managed if int(n[0]) in PROFILE_GROUPS[profile]]
    off = [n for n in managed if n not in on]
    write_since(game, T0)
    return fill(game, on=CLIENT + on, off=off)


def files(game: GameDir) -> list[str]:
    return sorted(p.name for p in game.mods.iterdir())


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def sha512(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def put(root: Path, rel: str, data: bytes | str = b"x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


class FakeModrinth:
    """An offline ModrinthClient: serves published files by hash and url."""

    def __init__(self) -> None:
        self.files: dict[str, VersionFile] = {}
        self.served: dict[str, bytes] = {}
        self.versions: dict[tuple[str, str, str, str], VersionFile] = {}
        self.lookups: list[list[str]] = []
        self.downloads: list[str] = []

    def publish(self, data: bytes, file_name: str, project: str = "proj", version: str = "1.0") -> VersionFile:
        digest = sha512(data)
        found = VersionFile(project, f"{project}-{version}", file_name, f"https://cdn.test/{project}/{file_name}", digest)
        self.files[digest] = found
        self.served[found.url] = data
        self.versions[(project, version, "fabric", "26.3")] = found
        return found

    def lookup_sha512(self, hashes: list[str]) -> dict[str, VersionFile]:
        self.lookups.append(list(hashes))
        return {h: self.files[h] for h in hashes if h in self.files}

    def find_version(self, project: str, version_number: str, loader: str, game_version: str) -> VersionFile | None:
        return self.versions.get((project, version_number, loader, game_version))

    def download(self, url: str, dest: Path, sha512_hex: str) -> None:
        self.downloads.append(url)
        data = self.served[url]
        if sha512(data) != sha512_hex:
            raise HashMismatch(url, sha512_hex, sha512(data))
        dest.write_bytes(data)


PACK_JARS = {
    "01 lib - cloth.jar": b"cloth",
    "40 world - terralith.jar": b"terralith",
    "50 mobs - friends.jar": b"friends",
    "60 rpg - spell engine.jar": b"spell",
}


def make_pack(game: GameDir, modrinth: FakeModrinth) -> None:
    """A full-profile pack: Modrinth jars, one local jar, packs, configs and a log."""
    for name, data in PACK_JARS.items():
        modrinth.publish(data, name.split(" - ")[-1], project=name)
        put(game.mods, name + (".disabled" if name.startswith("6") else ""), data)
    put(game.mods, "fabric-api.jar", b"local build")
    modrinth.publish(b"fresh", "Fresh.zip", project="fresh")
    put(game.root, "resourcepacks/Fresh.zip", b"fresh")
    modrinth.publish(b"comp", "Comp.zip", project="comp")
    put(game.root, "shaderpacks/Comp.zip", b"comp")
    put(game.root, "shaderpacks/Comp.zip.txt", "shader=on")
    put(game.root, "config/mod.json", "{}")
    put(game.root, "config/[cfg]/a.json", "{}")
    put(game.root, "defaultconfigs/d.toml", "d")
    put(game.root, "options.txt", "fov:90")
    put(game.root, "servers.dat", "s")
    put(game.root, "CustomSkinLoader/CustomSkinLoader.json", "{}")
    put(game.root, "CustomSkinLoader/CustomSkinLoader.log", "log")


def empty_game(tmp_path: Path) -> GameDir:
    root = tmp_path / "fresh" / ".minecraft"
    root.mkdir(parents=True)
    return GameDir(root)

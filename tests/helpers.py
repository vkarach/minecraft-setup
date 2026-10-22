import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.http import HashMismatch, HttpError
from minecraft_setup.engine.manifest import Manifest
from minecraft_setup.engine.modrinth import VersionFile
from minecraft_setup.engine.remote import RAW
from minecraft_setup.engine.mods import PROFILE_GROUPS, ModFile, Profile, list_mods
from minecraft_setup.engine.state import write_since

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BEFORE = T0 - timedelta(hours=1)
AFTER = T0 + timedelta(hours=1)
LATER = T0 + timedelta(days=1)

CLIENT = ["01 lib - cloth.jar", "fabric-api.jar"]
FULL = ["40 world - terralith.jar", "50 mobs - friends.jar"]


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


def pack(game: GameDir, profile: Profile) -> list[ModFile]:
    """Install CLIENT and FULL jars switched to `profile`, with `since` at T0."""
    on = [n for n in FULL if int(n[0]) in PROFILE_GROUPS[profile]]
    off = [n for n in FULL if n not in on]
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
    """An offline ModrinthClient: knows published files by hash."""

    def __init__(self) -> None:
        self.files: dict[str, VersionFile] = {}
        self.sides: dict[str, tuple[str, str]] = {}
        self.lookups: list[list[str]] = []

    def publish(self, data: bytes, file_name: str, project: str = "proj", sides=("required", "optional")) -> VersionFile:
        digest = sha512(data)
        found = VersionFile(project, f"{project}-v", file_name, f"https://cdn.test/{project}/{file_name}", digest)
        self.files[digest] = found
        self.sides[project] = sides
        return found

    def lookup_sha512(self, hashes: list[str]) -> dict[str, VersionFile]:
        self.lookups.append(list(hashes))
        return {h: self.files[h] for h in hashes if h in self.files}

    def project_sides(self, project_ids: list[str]) -> dict[str, tuple[str, str]]:
        return {p: self.sides[p] for p in project_ids if p in self.sides}


class FakeHttp:
    """An offline Http: serves bytes by url and records every request."""

    def __init__(self, served: dict[str, bytes] | None = None) -> None:
        self.served = dict(served or {})
        self.requests: list[str] = []
        self.fail: set[str] = set()

    def get(self, url: str, body=None) -> bytes:
        self.requests.append(url)
        if url in self.fail or url not in self.served:
            raise HttpError(f"404 for {url}")
        return self.served[url]

    def download(self, url: str, dest: Path, *, sha512: str | None = None, sha256: str | None = None) -> None:
        data = self.get(url)
        for algo, expected in (("sha512", sha512), ("sha256", sha256)):
            actual = hashlib.new(algo, data).hexdigest()
            if expected is not None and actual != expected:
                raise HashMismatch(url, algo, expected, actual)
        dest.write_bytes(data)


def version(root: Path, name: str, data: dict, additional: dict | None = None) -> Path:
    folder = root / "versions" / name
    folder.mkdir(parents=True)
    (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    if additional is not None:
        (folder / "TLauncherAdditional.json").write_text(json.dumps(additional), encoding="utf-8")
    return folder


def fabric_install(root: Path, name: str = "Fabric 26.3", loader: str = "0.19.5") -> Path:
    data = {"id": name, "libraries": [{"name": "org.ow2.asm:asm:9.8"}, {"name": f"net.fabricmc:fabric-loader:{loader}"}]}
    return version(root, name, data, {"jar": "26.3", "source": "local_version_repo"})


def neoforge_install(root: Path, name: str = "Solo") -> Path:
    # TLauncher wraps every argument as {"values": [...], "rules": [...]}
    game = [{"values": [v], "rules": []} for v in ("--username", "${auth_player_name}", "--fml.neoForgeVersion", "21.1.264")]
    data = {"id": name, "arguments": {"game": game}}
    return version(root, name, data, {"jar": "1.21.1", "modpack": {"name": name}})


def served_packs(*manifests: Manifest, **extra: bytes) -> FakeHttp:
    """A FakeHttp publishing these manifests under the repo's raw urls, plus `extra` urls."""
    served = {f"{RAW}/packs/index.json": json.dumps({"packs": [m.pack.id for m in manifests]}).encode()}
    served.update({f"{RAW}/packs/{m.pack.id}/manifest.json": m.dumps().encode() for m in manifests})
    return FakeHttp({**served, **extra})

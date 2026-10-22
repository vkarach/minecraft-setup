import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from minecraft_setup.engine.files import PACK_DIRS, config_files, is_link, is_pack, walk
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.http import sha512_of
from minecraft_setup.engine.installs import Install
from minecraft_setup.engine.jarmeta import read_jar
from minecraft_setup.engine.manifest import SCHEMA, SIDES, ConfigFile, Manifest, ModEntry, PackInfo, Side
from minecraft_setup.engine.modrinth import VersionFile
from minecraft_setup.engine.mods import group_of, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError
from minecraft_setup.engine.sides import classify

SECRET = re.compile(r'(?i)(access|client)_?token|password|"token"\s*:')
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# raw downloads are checked against the manifest hashes, so git must keep every byte, line endings included
_ATTRIBUTES = b"* -text\n"


class PublishError(GuardError):
    """The live game dir cannot be published as it is."""


class Client(Protocol):
    def lookup_sha512(self, hashes: list[str]) -> dict[str, VersionFile]: ...

    def project_sides(self, project_ids: list[str]) -> dict[str, tuple[str, str]]: ...


@dataclass(frozen=True)
class PublishReport:
    pack_id: str
    entries: int
    configs: int
    changed: bool


@dataclass(frozen=True)
class _Local:
    name: str
    dir: str
    path: Path
    enabled: bool


def _local_files(game: GameDir) -> list[_Local]:
    found = [_Local(mod.name, "mods", mod.path, mod.enabled) for mod in list_mods(game)]
    for folder in PACK_DIRS:
        root = game.root / folder
        if root.is_dir() and not is_link(root):
            found += [
                _Local(p.name, folder, p, True)
                for p in sorted(root.iterdir(), key=lambda p: p.name)
                if p.is_file() and is_pack(Path(folder) / p.name)
            ]
    return found


def _configs(game: GameDir) -> list[tuple[str, Path]]:
    found = [(path.relative_to(game.root).as_posix(), path) for path in config_files(game)]
    leaking = [rel for rel, path in found if SECRET.search(path.read_bytes().decode("utf-8", "ignore"))]
    if leaking:
        raise PublishError(f"These configs look like they hold a token or password: {', '.join(leaking)}.")
    return found


def _entry(local: _Local, found: VersionFile, sides, overrides: dict[str, Side], loader) -> ModEntry:
    ids: list[str] = []
    depends: list[str] = []
    if local.dir == "mods":
        try:
            meta = read_jar(local.path, loader)
        except (OSError, zipfile.BadZipFile, ValueError, KeyError, TypeError) as error:
            raise PublishError(f"Cannot read the mod info in {local.path.name}: {error}") from error
        ids, depends = sorted(meta.ids), sorted(meta.depends)
    override = overrides.get(local.name, overrides.get(found.project_id))
    side = classify(group_of(local.name), sides.get(found.project_id), override) if local.dir == "mods" else "client"
    return ModEntry(local.name, local.dir, found.project_id, found.version_id, found.url, found.sha512,
                    side, local.enabled, ids, depends)


def build_manifest(install: Install, info: PackInfo, client: Client, overrides: dict[str, Side]) -> Manifest:
    """Describe the live game dir: every jar and pack archive by its Modrinth file, plus the config set."""
    if (install.game_version, install.loader) != (info.game_version, info.loader):
        raise PublishError(
            f"'{install.name}' runs {install.loader} {install.game_version}, "
            f"but pack '{info.id}' is {info.loader} {info.game_version}."
        )
    game = GameDir(install.game_dir)
    configs = _configs(game)
    local = _local_files(game)
    hashes = {item.path: sha512_of(item.path) for item in local}
    known = client.lookup_sha512(sorted(set(hashes.values())))
    missing = [f"{item.dir}/{item.path.name}" for item in local if hashes[item.path] not in known]
    if missing:
        raise PublishError(f"No Modrinth source for: {', '.join(missing)}. Replace them with Modrinth files or remove them.")
    sides = client.project_sides(sorted({known[h].project_id for h in hashes.values()}))
    entries = [_entry(item, known[hashes[item.path]], sides, overrides, info.loader) for item in local]
    m = Manifest(
        SCHEMA,
        replace(info, loader_version=install.loader_version),
        entries,
        [ConfigFile(rel, sha512_of(path)) for rel, path in configs],
    )
    try:
        return Manifest.loads(m.dumps())
    except ValueError as error:
        raise PublishError(f"The game dir does not make a valid pack: {error}") from error


def _overrides(path: Path) -> dict[str, Side]:
    if not path.is_file():
        return {}
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise PublishError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict) or not all(isinstance(k, str) and v in SIDES for k, v in data.items()):
        raise PublishError(f"{path} must map a jar name or project id to one of {', '.join(SIDES)}.")
    return data


def _write(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def _mirror(game: GameDir, m: Manifest, files: Path) -> bool:
    """Make `files` hold exactly the manifest's configs; True when anything changed."""
    changed = False
    for config in m.configs:
        changed |= _write(files / config.path, (game.root / config.path).read_bytes())
    wanted = {files / config.path for config in m.configs}
    if files.is_dir():
        for path in reversed(walk(files)):
            if path.is_file() and path not in wanted:
                path.unlink()
                changed = True
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return changed


def _index(packs: Path, pack_id: str) -> bool:
    path = packs / "index.json"
    try:
        listed = json.loads(path.read_text(encoding="utf-8"))["packs"] if path.is_file() else []
    except (ValueError, KeyError, TypeError) as error:
        raise PublishError(f"{path} is malformed: {error}") from error
    if pack_id in listed:
        return False
    return _write(path, (json.dumps({"packs": [*listed, pack_id]}, indent=2) + "\n").encode())


def publish_pack(install: Install, info: PackInfo, repo: Path, client: Client, *, running) -> PublishReport:
    """Write the live game dir as pack `info.id` into the repo clone; git is left to `git_publish`."""
    if running():
        raise GameRunningError()
    pack = repo / "packs" / info.id
    m = build_manifest(install, info, client, _overrides(pack / "overrides.json"))
    changed = _mirror(GameDir(install.game_dir), m, pack / "files")
    changed |= _write(pack / "manifest.json", m.dumps().encode("ascii"))
    changed |= _index(repo / "packs", info.id)
    changed |= _write(repo / "packs" / ".gitattributes", _ATTRIBUTES)
    return PublishReport(info.id, len(m.entries), len(m.configs), changed)


def _is_clone(folder: Path) -> bool:
    try:
        project = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return (folder / ".git").exists() and 'name = "minecraft-setup"' in project


def find_repo(starts: list[Path] | None = None) -> Path | None:
    """The owner's clone of this repo above any start folder; None without one or without git."""
    if starts is None:
        if shutil.which("git") is None:
            return None
        starts = [Path.cwd(), Path(sys.executable).parent, Path(__file__).parent]
    for start in starts:
        for folder in [start.resolve(), *start.resolve().parents]:
            if _is_clone(folder):
                return folder
    return None


def git_publish(repo: Path, pack_id: str, run=subprocess.run) -> bool:
    """Commit and push `packs/`; False when there was nothing new to commit."""

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = run(["git", *args], cwd=repo, capture_output=True, text=True, creationflags=_NO_WINDOW)
        if check and result.returncode != 0:
            raise PublishError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}")
        return result

    git("add", "--", "packs")
    git("add", "--renormalize", "--", "packs")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard", "--", "packs").stdout.split("\n")
    if any(ignored):
        raise PublishError(f".gitignore hides published files, friends would miss them: {', '.join(filter(None, ignored))}.")
    if git("diff", "--cached", "--quiet", "--", "packs", check=False).returncode == 0:
        return False
    git("commit", "-m", f"publish {pack_id}", "--", "packs")
    git("push")
    return True

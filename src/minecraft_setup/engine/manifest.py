from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from minecraft_setup.engine.files import PACK_DIRS, is_config, is_pack
from minecraft_setup.engine.mods import group_of

SCHEMA = 1
DIRS = ("mods", *PACK_DIRS)
SIDES = ("required", "client")
LOADERS = ("fabric", "neoforge")
Side = Literal["required", "client"]
_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA512 = re.compile(r"^[0-9a-f]{128}$")
_LIBRARY = re.compile(r"^\d\d lib\b")
_BAD_CHARS = frozenset('<>:"|?*')
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"{port}{n}" for port in ("COM", "LPT") for n in range(1, 10)})


@dataclass(frozen=True)
class PackInfo:
    id: str
    title: str
    game_version: str
    loader: Literal["fabric", "neoforge"]
    loader_version: str
    profiles: bool


@dataclass(frozen=True)
class ModEntry:
    name: str
    dir: str
    project_id: str
    version_id: str
    url: str
    sha512: str
    side: Side
    default: bool
    ids: list[str]
    depends: list[str]

    @property
    def group(self) -> int | None:
        return group_of(self.name)

    @property
    def library(self) -> bool:
        return bool(_LIBRARY.match(self.name))


@dataclass(frozen=True)
class ConfigFile:
    path: str
    sha512: str


@dataclass
class Manifest:
    schema: int
    pack: PackInfo
    entries: list[ModEntry]
    configs: list[ConfigFile]

    @classmethod
    def loads(cls, text: str) -> Manifest:
        """Parse and validate a manifest; anything malformed raises ValueError."""
        try:
            data = json.loads(text)
            schema = data["schema"]
            if not isinstance(schema, int) or schema > SCHEMA:
                raise ValueError(f"schema {schema!r} comes from a newer app - update the app")
            m = cls(
                schema,
                PackInfo(**data["pack"]),
                [ModEntry(**item) for item in data["entries"]],
                [ConfigFile(**item) for item in data["configs"]],
            )
            _validate(m)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError(f"manifest is malformed: {error}") from error
        return m

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.loads(path.read_text(encoding="utf-8"))

    def dumps(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    def save(self, path: Path) -> None:
        """Replace the file in one step so a crash never leaves it half-written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="ascii", newline="\n") as stream:
                stream.write(self.dumps())
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def _check_strings(owner: str, obj: object, names: tuple[str, ...]) -> None:
    for field_name in names:
        if not isinstance(getattr(obj, field_name), str):
            raise ValueError(f"{owner} field '{field_name}' must be a string")


def _check_name(name: str) -> None:
    """Refuse names Windows cannot hold as a plain file in the target folder."""
    bad = [c for c in name if c in _BAD_CHARS or ord(c) < 32]
    if bad:
        raise ValueError(f"entry name {name!r} has a forbidden character {bad[0]!r}")
    if name.split(".")[0].rstrip(" ").upper() in _RESERVED:
        raise ValueError(f"entry name {name!r} is a reserved device name")
    if name.endswith((".", " ")):
        raise ValueError(f"entry name {name!r} has a trailing dot or space")
    if name in ("", ".", "..") or "/" in name or "\\" in name or Path(name).name != name:
        raise ValueError(f"entry name {name!r} is not a plain file name")


def _check_pack(pack: PackInfo) -> None:
    _check_strings("pack", pack, ("id", "title", "game_version", "loader", "loader_version"))
    if not _PACK_ID.match(pack.id):
        raise ValueError(f"pack id {pack.id!r} must be lowercase letters, digits, '.', '_' or '-'")
    if pack.loader not in LOADERS:
        raise ValueError(f"pack loader {pack.loader!r} is not one of {', '.join(LOADERS)}")
    if not isinstance(pack.profiles, bool):
        raise ValueError("pack profiles must be true or false")


def _check_entry(entry: ModEntry) -> None:
    _check_strings("entry", entry, ("name", "dir", "project_id", "version_id", "url", "sha512", "side"))
    name = entry.name
    _check_name(name)
    if entry.dir not in DIRS:
        raise ValueError(f"entry {name!r} has an unknown folder {entry.dir!r}")
    kind_ok = name.lower().endswith(".jar") if entry.dir == "mods" else is_pack(Path(entry.dir) / name)
    if not kind_ok:
        raise ValueError(f"entry {name!r} is not a jar or pack archive for {entry.dir}")
    if not entry.url.startswith("https://"):
        raise ValueError(f"entry {name!r} url must be https")
    if not _SHA512.match(entry.sha512):
        raise ValueError(f"entry {name!r} sha512 must be 128 lowercase hex digits")
    if entry.side not in SIDES:
        raise ValueError(f"entry {name!r} side {entry.side!r} is not one of {', '.join(SIDES)}")
    if not isinstance(entry.default, bool):
        raise ValueError(f"entry {name!r} default must be true or false")
    for field_name in ("ids", "depends"):
        value = getattr(entry, field_name)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"entry {name!r} {field_name} must be a list of strings")


def _check_config(config: ConfigFile) -> None:
    _check_strings("config", config, ("path", "sha512"))
    rel = Path(config.path)
    if "\\" in config.path or not is_config(rel):
        raise ValueError(f"config {config.path!r} is not part of the config set")
    if not _SHA512.match(config.sha512):
        raise ValueError(f"config {config.path!r} sha512 must be 128 lowercase hex digits")


def _validate(m: Manifest) -> None:
    _check_pack(m.pack)
    seen: set[tuple[str, str]] = set()
    for entry in m.entries:
        _check_entry(entry)
        key = (entry.dir, entry.name.casefold())
        if key in seen:
            raise ValueError(f"entry {entry.name!r} is listed twice in {entry.dir}")
        seen.add(key)
    for config in m.configs:
        _check_config(config)

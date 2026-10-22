import io
import json
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Loader = Literal["fabric", "neoforge"]
# provided by the game or bundled with the loader itself
BUILTIN_IDS = frozenset({"minecraft", "java", "fabricloader", "neoforge", "forge", "fabric", "mixinextras"})
_FABRIC = "fabric.mod.json"
_NEOFORGE = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
_JARJAR = "META-INF/jarjar/metadata.json"


@dataclass(frozen=True)
class JarMeta:
    ids: frozenset[str]
    depends: frozenset[str]


def _read(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        return archive.read(name).decode("utf-8-sig")
    except KeyError:
        return None


def _fabric(archive: zipfile.ZipFile, ids: set[str], depends: set[str]) -> list[str]:
    raw = _read(archive, _FABRIC)
    if raw is None:
        return []
    meta = json.loads(raw, strict=False)
    ids.add(meta["id"])
    ids.update(meta.get("provides", []))
    depends.update(meta.get("depends", {}))
    return [nested["file"] for nested in meta.get("jars", [])]


def _required(dep: dict) -> bool:
    kind = dep.get("type")
    return kind == "required" if kind is not None else bool(dep.get("mandatory", False))


def _neoforge(archive: zipfile.ZipFile, ids: set[str], depends: set[str]) -> list[str]:
    for name in _NEOFORGE:
        raw = _read(archive, name)
        if raw is None:
            continue
        meta = tomllib.loads(raw)
        ids.update(mod["modId"] for mod in meta.get("mods", []))
        for deps in meta.get("dependencies", {}).values():
            depends.update(dep["modId"] for dep in deps if _required(dep))
        break
    raw = _read(archive, _JARJAR)
    return [nested["path"] for nested in json.loads(raw).get("jars", [])] if raw else []


def _collect(archive: zipfile.ZipFile, loader: Loader, ids: set[str], depends: set[str]) -> None:
    # multi-loader jars carry both metadata files; the other loader's deps are not ours
    reader = _fabric if loader == "fabric" else _neoforge
    for nested in reader(archive, ids, depends):
        try:
            data = archive.read(nested)
        except KeyError:
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as inner:
            _collect(inner, loader, ids, depends)


def read_jar(path: Path, loader: Loader) -> JarMeta:
    """Mod ids a jar provides to `loader` (nested jars included) and the ids it requires."""
    ids: set[str] = set()
    depends: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        _collect(archive, loader, ids, depends)
    return JarMeta(frozenset(ids), frozenset(depends - BUILTIN_IDS - ids))

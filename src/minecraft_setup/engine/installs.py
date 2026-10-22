import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from minecraft_setup.engine.manifest import PackInfo

_FABRIC_LIB = "net.fabricmc:fabric-loader:"
_NEOFORGE_LIB = "net.neoforged:neoforge:"
_NEOFORGE_ARG = "--fml.neoForgeVersion"
_ADDITIONAL = "TLauncherAdditional.json"


@dataclass(frozen=True)
class Install:
    name: str
    game_dir: Path
    game_version: str
    loader: Literal["fabric", "neoforge"]
    loader_version: str


def find_minecraft() -> Path | None:
    root = Path(os.environ.get("APPDATA", "~")).expanduser() / ".minecraft"
    return root if (root / "versions").is_dir() else None


def _json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _game_args(data: dict) -> list[str]:
    args = data.get("arguments", {}).get("game", []) if isinstance(data.get("arguments"), dict) else []
    flat: list[str] = []
    for arg in args:
        # Mojang writes plain strings or {"value": ...}; TLauncher writes {"values": [...]}
        if isinstance(arg, dict):
            arg = arg.get("values", arg.get("value", []))
        flat += [arg] if isinstance(arg, str) else [a for a in arg if isinstance(a, str)]
    return flat


def _loader(data: dict) -> tuple[Literal["fabric", "neoforge"], str] | None:
    for lib in data.get("libraries", []):
        name = lib.get("name", "") if isinstance(lib, dict) else ""
        if name.startswith(_FABRIC_LIB):
            return "fabric", name[len(_FABRIC_LIB):]
        if name.startswith(_NEOFORGE_LIB):
            return "neoforge", name[len(_NEOFORGE_LIB):].split(":")[0]
    args = _game_args(data)
    if _NEOFORGE_ARG in args[:-1]:
        return "neoforge", args[args.index(_NEOFORGE_ARG) + 1]
    return None


def _install(minecraft: Path, folder: Path) -> Install | None:
    data = _json(folder / f"{folder.name}.json")
    if data is None:
        return None
    loader = _loader(data)
    if loader is None:
        return None
    extra = _json(folder / _ADDITIONAL) or {}
    game_version = extra.get("jar") or data.get("inheritsFrom")
    if not isinstance(game_version, str):
        return None
    # TLauncher modpacks keep their own game dir inside the version folder
    game_dir = folder if isinstance(extra.get("modpack"), dict) else minecraft
    return Install(folder.name, game_dir, game_version, *loader)


def find_installs(minecraft: Path) -> list[Install]:
    """Modded launcher versions under `minecraft/versions`; unreadable ones are skipped."""
    versions = minecraft / "versions"
    if not versions.is_dir():
        return []
    found = (_install(minecraft, folder) for folder in sorted(versions.iterdir(), key=lambda p: p.name))
    return [i for i in found if i is not None]


def match(pack: PackInfo, installs: list[Install]) -> Install | None:
    """The install that runs `pack`, preferring the same loader version, then name order."""
    fits = [i for i in installs if i.game_version == pack.game_version and i.loader == pack.loader]
    exact = [i for i in fits if i.loader_version == pack.loader_version]
    best = sorted(exact or fits, key=lambda i: i.name)
    return best[0] if best else None

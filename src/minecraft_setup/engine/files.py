from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.profiles import GuardError

DENY_FILES = frozenset({"tlauncherprofiles.json", "launcher_profiles.json", "usercache.json", "usernamecache.json"})
DENY_DIRS = frozenset({"logs", "crash-reports", "xaero"})
PACK_DIRS = ("resourcepacks", "shaderpacks")
# the config set, besides top-level shaderpacks/*.txt
_CONFIG_FILES = ("options.txt", "servers.dat", "CustomSkinLoader/CustomSkinLoader.json")
_CONFIG_DIRS = ("config", "defaultconfigs")


def _denied(rel: Path) -> bool:
    if rel.name.lower() in DENY_FILES or rel.suffix.lower() == ".jar":
        return True
    top = rel.parts[0].lower()
    return top in DENY_DIRS or top.startswith("saves")


def is_pack(rel: Path) -> bool:
    """A resource or shader pack archive, which the manifest describes instead of copying."""
    return len(rel.parts) == 2 and rel.parts[0].lower() in PACK_DIRS and rel.suffix.lower() == ".zip"


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def walk(root: Path) -> list[Path]:
    """Every entry below `root`, parents before children, never entering links or junctions."""
    found = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        found.append(path)
        if path.is_dir() and not is_link(path):
            found += walk(path)
    return found


def _lower(path: Path | str) -> list[str]:
    return [p.lower() for p in Path(path).parts]


def is_config(rel: Path) -> bool:
    """Whether a game-relative path belongs to the config set."""
    if not rel.parts or rel.is_absolute() or ".." in rel.parts or _denied(rel):
        return False
    parts = _lower(rel)
    if any("cache" in part for part in parts):
        return False
    if len(parts) == 2 and parts[0] == "shaderpacks" and rel.suffix.lower() == ".txt":
        return True
    if any(parts == _lower(root) for root in _CONFIG_FILES):
        return True
    return any(len(parts) > 1 and parts[0] == root for root in _CONFIG_DIRS)


def config_files(game: GameDir) -> list[Path]:
    """The game's config set as regular files; links and junctions are never followed."""
    found = []
    for root in _CONFIG_FILES:
        path = game.root / root
        if path.is_dir():
            raise GuardError(f"'{root}' is a folder but should be a file - move it out of the way and retry.")
        if path.is_file() and not is_link(path):
            found.append(path)
    for root in _CONFIG_DIRS:
        path = game.root / root
        if path.is_dir() and not is_link(path):
            found += walk(path)
    shaders = game.root / "shaderpacks"
    if shaders.is_dir() and not is_link(shaders):
        found += sorted(shaders.iterdir(), key=lambda p: p.name)
    return [f for f in found if f.is_file() and not is_link(f) and is_config(f.relative_to(game.root))]

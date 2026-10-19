import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime
from itertools import count
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import SOLO_GROUPS, list_mods
from minecraft_setup.engine.profiles import GameRunningError, GuardError

PARTS = ("mods", "config", "resourcepacks", "shaders", "options", "servers", "skins", "app")
APP_ENTRY = "minecraft-setup.exe"

_PATHS = {
    "mods": ("mods",),
    "config": ("config", "defaultconfigs"),
    "resourcepacks": ("resourcepacks",),
    "shaders": ("shaderpacks",),
    "options": ("options.txt",),
    "servers": ("servers.dat",),
    "skins": ("CustomSkinLoader/CustomSkinLoader.json", "CustomSkinLoader/LocalSkin", "CustomSkinLoader/ExtraList"),
}
# the game rewrites these on exit, so exporting them mid-session loses changes
_SAVED_ON_EXIT = frozenset({"config", "options", "servers"})
_DENY_FILES = frozenset({"tlauncherprofiles.json", "launcher_profiles.json", "usercache.json"})
_DENY_DIRS = frozenset({"logs", "crash-reports", "xaero"})


class ExportError(GuardError):
    """An export request that cannot be fulfilled; nothing was written."""


def _denied(rel: Path, *, jars_allowed: bool) -> bool:
    if rel.name.lower() in _DENY_FILES:
        return True
    if not jars_allowed and rel.suffix.lower() == ".jar":
        return True
    top = rel.parts[0].lower()
    return top in _DENY_DIRS or top.startswith("saves")


def _files(game: GameDir, part: str, skipped: set[Path]) -> Iterator[Path]:
    for rel in _PATHS[part]:
        path = game.root / rel
        if path.is_file():
            found = [path]
        elif path.is_dir():
            found = sorted(p for p in path.rglob("*") if p.is_file())
        else:
            continue
        for file in found:
            if file not in skipped and not _denied(file.relative_to(game.root), jars_allowed=part == "mods"):
                yield file


def export(
    game: GameDir,
    parts: set[str] | None,
    *,
    include_solo: bool,
    out_dir: Path,
    app_exe: Path | None,
    running: Callable[[], bool],
) -> Path:
    """Zip the chosen parts of the game dir for a friend; `None` means every part that exists."""
    if parts is not None and not parts:
        raise ExportError("No export parts chosen.")
    unknown = sorted(set(parts or ()) - set(PARTS))
    if unknown:
        raise ExportError(f"Unknown export part: {', '.join(unknown)}")
    has_exe = app_exe is not None and app_exe.is_file()
    if parts is not None and "app" in parts and not has_exe:
        raise ExportError("The 'app' part needs the built app exe, and none was found - build it first.")
    selected = [p for p in PARTS if (p in parts if parts is not None else p != "app" or has_exe)]
    if _SAVED_ON_EXIT.intersection(selected) and running():
        raise GameRunningError()

    skipped = set()
    if not include_solo:
        skipped = {m.path for m in list_mods(game) if m.group in SOLO_GROUPS}
    entries = [(f, f.relative_to(game.root).as_posix()) for p in selected if p != "app" for f in _files(game, p, skipped)]
    if "app" in selected:
        entries.append((app_exe, APP_ENTRY))

    label = "all" if parts is None else "+".join(selected)
    if include_solo:
        label += "+solo"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"modpack-{label}-{datetime.now():%Y-%m-%d_%H-%M}"
    final = out_dir / f"{stem}.zip"
    for n in count(2):
        if not final.exists():
            break
        final = out_dir / f"{stem}-{n}.zip"
    partial = out_dir / f"{final.name}.partial"
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for file, name in entries:
                archive.write(file, name)
        partial.rename(final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return final

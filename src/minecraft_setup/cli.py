import argparse
import os
import subprocess
import sys
import sysconfig
import tomllib
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from minecraft_setup.engine.export import PARTS, export
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import MANAGED, Profile, detect, list_mods
from minecraft_setup.engine.process import is_minecraft_running
from minecraft_setup.engine.profiles import GuardError, assign, switch
from minecraft_setup.engine.worlds import list_worlds

DEFAULT_GAME_DIR = Path(os.environ.get("APPDATA", "~")).expanduser() / ".minecraft"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "minecraft-setup"
SHORTCUT_NAME = "Minecraft Setup"
GUI_EXE = "minecraft-setup-gui.exe"
_MAKE_SHORTCUT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MS_LNK); "
    "$s.TargetPath = $env:MS_TARGET; $s.WorkingDirectory = $env:MS_WORKDIR; "
    "$s.Description = $env:MS_NAME; $s.Save()"
)


def _running() -> bool:
    """The game check, failing closed: an unanswerable query refuses instead of guessing."""
    try:
        return is_minecraft_running()
    except (OSError, subprocess.SubprocessError) as error:
        raise GuardError(f"Cannot tell whether Minecraft is running ({error}) - refusing to continue.") from error


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _show_worlds(game: GameDir) -> None:
    for world in list_worlds(game):
        state = "visible" if world.visible else "hidden "
        print(f"  {state}  {world.profile.value if world.profile else '-':<8} {world.name}")


def _status(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    mods = list_mods(game)
    managed = [m for m in mods if m.group in MANAGED]
    current = detect(mods)
    print(f"Current profile: {current.value if isinstance(current, Profile) else current} ({sum(m.enabled for m in managed)}/{len(managed)} managed mods enabled)")
    _show_worlds(game)
    return 0


def _profile(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    report = switch(game, Profile(args.profile), running=_running, now=_now)
    for name in report.tagged:
        print(f"  tag  world '{name}'")
    for name in report.hidden:
        print(f"  hid  world '{name}'")
    for name in report.disabled:
        print(f"  off  {name}")
    for name in report.enabled:
        print(f"  on   {name}")
    for name in report.restored:
        print(f"  back world '{name}'")
    for name in report.kept:
        print(f"  kept world '{name}': a world with this name exists in saves")
    print(f"Switched to '{args.profile}'.")
    return 0


def _assign(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    for name in assign(game, Profile(args.profile), args.worlds, running=_running):
        print(f"  tag  world '{name}' -> {args.profile}")
    _show_worlds(game)
    return 0


def _is_project(folder: Path) -> bool:
    try:
        data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("project", {}).get("name") == PROJECT_NAME


def _repo(args: argparse.Namespace) -> Path | None:
    """--repo, then MINECRAFT_SETUP_REPO, then the package's own checkout when it is this project."""
    if args.repo:
        return args.repo
    if os.environ.get("MINECRAFT_SETUP_REPO"):
        return Path(os.environ["MINECRAFT_SETUP_REPO"])
    return PACKAGE_ROOT if _is_project(PACKAGE_ROOT) else None


def _app_exe(args: argparse.Namespace) -> Path | None:
    repo = _repo(args)
    if repo is None:
        if args.parts and "app" in args.parts:
            raise GuardError("Cannot find the repo for the app exe - pass --repo or set MINECRAFT_SETUP_REPO.")
        return None
    exe = repo / "dist" / "minecraft-setup.exe"
    return exe if exe.is_file() else None


def _export(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    path = export(
        game,
        set(args.parts) if args.parts else None,
        include_solo=args.solo,
        out_dir=game.root / "exports",
        app_exe=_app_exe(args),
        running=_running,
    )
    with zipfile.ZipFile(path) as archive:
        count = len(archive.namelist())
    chosen = ", ".join(p for p in PARTS if p in args.parts) if args.parts else "all"
    print(f"Exported [{chosen}]: {count} files ({path.stat().st_size / 1024 / 1024:.1f} MB) -> {path}")
    return 0


def _gui_target() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sysconfig.get_path("scripts")) / GUI_EXE


def install_shortcut(start_menu: Path) -> Path:
    """Create the Start menu shortcut to the GUI exe that sits next to this interpreter."""
    target = _gui_target()
    if not target.is_file():
        raise GuardError(f"Cannot create the shortcut: {target} does not exist.")
    start_menu.mkdir(parents=True, exist_ok=True)
    lnk = start_menu / f"{SHORTCUT_NAME}.lnk"
    env = {
        **os.environ,
        "MS_LNK": str(lnk),
        "MS_TARGET": str(target),
        "MS_WORKDIR": str(target.parent),
        "MS_NAME": SHORTCUT_NAME,
    }
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _MAKE_SHORTCUT],
            env=env,
            check=True,
            capture_output=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GuardError(f"Could not create the shortcut: {error}") from error
    return lnk


def _install_shortcut(args: argparse.Namespace) -> int:
    print(f"Created {install_shortcut(args.start_menu)}")
    return 0


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--game-dir",
        type=Path,
        default=Path(os.environ.get("MINECRAFT_DIR") or DEFAULT_GAME_DIR),
        help="the .minecraft folder (default: %%MINECRAFT_DIR%% or %%APPDATA%%\\.minecraft)",
    )
    common.add_argument("--repo", type=Path, help="the minecraft-setup repo folder (default: MINECRAFT_SETUP_REPO)")
    return common


def _commands(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    def add(name: str, handler: Callable[[argparse.Namespace], int], help: str) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, parents=[common], help=help)
        parser.set_defaults(handler=handler)
        return parser

    add("status", _status, "show the current profile and worlds")
    profile = add("profile", _profile, "switch the mod profile")
    profile.add_argument("profile", choices=[p.value for p in Profile])
    assign_parser = add("assign", _assign, "tag untouched worlds with a profile")
    assign_parser.add_argument("profile", choices=[p.value for p in Profile])
    assign_parser.add_argument("worlds", nargs="+", metavar="world")
    export_parser = add("export", _export, "zip the modpack for a friend (worlds are never included)")
    export_parser.add_argument("--parts", nargs="+", choices=PARTS, help="parts to export (default: all)")
    export_parser.add_argument("--solo", action="store_true", help="include the solo mods (6x)")
    shortcut = add("install-shortcut", _install_shortcut, "add a Start menu shortcut to the GUI")
    shortcut.add_argument(
        "--start-menu",
        type=Path,
        default=Path(os.environ.get("APPDATA", "~")).expanduser() / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        help="folder that receives the shortcut",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minecraft-setup", description="Manage the Minecraft Fabric modpack.")
    _commands(parser.add_subparsers(dest="command", required=True), _common())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except GuardError as error:
        print(error)
        return 1

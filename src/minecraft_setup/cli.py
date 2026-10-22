import argparse
import os
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.mods import MANAGED, Profile, detect, list_mods
from minecraft_setup.engine.process import running_in
from minecraft_setup.engine.profiles import GuardError, SwitchInterrupted, SwitchReport, assign, switch
from minecraft_setup.engine.worlds import list_worlds

DEFAULT_GAME_DIR = Path(os.environ.get("APPDATA", "~")).expanduser() / ".minecraft"
SHORTCUT_NAME = "Minecraft Setup"
GUI_EXE = "minecraft-setup-gui.exe"
_MAKE_SHORTCUT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MS_LNK); "
    "$s.TargetPath = $env:MS_TARGET; $s.WorkingDirectory = $env:MS_WORKDIR; "
    "$s.Description = $env:MS_NAME; $s.Save()"
)


def _running(game: GameDir) -> Callable[[], bool]:
    """The game check for `game`, failing closed: an unanswerable query refuses instead of guessing."""

    def check() -> bool:
        try:
            return running_in(game.root)
        except (OSError, subprocess.SubprocessError) as error:
            raise GuardError(f"Cannot tell whether Minecraft is running ({error}) - refusing to continue.") from error

    return check


def _label(value: Profile | str) -> str:
    return value.value if isinstance(value, Profile) else value


def _show_worlds(game: GameDir) -> None:
    for world in list_worlds(game):
        state = "visible" if world.visible else "hidden "
        print(f"  {state}  {_label(world.profile) if world.profile else '-':<8} {world.name}")


def _header(game: GameDir) -> Profile | str:
    mods = list_mods(game)
    managed = [m for m in mods if m.group in MANAGED]
    current = detect(mods)
    print(f"Current profile: {_label(current)} ({sum(m.enabled for m in managed)}/{len(managed)} managed mods enabled)")
    return current


def _world_info(game: GameDir) -> dict[str, tuple[str, str]]:
    """World name -> (profile marker or '-', folder it sits in), read after the change."""
    return {w.name: (_label(w.profile) if w.profile else "-", w.path.parent.name) for w in list_worlds(game)}


def _print_report(game: GameDir, report: SwitchReport, target: Profile) -> None:
    info = _world_info(game)
    for name in report.tagged:
        print(f"  tag  world '{name}' -> {info.get(name, ('?',))[0]}")
    for name in report.hidden:
        print(f"  hid  world '{name}' ({info.get(name, ('?',))[0]})")
    for name in report.disabled:
        print(f"  off  {name}")
    for name in report.enabled:
        print(f"  on   {name}")
    for name in report.relocated:
        print(f"  moved world '{name}' to {info.get(name, ('?', '?'))[1]}")
    for name in report.restored:
        print(f"  back world '{name}'")
    for name in report.kept:
        print(f"  kept world '{name}' in {game.vault(target).name}: a world with this name exists in saves")


def _status(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    _header(game)
    _show_worlds(game)
    return 0


def _profile(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    target = Profile(args.profile)
    current = _header(game)
    try:
        report = switch(game, target, running=_running(game))
    except SwitchInterrupted as error:
        _print_report(game, error.report, target)
        print(error)
        return 1
    _print_report(game, report, target)
    print(f"Already on '{target.value}', nothing else to do." if current == target else f"Switched to '{target.value}'.")
    return 0


def _assign(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    profile = Profile(args.profile)
    _header(game)
    owned = {w.name.casefold() for w in list_worlds(game) if w.profile == profile}
    try:
        tagged = assign(game, profile, args.worlds, running=_running(game))
    except SwitchInterrupted as error:
        _print_report(game, error.report, profile)
        print(error)
        return 1
    for name in args.worlds:
        if name.casefold() in owned:
            print(f"  '{name}' already belongs to {profile.value}")
    for name in tagged:
        print(f"  tag  world '{name}' -> {profile.value}")
    _show_worlds(game)
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

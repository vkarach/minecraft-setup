import argparse
import os
import subprocess
import sys
import sysconfig
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path

from minecraft_setup.engine.export import PARTS, export
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.lock import LOCK_FILE
from minecraft_setup.engine.modrinth import ModrinthClient, ModrinthError
from minecraft_setup.engine.mods import MANAGED, Profile, detect, list_mods
from minecraft_setup.engine.process import is_minecraft_running
from minecraft_setup.engine.profiles import GuardError, SwitchInterrupted, SwitchReport, assign, local_now, switch
from minecraft_setup.engine.sync import FILES_DIR, RestoreInterrupted, RestoreReport, restore, sync
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
        report = switch(game, target, running=_running)
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
        tagged = assign(game, profile, args.worlds, running=_running)
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
    exe = _app_exe(args)
    path = export(
        game,
        set(args.parts) if args.parts else None,
        include_solo=args.solo,
        out_dir=game.root / "exports",
        app_exe=exe,
        running=_running,
    )
    if not args.parts and exe is None:
        print("App exe not included: dist/minecraft-setup.exe was not found - build it and pass --repo <repo> (or set MINECRAFT_SETUP_REPO).")
    with zipfile.ZipFile(path) as archive:
        count = len(archive.namelist())
    chosen = ", ".join(p for p in PARTS if p in args.parts) if args.parts else "all"
    print(f"Exported [{chosen}]: {count} files ({path.stat().st_size / 1024 / 1024:.1f} MB) -> {path}")
    return 0


def _need_repo(args: argparse.Namespace) -> Path:
    repo = _repo(args)
    if repo is None:
        raise GuardError("Cannot find the repo - pass --repo or set MINECRAFT_SETUP_REPO.")
    return repo


def _sync(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    repo = _need_repo(args)
    report = sync(game, repo, ModrinthClient(), running=_running)
    print(f"Locked {report.entries} entries ({len(report.local)} local) -> {repo / LOCK_FILE}")
    for rel in report.local:
        print(f"  local {rel} - not on Modrinth, restore cannot download it")
    for rel in report.copied:
        print(f"  copy {rel}")
    for rel in report.removed:
        print(f"  del  {rel}")
    files = repo / FILES_DIR
    print(f"Config set: {report.files} files in {files} ({len(report.copied)} copied, {len(report.removed)} removed).")
    return 0


def _print_restore(game: GameDir, report: RestoreReport, target: Profile) -> None:
    replaced = set(report.replaced)
    for name in report.downloaded:
        if name not in replaced:
            print(f"  get  {name}")
    for name in report.replaced:
        print(f"  repl {name} (the old copy is in the backup)")
    for rel in report.not_restorable:
        print(f"  lost {rel} - not on Modrinth, not restorable: copy it in by hand")
    pruned = set(report.pruned)
    for rel in report.extra:
        if rel in pruned:
            print(f"  prune {rel}")
        else:
            print(f"  extra {rel} - not in the lock, left alone (--prune moves it to backup)")
    for rel in report.skipped:
        print(f"  skip {rel} - not part of the config set")
    for rel in report.configs:
        print(f"  cfg  {rel}")
    if report.backup is not None:
        print(f"  backup {report.backup}")
    if report.switch is not None:
        _print_report(game, report.switch, target)


def _restore(args: argparse.Namespace) -> int:
    game = GameDir(args.game_dir)
    target = Profile(args.profile)
    repo = _need_repo(args)
    _header(game)
    try:
        report = restore(repo, game, target, ModrinthClient(), prune=args.prune, running=_running, now=local_now)
    except RestoreInterrupted as error:
        _print_restore(game, error.report, target)
        print(error)
        return 1
    _print_restore(game, report, target)
    print(f"Restored onto '{target.value}'.")
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
    add("sync", _sync, "write the mod lock and mirror the config set into the repo")
    restore_parser = add("restore", _restore, "rebuild the pack from the repo, ending on a profile")
    restore_parser.add_argument("profile", choices=[p.value for p in Profile])
    restore_parser.add_argument("--prune", action="store_true", help="move jars not in the lock to backup")
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
    except (GuardError, ModrinthError) as error:
        print(error)
        return 1

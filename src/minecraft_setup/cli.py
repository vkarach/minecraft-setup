import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from minecraft_setup import __version__
from minecraft_setup.engine import selfupdate
from minecraft_setup.engine.apply import InstallState, UpdateInterrupted, UpdatePlan, apply_update, plan_update
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.http import Http, HttpError, UrllibHttp
from minecraft_setup.engine.installs import Install, find_installs, find_minecraft, match, needs
from minecraft_setup.engine.manifest import Manifest, PackInfo
from minecraft_setup.engine.modrinth import ModrinthClient
from minecraft_setup.engine.mods import MANAGED, Profile, detect, list_mods
from minecraft_setup.engine.process import running_check, running_in
from minecraft_setup.engine.profiles import GuardError, SwitchInterrupted, SwitchReport, assign, local_now, switch
from minecraft_setup.engine.publish import Client, find_repo, git_publish, publish_pack
from minecraft_setup.engine.remote import CACHE, RemoteError, load_packs
from minecraft_setup.engine.selection import choosable, default_choice
from minecraft_setup.engine.worlds import list_worlds

@dataclass
class Services:
    http: Http
    modrinth: Client
    cache: Path
    repo: Path | None


def services() -> Services:
    http = UrllibHttp()
    return Services(http, ModrinthClient(http), CACHE, find_repo())


def _running(game: GameDir) -> Callable[[], bool]:
    return running_check(game.root, running_in)


@dataclass
class _Context:
    services: Services
    minecraft: Path
    packs: list[Manifest]
    offline: bool
    installs: list[Install]

    def manifest(self, pack_id: str) -> Manifest:
        found = next((m for m in self.packs if m.pack.id == pack_id), None)
        if found is None:
            raise GuardError(f"No pack '{pack_id}'. Known packs: {', '.join(m.pack.id for m in self.packs)}.")
        return found

    def game(self, m: Manifest) -> GameDir:
        install = match(m.pack, self.installs)
        if install is None:
            raise GuardError(f"Pack '{m.pack.id}' is not installed: install {needs(m.pack)} in TLauncher.")
        return GameDir(install.game_dir)

    def profile_pack(self, pack_id: str | None) -> Manifest:
        if pack_id is not None:
            m = self.manifest(pack_id)
            if not m.pack.profiles:
                raise GuardError(f"Pack '{pack_id}' has no profiles.")
            return m
        with_profiles = [m for m in self.packs if m.pack.profiles]
        if len(with_profiles) != 1:
            raise GuardError("Name the pack with --pack.")
        return with_profiles[0]


def _context(args: argparse.Namespace) -> _Context:
    minecraft = args.minecraft or find_minecraft()
    if minecraft is None:
        raise GuardError("No .minecraft folder with versions found - pass --minecraft PATH.")
    svc = services()
    try:
        packs, offline = load_packs(svc.http, svc.cache)
    except RemoteError as error:
        raise GuardError(f"{error} There is no saved copy to fall back on.") from error
    if offline:
        print("Offline: showing the packs as they were last loaded.")
    return _Context(svc, minecraft, packs, offline, find_installs(minecraft))


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


def _print_plan(plan: UpdatePlan) -> None:
    rows = [("add", plan.added), ("upd", plan.updated), ("del", plan.removed), ("on ", plan.enable),
            ("off", plan.disable), ("cfg", plan.configs), ("own", plan.own)]
    for tag, names in rows:
        for name in names:
            print(f"  {tag}  {name}")


def _packs(args: argparse.Namespace) -> int:
    ctx = _context(args)
    for m in ctx.packs:
        install = match(m.pack, ctx.installs)
        where = install.name if install else f"not installed: install {needs(m.pack)}"
        print(f"{m.pack.id}  {m.pack.title} -> {where}")
    return 0


def _apply(ctx: _Context, m: Manifest, game: GameDir, chosen: frozenset[str] | None) -> int:
    try:
        done = apply_update(game, m, chosen, ctx.services.http, running=_running(game), now=local_now)
    except UpdateInterrupted as error:
        _print_plan(error.done)
        print(error)
        return 1
    _print_plan(done)
    print("Done.")
    return 0


def _update(args: argparse.Namespace) -> int:
    ctx = _context(args)
    m = ctx.manifest(args.pack)
    game = ctx.game(m)
    plan = plan_update(game, m, None)
    _print_plan(plan)
    if plan.empty:
        print("Up to date.")
        return 0
    if not args.yes:
        print("Run again with --yes to apply.")
        return 0
    return _apply(ctx, m, game, None)


def _choose(args: argparse.Namespace) -> int:
    ctx = _context(args)
    m = ctx.manifest(args.pack)
    game = ctx.game(m)
    names = {e.name for e in choosable(m)}
    unknown = sorted(set(args.on + args.off) - names)
    if unknown:
        raise GuardError(f"Not an optional mod of '{m.pack.id}': {', '.join(unknown)}.")
    state = InstallState.load(game)
    chosen = frozenset(state.chosen) if state and state.chosen is not None else default_choice(m)
    if not args.on and not args.off:
        for name in sorted(names):
            print(f"  [{'x' if name in chosen else ' '}] {name}")
        return 0
    return _apply(ctx, m, game, (chosen | set(args.on)) - set(args.off))


def _status(args: argparse.Namespace) -> int:
    ctx = _context(args)
    game = ctx.game(ctx.profile_pack(args.pack))
    _header(game)
    _show_worlds(game)
    return 0


def _profile(args: argparse.Namespace) -> int:
    ctx = _context(args)
    game = ctx.game(ctx.profile_pack(args.pack))
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
    ctx = _context(args)
    game = ctx.game(ctx.profile_pack(args.pack))
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


def _publish(args: argparse.Namespace) -> int:
    repo = args.repo or services().repo
    if repo is None:
        raise GuardError("No local clone of the minecraft-setup repo with git found - pass --repo PATH.")
    ctx = _context(args)
    local = repo / "packs" / args.pack / "manifest.json"
    if local.is_file():
        info = Manifest.load(local).pack
    elif args.title is None:
        raise GuardError(f"Pack '{args.pack}' is new - pass --title and --install to describe it.")
    else:
        info = None
    install = _publish_install(ctx, args, info)
    if info is None:
        info = PackInfo(args.pack, args.title, install.game_version, install.loader, install.loader_version, args.profiles)
    report = publish_pack(install, info, repo, ctx.services.modrinth, running=_running(GameDir(install.game_dir)))
    print(f"Wrote pack '{report.pack_id}': {report.entries} files, {report.configs} configs.")
    if args.no_push:
        return 0
    print("Pushed." if git_publish(repo, report.pack_id) else "Nothing new to push.")
    return 0


def _publish_install(ctx: _Context, args: argparse.Namespace, info: PackInfo | None) -> Install:
    if args.install is not None:
        found = next((i for i in ctx.installs if i.name == args.install), None)
        if found is None:
            raise GuardError(f"No TLauncher version '{args.install}'. Found: {', '.join(i.name for i in ctx.installs)}.")
        return found
    found = match(info, ctx.installs) if info is not None else None
    if found is None:
        raise GuardError(f"Cannot tell which TLauncher version holds '{args.pack}' - pass --install NAME.")
    return found


def _self_update(args: argparse.Namespace) -> int:
    http = services().http
    release = selfupdate.latest_release(http)
    if release is None or not selfupdate.newer(__version__, release.version):
        print(f"Up to date ({__version__}).")
        return 0
    if not getattr(sys, "frozen", False):
        raise GuardError(f"{release.version} is out; update the source checkout instead (self-update replaces the exe).")
    exe = Path(sys.executable)
    selfupdate.cleanup(exe)
    selfupdate.stage(release, exe, http)
    selfupdate.swap(exe)
    print(f"Updated to {release.version}. Start the app again.")
    return 0


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--minecraft", type=Path, help="the .minecraft folder (default: found automatically)")
    return common


def _commands(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    def add(name: str, handler: Callable[[argparse.Namespace], int], help: str) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, parents=[common], help=help)
        parser.set_defaults(handler=handler)
        return parser

    add("packs", _packs, "list the published packs and the installs they run in")
    update = add("update", _update, "show what a pack update changes; apply it with --yes")
    update.add_argument("pack")
    update.add_argument("--yes", action="store_true", help="apply the update")
    choose = add("choose", _choose, "list or change the optional mods of a pack")
    choose.add_argument("pack")
    choose.add_argument("--on", nargs="+", default=[], metavar="NAME")
    choose.add_argument("--off", nargs="+", default=[], metavar="NAME")
    for name, handler, help in (("status", _status, "show the current profile and worlds"),
                                ("profile", _profile, "switch the mod profile"),
                                ("assign", _assign, "tag untouched worlds with a profile")):
        parser = add(name, handler, help)
        if name != "status":
            parser.add_argument("profile", choices=[p.value for p in Profile])
        if name == "assign":
            parser.add_argument("worlds", nargs="+", metavar="world")
        parser.add_argument("--pack", help="the pack with profiles (default: the only one)")
    publish = add("publish", _publish, "write a pack from this game dir into the repo, then commit and push")
    publish.add_argument("pack")
    publish.add_argument("--repo", type=Path, help="the minecraft-setup clone (default: found automatically)")
    publish.add_argument("--install", help="the TLauncher version to read (default: the one matching the pack)")
    publish.add_argument("--title", help="title of a new pack")
    publish.add_argument("--profiles", action="store_true", help="a new pack uses the vanilla/full profiles")
    publish.add_argument("--no-push", action="store_true", help="write the files only")
    add("self-update", _self_update, "replace this exe with the latest release")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minecraft-setup", description="Install and update the Minecraft modpacks.")
    _commands(parser.add_subparsers(dest="command", required=True), _common())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (GuardError, RemoteError, HttpError) as error:
        print(error)
        return 1

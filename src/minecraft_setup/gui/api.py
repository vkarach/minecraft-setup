import functools
import subprocess
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from minecraft_setup import __version__
from minecraft_setup.engine import selfupdate
from minecraft_setup.engine.apply import InstallState, apply_update, plan_update
from minecraft_setup.engine.gamedir import GameDir
from minecraft_setup.engine.http import Http, HttpError
from minecraft_setup.engine.installs import Install, find_installs, match, needs
from minecraft_setup.engine.manifest import Manifest
from minecraft_setup.engine.mods import Profile, detect, list_mods
from minecraft_setup.engine.process import running_check, running_in
from minecraft_setup.engine.profiles import GuardError, assign, local_now, switch
from minecraft_setup.engine.publish import Client, git_publish, publish_pack
from minecraft_setup.engine.remote import RemoteError, load_packs
from minecraft_setup.engine.selection import choosable, default_choice, needed_by, resolve
from minecraft_setup.engine.worlds import list_worlds

_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _message(error: BaseException) -> str:
    if isinstance(error, (GuardError, RemoteError, HttpError)):
        return str(error)
    return f"{type(error).__name__}: {error}"


def _safe(method: Callable[..., dict]) -> Callable[..., dict]:
    """JS gets {"error": ...} instead of an exception it cannot read."""

    @functools.wraps(method)
    def wrapper(self, *args: Any) -> dict:
        try:
            return method(self, *args)
        except Exception as error:
            return {"error": _message(error)}

    return wrapper


def _worlds(game: GameDir, current: Profile | str) -> list[dict]:
    """Worlds with where they stand; own worlds left in the active vault come back on a switch to it."""
    worlds = list_worlds(game)
    in_saves = {w.name.casefold() for w in worlds if w.visible}
    found = []
    for w in worlds:
        state = "visible" if w.visible else "hidden"
        if not w.visible and isinstance(current, Profile) and w.path.parent == game.vault(current):
            if w.profile in (current, None):
                state = "stuck" if w.name.casefold() in in_saves else "restorable"
        found.append({"name": w.name, "profile": w.profile.value if w.profile else None, "state": state})
    return found


class Api:
    """What the window's JS calls; names starting with '_' stay hidden from it."""

    def __init__(
        self,
        minecraft: Path | None,
        http: Http,
        modrinth: Client,
        repo: Path | None,
        exe: Path | None,
        cache: Path,
        probe: Callable[[Path], bool] = running_in,
    ) -> None:
        self._minecraft = minecraft
        self._http = http
        self._modrinth = modrinth
        self._repo = repo
        self._exe = exe
        self._cache = cache
        self._probe = probe
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._jobs: dict[str, Future] = {}
        self._choices: dict[str, frozenset[str]] = {}
        self._packs: list[Manifest] | None = None
        self._offline = False
        self._error: str | None = None
        self._release: selfupdate.Release | None = None
        self._quit: Callable[[], None] | None = None

    def _load(self) -> None:
        try:
            self._packs, self._offline = load_packs(self._http, self._cache)
            self._error = None
        except RemoteError as error:
            self._packs, self._offline, self._error = [], True, str(error)
        self._release = None
        if self._exe is not None and not self._offline:
            try:
                found = selfupdate.latest_release(self._http)
            except HttpError:
                found = None
            if found is not None and selfupdate.newer(__version__, found.version):
                self._release = found

    def _all(self) -> list[Manifest]:
        if self._packs is None:
            self._load()
        return self._packs

    def _installs(self) -> list[Install]:
        return find_installs(self._minecraft) if self._minecraft is not None else []

    def _manifest(self, pack_id: str) -> Manifest:
        found = next((m for m in self._all() if m.pack.id == pack_id), None)
        if found is None:
            raise GuardError(f"No pack '{pack_id}'.")
        return found

    def _game(self, m: Manifest) -> GameDir:
        install = match(m.pack, self._installs())
        if install is None:
            raise GuardError(f"Install {needs(m.pack)} in TLauncher first.")
        return GameDir(install.game_dir)

    def _profile_game(self) -> GameDir:
        with_profiles = [m for m in self._all() if m.pack.profiles]
        if len(with_profiles) != 1:
            raise GuardError("No single pack with profiles.")
        return self._game(with_profiles[0])

    def _chosen(self, m: Manifest, game: GameDir) -> frozenset[str]:
        if m.pack.id in self._choices:
            return self._choices[m.pack.id]
        state = InstallState.load(game)
        return frozenset(state.chosen) if state and state.chosen is not None else default_choice(m)

    def _running(self, game: GameDir) -> Callable[[], bool]:
        return running_check(game.root, self._probe)

    def _pack_state(self, m: Manifest, installs: list[Install]) -> dict:
        install = match(m.pack, installs)
        view: dict[str, Any] = {
            "id": m.pack.id, "title": m.pack.title, "install": install.name if install else None,
            "needs": None if install else needs(m.pack), "mods": [], "update": None, "own": [],
            "problem": None, "profile": None, "worlds": [], "restorable": [], "pending": m.pack.id in self._choices,
        }
        try:
            game = GameDir(install.game_dir) if install else None
            chosen = self._chosen(m, game) if game else default_choice(m)
            enabled = resolve(m, chosen)
            options = {e.name for e in choosable(m)}
            users = needed_by(m, enabled)
            view["mods"] = [
                {"name": e.name, "group": e.group, "side": e.side, "library": e.library,
                 "enabled": e.name in enabled, "choosable": e.name in options, "needed_by": users.get(e.name, [])}
                for e in m.entries if e.dir == "mods"
            ]
            if game is None:
                return view
            if m.pack.profiles:
                current = detect(list_mods(game))
                view["profile"] = current.value if isinstance(current, Profile) else current
                view["worlds"] = _worlds(game, current)
                view["restorable"] = [w["name"] for w in view["worlds"] if w["state"] == "restorable"]
            plan = plan_update(game, m, chosen)
            view["own"] = plan.own
            view["update"] = None if plan.empty else asdict(plan)
        except GuardError as error:
            view["problem"] = str(error)
        return view

    def _submit(self, work: Callable[[], Any]) -> dict:
        job = uuid.uuid4().hex
        self._jobs[job] = self._pool.submit(work)
        return {"job": job}

    @_safe
    def state(self) -> dict:
        packs = self._all()
        installs = self._installs()
        return {
            "version": __version__, "offline": self._offline, "error": self._error,
            "minecraft": str(self._minecraft) if self._minecraft else None,
            "app_update": self._release.version if self._release else None,
            "owner": self._repo is not None,
            "packs": [self._pack_state(m, installs) for m in packs],
        }

    @_safe
    def refresh(self) -> dict:
        self._load()
        return self.state()

    @_safe
    def job(self, job_id: str) -> dict:
        future = self._jobs.get(job_id)
        if future is None:
            return {"done": True, "error": "Unknown job.", "result": None}
        if not future.done():
            return {"done": False, "error": None, "result": None}
        error = future.exception()
        self._jobs.pop(job_id)
        if error is not None:
            return {"done": True, "error": _message(error), "result": None}
        return {"done": True, "error": None, "result": future.result()}

    @_safe
    def set_choice(self, pack_id: str, name: str, on: bool) -> dict:
        m = self._manifest(pack_id)
        if name not in {e.name for e in choosable(m)}:
            raise GuardError(f"'{name}' is not an optional mod.")
        current = self._chosen(m, self._game(m))
        self._choices[pack_id] = current | {name} if on else current - {name}
        return {"ok": True}

    @_safe
    def apply(self, pack_id: str) -> dict:
        m = self._manifest(pack_id)
        game = self._game(m)
        chosen = self._choices.get(pack_id)

        def work() -> dict:
            done = apply_update(game, m, chosen, self._http, running=self._running(game), now=local_now)
            self._choices.pop(pack_id, None)
            return asdict(done)

        return self._submit(work)

    @_safe
    def switch_profile(self, profile: str) -> dict:
        game = self._profile_game()
        target = Profile(profile)
        return self._submit(lambda: asdict(switch(game, target, running=self._running(game))))

    @_safe
    def assign(self, profile: str, names: list[str]) -> dict:
        game = self._profile_game()
        target = Profile(profile)
        return self._submit(lambda: {"tagged": assign(game, target, names, running=self._running(game))})

    @_safe
    def publish(self, pack_id: str) -> dict:
        if self._repo is None:
            raise GuardError("Publishing needs a local clone of the repo.")
        repo = self._repo
        local = repo / "packs" / pack_id / "manifest.json"
        info = Manifest.load(local).pack if local.is_file() else self._manifest(pack_id).pack
        install = match(info, self._installs())
        if install is None:
            raise GuardError(f"Install {needs(info)} in TLauncher first.")

        def work() -> dict:
            game = GameDir(install.game_dir)
            report = publish_pack(install, info, repo, self._modrinth, running=self._running(game))
            return {**asdict(report), "pushed": git_publish(repo, pack_id)}

        return self._submit(work)

    @_safe
    def update_app(self) -> dict:
        if self._exe is None or self._release is None:
            raise GuardError("No app update to install.")
        exe, release = self._exe, self._release

        def work() -> dict:
            selfupdate.stage(release, exe, self._http)
            selfupdate.swap(exe)
            subprocess.Popen([str(exe)], creationflags=_DETACHED, close_fds=True)
            if self._quit is not None:
                self._quit()
            return {"version": release.version}

        return self._submit(work)

import json
import os
import urllib.parse
from pathlib import Path

from minecraft_setup.engine.http import Http, HttpError
from minecraft_setup.engine.manifest import Manifest

RAW = "https://raw.githubusercontent.com/vkarach/minecraft-setup/main"
CACHE = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "minecraft-setup"


class RemoteError(Exception):
    """The published packs could not be read."""


def fetch_index(http: Http) -> list[str]:
    url = f"{RAW}/packs/index.json"
    try:
        packs = json.loads(http.get(url))["packs"]
    except (HttpError, ValueError, KeyError, TypeError) as error:
        raise RemoteError(f"Cannot read the pack index {url}: {error}") from error
    if not isinstance(packs, list) or not all(isinstance(p, str) for p in packs):
        raise RemoteError(f"The pack index {url} is malformed.")
    return packs


def fetch_manifest(http: Http, pack_id: str) -> Manifest:
    url = f"{RAW}/packs/{urllib.parse.quote(pack_id)}/manifest.json"
    try:
        m = Manifest.loads(http.get(url).decode("utf-8"))
    except (HttpError, ValueError) as error:
        raise RemoteError(f"Cannot read pack '{pack_id}' from {url}: {error}") from error
    if m.pack.id != pack_id:
        raise RemoteError(f"{url} describes pack '{m.pack.id}', not '{pack_id}'.")
    return m


def config_url(pack_id: str, path: str) -> str:
    return f"{RAW}/packs/{urllib.parse.quote(pack_id)}/files/{urllib.parse.quote(path)}"


def _cached(folder: Path) -> list[Manifest]:
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            found.append(Manifest.load(path))
        except (OSError, ValueError):
            continue
    return found


def load_packs(http: Http, cache: Path) -> tuple[list[Manifest], bool]:
    """The published packs, or the copies saved by the last successful load; True means offline."""
    folder = cache / "packs"
    try:
        packs = [fetch_manifest(http, pack_id) for pack_id in fetch_index(http)]
    except RemoteError:
        found = _cached(folder)
        if not found:
            raise
        order = {pack_id: n for n, pack_id in enumerate(_order(cache))}
        return sorted(found, key=lambda m: order.get(m.pack.id, len(order))), True
    for m in packs:
        m.save(folder / f"{m.pack.id}.json")
    wanted = {f"{m.pack.id}.json" for m in packs}
    for stale in folder.glob("*.json") if folder.is_dir() else []:
        if stale.name not in wanted:
            stale.unlink(missing_ok=True)
    (cache / "index.json").write_text(json.dumps([m.pack.id for m in packs]), encoding="ascii")
    return packs, False


def _order(cache: Path) -> list[str]:
    try:
        return [str(pack_id) for pack_id in json.loads((cache / "index.json").read_text(encoding="ascii"))]
    except (OSError, ValueError, TypeError):
        return []

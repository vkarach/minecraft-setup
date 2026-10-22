import json
import urllib.parse

from minecraft_setup.engine.http import Http, HttpError
from minecraft_setup.engine.manifest import Manifest

RAW = "https://raw.githubusercontent.com/vkarach/minecraft-setup/main"


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

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from minecraft_setup.engine.http import Http, HttpError

API = "https://api.modrinth.com/v2"
_BATCH = 100


class ModrinthError(HttpError):
    """Modrinth answered with something this client cannot read."""


@dataclass(frozen=True)
class VersionFile:
    project_id: str
    version_id: str
    file_name: str
    url: str
    sha512: str


def _version_file(version: dict[str, Any], sha512: str | None = None) -> VersionFile | None:
    """The file of `version` with this hash, or its primary (else first) file."""
    files = version.get("files") or []
    if sha512 is not None:
        chosen = next((f for f in files if f["hashes"].get("sha512") == sha512), None)
    else:
        chosen = next((f for f in files if f.get("primary")), files[0] if files else None)
    if chosen is None:
        return None
    return VersionFile(version["project_id"], version["id"], chosen["filename"], chosen["url"], chosen["hashes"]["sha512"])


class ModrinthClient:
    """Modrinth API v2 over an Http."""

    def __init__(self, http: Http, api: str = API) -> None:
        self.http = http
        self.api = api

    def _json(self, path: str, body: Any = None) -> Any:
        url = f"{self.api}{path}"
        try:
            return json.loads(self.http.get(url, body))
        except ValueError as error:
            raise ModrinthError(f"Modrinth answered {url} with bad JSON: {error}") from error

    def lookup_sha512(self, hashes: list[str]) -> dict[str, VersionFile]:
        """Versions that Modrinth knows by file hash; unknown hashes are absent."""
        if not hashes:
            return {}
        found = self._json("/version_files", {"hashes": list(hashes), "algorithm": "sha512"})
        try:
            files = {digest: _version_file(version, digest) for digest, version in found.items()}
        except (AttributeError, KeyError, TypeError) as error:
            raise ModrinthError(f"Unexpected Modrinth answer to the hash lookup: {error!r}") from error
        return {digest: file for digest, file in files.items() if file is not None}

    def find_version(self, project: str, version_number: str, loader: str, game_version: str) -> VersionFile | None:
        """The primary file of that exact version; None when no version matches."""
        query = urllib.parse.urlencode({"loaders": json.dumps([loader]), "game_versions": json.dumps([game_version])})
        versions = self._json(f"/project/{urllib.parse.quote(project, safe='')}/version?{query}")
        try:
            version = next((v for v in versions if v.get("version_number") == version_number), None)
            return None if version is None else _version_file(version)
        except (AttributeError, KeyError, TypeError) as error:
            raise ModrinthError(f"Unexpected Modrinth answer for {project}: {error!r}") from error

    def project_sides(self, project_ids: list[str]) -> dict[str, tuple[str, str]]:
        """(client_side, server_side) per project id, as the mod authors declared them."""
        sides: dict[str, tuple[str, str]] = {}
        for start in range(0, len(project_ids), _BATCH):
            batch = project_ids[start : start + _BATCH]
            found = self._json(f"/projects?{urllib.parse.urlencode({'ids': json.dumps(batch)})}")
            try:
                sides.update({p["id"]: (p["client_side"], p["server_side"]) for p in found})
            except (KeyError, TypeError) as error:
                raise ModrinthError(f"Unexpected Modrinth answer to the project lookup: {error!r}") from error
        return sides

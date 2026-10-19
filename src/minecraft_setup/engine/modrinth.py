import hashlib
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minecraft_setup import __version__

API = "https://api.modrinth.com/v2"
# Modrinth asks every client for a User-Agent that names the project; nothing personal goes out
USER_AGENT = f"minecraft-setup/{__version__} (github.com/vkarach/minecraft-setup)"
_CHUNK = 1 << 20


class ModrinthError(Exception):
    """A Modrinth request or download failed; nothing was written to its destination."""


class HashMismatch(ModrinthError):
    def __init__(self, url: str, expected: str, actual: str) -> None:
        super().__init__(f"sha512 mismatch for {url}: expected {expected[:16]}..., got {actual[:16]}...")
        self.url = url


@dataclass(frozen=True)
class VersionFile:
    project_id: str
    version_id: str
    file_name: str
    url: str
    sha512: str


def sha512_of(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


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
    """Modrinth API v2 over the standard library."""

    def __init__(self, api: str = API, timeout: float = 60.0) -> None:
        self.api = api
        self.timeout = timeout

    def _open(self, url: str, body: Any = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(request, timeout=self.timeout)

    def _json(self, path: str, body: Any = None) -> Any:
        url = f"{self.api}{path}"
        try:
            with self._open(url, body) as response:
                return json.loads(response.read())
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise ModrinthError(f"Modrinth request {url} failed: {error}") from error

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
        query = urllib.parse.urlencode({"loaders": json.dumps([loader]), "game_versions": json.dumps([game_version])})
        versions = self._json(f"/project/{urllib.parse.quote(project, safe='')}/version?{query}")
        try:
            version = next((v for v in versions if v.get("version_number") == version_number), None)
            return None if version is None else _version_file(version)
        except (AttributeError, KeyError, TypeError) as error:
            raise ModrinthError(f"Unexpected Modrinth answer for {project}: {error!r}") from error

    def download(self, url: str, dest: Path, sha512: str) -> None:
        """Stream `url` into `dest`, which appears only once its sha512 matches."""
        if urllib.parse.urlsplit(url).scheme != "https":
            raise ModrinthError(f"Refusing to download {url}: only https urls are allowed.")
        partial = dest.with_name(dest.name + ".partial")
        digest = hashlib.sha512()
        try:
            with self._open(url) as response, partial.open("wb") as stream:
                while chunk := response.read(_CHUNK):
                    digest.update(chunk)
                    stream.write(chunk)
            if digest.hexdigest() != sha512:
                raise HashMismatch(url, sha512, digest.hexdigest())
            os.replace(partial, dest)
        except (OSError, http.client.HTTPException) as error:
            raise ModrinthError(f"Download of {url} failed: {error}") from error
        finally:
            partial.unlink(missing_ok=True)

import hashlib
import http.client
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from minecraft_setup import __version__

# Modrinth asks every client for a User-Agent that names the project; nothing personal goes out
USER_AGENT = f"minecraft-setup/{__version__} (github.com/vkarach/minecraft-setup)"
_CHUNK = 1 << 20


class HttpError(Exception):
    """A request or download failed; nothing was written to its destination."""


class HashMismatch(HttpError):
    def __init__(self, url: str, algo: str, expected: str, actual: str) -> None:
        super().__init__(f"{algo} mismatch for {url}: expected {expected[:16]}..., got {actual[:16]}...")
        self.url = url


class Http(Protocol):
    def get(self, url: str, body: Any = None) -> bytes: ...

    def download(self, url: str, dest: Path, *, sha512: str | None = None, sha256: str | None = None) -> None: ...


def _digest(path: Path, algo: str) -> str:
    digest = hashlib.new(algo)
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_of(path: Path) -> str:
    return _digest(path, "sha512")


def sha256_of(path: Path) -> str:
    return _digest(path, "sha256")


class UrllibHttp:
    """Https over the standard library; a body is sent as a JSON POST."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 60.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def _open(self, url: str, body: Any = None):
        if urllib.parse.urlsplit(url).scheme != "https":
            raise HttpError(f"Refusing {url}: only https urls are allowed.")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, headers={"User-Agent": self.user_agent})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(request, timeout=self.timeout)

    def get(self, url: str, body: Any = None) -> bytes:
        try:
            with self._open(url, body) as response:
                return response.read()
        except (OSError, http.client.HTTPException) as error:
            raise HttpError(f"Request {url} failed: {error}") from error

    def download(self, url: str, dest: Path, *, sha512: str | None = None, sha256: str | None = None) -> None:
        """Stream `url` into `dest`, which appears only once every given hash matches."""
        expected = {"sha512": sha512, "sha256": sha256}
        partial = dest.with_name(dest.name + ".partial")
        digests = {algo: hashlib.new(algo) for algo, value in expected.items() if value is not None}
        try:
            with self._open(url) as response, partial.open("wb") as stream:
                while chunk := response.read(_CHUNK):
                    for digest in digests.values():
                        digest.update(chunk)
                    stream.write(chunk)
            for algo, digest in digests.items():
                if digest.hexdigest() != expected[algo]:
                    raise HashMismatch(url, algo, expected[algo], digest.hexdigest())
            os.replace(partial, dest)
        except (OSError, http.client.HTTPException) as error:
            raise HttpError(f"Download of {url} failed: {error}") from error
        finally:
            partial.unlink(missing_ok=True)

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from minecraft_setup.engine.http import Http, HttpError

RELEASES = "https://api.github.com/repos/vkarach/minecraft-setup/releases/latest"
EXE_ASSET = "minecraft-setup.exe"
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class Release:
    version: str
    exe_url: str
    sha256: str


def _numbers(version: str) -> tuple[int, ...] | None:
    match = _VERSION.fullmatch(version.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer(current: str, latest: str) -> bool:
    """Whether `latest` is a higher numeric version; a tag that is not one never counts."""
    ours, theirs = _numbers(current), _numbers(latest)
    return ours is not None and theirs is not None and theirs > ours


def latest_release(http: Http) -> Release | None:
    """The newest published exe and its sha256, or None when the release lacks either asset."""
    try:
        data = json.loads(http.get(RELEASES))
        urls = {asset["name"]: asset["browser_download_url"] for asset in data["assets"]}
        tag = str(data["tag_name"])
    except (ValueError, KeyError, TypeError) as error:
        raise HttpError(f"The release info at {RELEASES} is malformed: {error}") from error
    if EXE_ASSET not in urls or EXE_ASSET + ".sha256" not in urls:
        return None
    sha = http.get(urls[EXE_ASSET + ".sha256"]).decode("ascii", "replace").split()
    if not sha or not re.fullmatch(r"[0-9a-fA-F]{64}", sha[0]):
        return None
    return Release(tag, urls[EXE_ASSET], sha[0].lower())


def _sibling(exe: Path, suffix: str) -> Path:
    return exe.with_name(exe.name + suffix)


def stage(release: Release, exe: Path, http: Http) -> Path:
    """Download the release next to `exe` as `<exe>.new`, present only once its sha256 matches."""
    staged = _sibling(exe, ".new")
    http.download(release.exe_url, staged, sha256=release.sha256)
    return staged


def swap(exe: Path) -> None:
    """Put `<exe>.new` in place; Windows lets a running exe be renamed but not overwritten."""
    old, staged = _sibling(exe, ".old"), _sibling(exe, ".new")
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    try:
        os.replace(staged, exe)
    except OSError:
        os.replace(old, exe)
        raise


def cleanup(exe: Path) -> None:
    """Drop the previous exe; it stays locked until the old process has exited, so retry next start."""
    try:
        _sibling(exe, ".old").unlink(missing_ok=True)
    except OSError:
        pass

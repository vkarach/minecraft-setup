import os
import re
import subprocess
from pathlib import Path

# unquoted values may hold spaces, so they run up to the next option
_GAME_DIR = re.compile(r'--gameDir\s+(?:"([^"]*)"|(.+?))(?=\s+--|\s*$)')
_QUERY = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process -Filter \"Name LIKE 'java%'\" | ForEach-Object { $_.CommandLine }"
)


def _normal(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def uses_game_dir(cmdline: str, game_dir: Path) -> bool:
    """True when the command line's --gameDir is exactly `game_dir`."""
    found = _GAME_DIR.search(cmdline)
    if found is None:
        return False
    return _normal(found.group(1) or found.group(2)) == _normal(str(game_dir))


def running_in(game_dir: Path) -> bool:
    """True when a java process runs a game in `game_dir`; a failed check raises instead of guessing."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _QUERY],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=60,
        # no console flash when the GUI runs without a console
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return any(uses_game_dir(line, game_dir) for line in result.stdout.splitlines())

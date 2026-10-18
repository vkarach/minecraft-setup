import re
import subprocess

_GAME = re.compile(r"net\.fabricmc|KnotClient")
_QUERY = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process -Filter \"Name LIKE 'java%'\" | ForEach-Object { $_.CommandLine }"
)


def is_minecraft_running() -> bool:
    """True when a java process runs Fabric; a failed check raises instead of guessing."""
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
    return bool(_GAME.search(result.stdout))

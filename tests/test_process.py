import subprocess
from pathlib import Path

import pytest

from minecraft_setup.engine import process
from minecraft_setup.engine.process import running_check, running_in, uses_game_dir
from minecraft_setup.engine.profiles import GuardError

ROOT = Path(r"C:\Users\A\AppData\Roaming\.minecraft")
SOLO = ROOT / "versions" / "Solo"
SOLO_LINE = 'javaw.exe -Xmx8G --username x --gameDir "C:\\Users\\A\\AppData\\Roaming\\.minecraft\\versions\\Solo" --assetsDir y'


def test_matches_quoted_game_dir():
    assert uses_game_dir(SOLO_LINE, SOLO)


def test_root_not_matched_by_nested_game_dir():
    assert not uses_game_dir(SOLO_LINE, ROOT)


def test_case_and_trailing_slash_ignored():
    assert uses_game_dir("javaw.exe --gameDir c:\\users\\a\\appdata\\roaming\\.minecraft\\ --width 1", ROOT)


def test_unquoted_game_dir():
    assert uses_game_dir(r"javaw.exe --gameDir C:\mc --width 1", Path(r"C:\mc"))


def test_no_game_dir_argument():
    assert not uses_game_dir("javaw.exe -jar server.jar nogui", ROOT)


@pytest.mark.parametrize(("lines", "expected"), [(SOLO_LINE, False), (SOLO_LINE + "\njavaw --gameDir " + str(ROOT), True)])
def test_running_in_reads_every_java_process(monkeypatch, lines, expected):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=lines + "\n")

    monkeypatch.setattr(process.subprocess, "run", run)
    assert running_in(ROOT) is expected


def test_running_check_fails_closed(tmp_path):
    def broken(game_dir):
        raise subprocess.TimeoutExpired("powershell.exe", 60)

    with pytest.raises(GuardError, match="Cannot tell"):
        running_check(tmp_path, broken)()
    assert running_check(tmp_path, lambda game_dir: game_dir == tmp_path)()

from helpers import put
from minecraft_setup.engine.files import config_files


def test_config_files_skip_secret_files(game):
    put(game.root, "config/a.json", "{}")
    put(game.root, "usernamecache.json", "{}")
    put(game.root, "TlauncherProfiles.json", "{}")
    put(game.root, "config/usercache.json", "{}")
    put(game.root, "logs/x.log", "log")
    assert [p.relative_to(game.root).as_posix() for p in config_files(game)] == ["config/a.json"]

import sys
from pathlib import Path

import webview

from minecraft_setup.engine import selfupdate
from minecraft_setup.engine.http import UrllibHttp
from minecraft_setup.engine.installs import find_minecraft
from minecraft_setup.engine.modrinth import ModrinthClient
from minecraft_setup.engine.publish import find_repo
from minecraft_setup.engine.remote import CACHE
from minecraft_setup.gui.api import Api


def _web() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "minecraft_setup" / "gui" / "web"
    return Path(__file__).parent / "web"


def main() -> None:
    exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if exe is not None:
        selfupdate.cleanup(exe)
    http = UrllibHttp()
    api = Api(find_minecraft(), http, ModrinthClient(http), find_repo(), exe, CACHE)
    window = webview.create_window(
        "Minecraft Setup", str(_web() / "index.html"), js_api=api, width=1100, height=800, min_size=(760, 520)
    )
    api._quit = window.destroy
    webview.start()


if __name__ == "__main__":
    main()

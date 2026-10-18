from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from minecraft_setup.engine.mods import Profile


class GameDir:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.mods = self.root / "mods"
        self.saves = self.root / "saves"
        self.state_file = self.root / "mods-profile.state.json"

    def vault(self, profile: Profile) -> Path:
        """Folder outside saves where the game cannot list or open this profile's worlds."""
        return self.root / f"saves-{profile.value}-profile"

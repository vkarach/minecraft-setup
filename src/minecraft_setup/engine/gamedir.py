from pathlib import Path


class GameDir:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.mods = self.root / "mods"
        self.saves = self.root / "saves"
        self.state_file = self.root / "mods-profile.state.json"

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from minecraft_setup.engine.gamedir import GameDir

# unknown start -> every world counts as opened on the current profile
NEVER = datetime.min.replace(tzinfo=timezone.utc)


def read_since(game: GameDir) -> datetime:
    """When the current profile was switched on; `fullSince` is the two-profile script's key."""
    if not game.state_file.is_file():
        return NEVER
    try:
        data = json.loads(game.state_file.read_text(encoding="utf-8-sig"))
        when = datetime.fromisoformat(data.get("since") or data["fullSince"])
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(f"{game.state_file.name} holds no readable 'since' time") from error
    return when if when.tzinfo else when.astimezone()


def write_since(game: GameDir, when: datetime) -> None:
    """Replace the state file in one step so a crash never leaves it half-written."""
    text = json.dumps({"since": when.astimezone().isoformat()}, indent=4) + "\n"
    target = game.state_file
    handle, temp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="ascii") as stream:
            stream.write(text)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise

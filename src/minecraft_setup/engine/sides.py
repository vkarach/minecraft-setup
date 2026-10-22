from minecraft_setup.engine.manifest import Side

# 4x/5x jars change worlds, so every player needs them
_SERVER_GROUP = 4


def classify(group: int | None, sides: tuple[str, str] | None, override: Side | None) -> Side:
    """Whether every player must run a mod; `sides` is Modrinth's (client_side, server_side)."""
    if override is not None:
        return override
    if group is not None and group >= _SERVER_GROUP:
        return "required"
    if sides is not None and sides[1] == "required":
        return "required"
    return "client"

import hashlib

from minecraft_setup.engine.manifest import SCHEMA, ConfigFile, Manifest, ModEntry, PackInfo


def info(**changes) -> PackInfo:
    base = {"id": "fabric-26.3", "title": "Fabric 26.3", "game_version": "26.3", "loader": "fabric",
            "loader_version": "0.19.5", "profiles": True}
    return PackInfo(**{**base, **changes})


def entry(
    name: str,
    *,
    dir: str = "mods",
    side: str = "client",
    default: bool = True,
    ids: list[str] | None = None,
    depends: list[str] | None = None,
    data: bytes | None = None,
) -> ModEntry:
    payload = data if data is not None else name.encode()
    return ModEntry(
        name=name,
        dir=dir,
        project_id="P" + str(abs(hash(name)) % 1000),
        version_id="V1",
        url=f"https://cdn.test/{dir}/{name.replace(' ', '%20')}",
        sha512=hashlib.sha512(payload).hexdigest(),
        side=side,
        default=default,
        ids=ids if ids is not None else [],
        depends=depends if depends is not None else [],
    )


def manifest(*entries: ModEntry, configs=(), pack: PackInfo | None = None) -> Manifest:
    return Manifest(
        SCHEMA,
        pack or info(),
        list(entries),
        [ConfigFile(path, sha) for path, sha in configs],
    )

from minecraft_setup.engine.manifest import Manifest, ModEntry


def _providers(m: Manifest) -> dict[str, list[ModEntry]]:
    found: dict[str, list[ModEntry]] = {}
    for entry in m.entries:
        for mod_id in entry.ids:
            found.setdefault(mod_id, []).append(entry)
    return found


def _deps(entry: ModEntry, providers: dict[str, list[ModEntry]]) -> list[ModEntry]:
    # an id nobody provides is optional or builtin, so it is skipped rather than refused
    return [p for dep in entry.depends for p in providers.get(dep, []) if p is not entry]


def _choosable(m: Manifest) -> list[ModEntry]:
    """Client jars the user can tick; a library something depends on only follows its dependents."""
    providers = _providers(m)
    needed = {p.name for e in m.entries for p in _deps(e, providers)}
    return [e for e in _jars(m) if e.side == "client" and not (e.library and e.name in needed)]


def _jars(m: Manifest) -> list[ModEntry]:
    # resource and shader packs load only when picked in game, so they are always installed
    return [e for e in m.entries if e.dir == "mods"]


def default_choice(m: Manifest) -> frozenset[str]:
    return frozenset(e.name for e in _choosable(m) if e.default)


def resolve(m: Manifest, chosen: frozenset[str]) -> frozenset[str]:
    """Entry names to enable: required ones, chosen ones, and everything they depend on."""
    providers = _providers(m)
    by_name = {e.name: e for e in m.entries}
    start = [e for e in _jars(m) if e.side == "required"] + [e for e in _choosable(m) if e.name in chosen]
    enabled: set[str] = set()
    todo = list(start)
    while todo:
        entry = todo.pop()
        if entry.name in enabled:
            continue
        enabled.add(entry.name)
        todo += [by_name[p.name] for p in _deps(entry, providers)]
    return frozenset(enabled)


def needed_by(m: Manifest, enabled: frozenset[str]) -> dict[str, list[str]]:
    """For each enabled entry, the enabled entries that depend on it."""
    providers = _providers(m)
    found: dict[str, list[str]] = {}
    for entry in m.entries:
        if entry.name not in enabled:
            continue
        for provider in _deps(entry, providers):
            if provider.name in enabled and entry.name not in found.setdefault(provider.name, []):
                found[provider.name].append(entry.name)
    return found

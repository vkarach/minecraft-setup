# minecraft-setup

One portable exe that installs and updates our Minecraft modpacks.

- Finds `.minecraft` and the TLauncher versions in it, and matches them to the published packs (Fabric 26.3 and Solo on NeoForge).
- Shows the mods of each pack with checkboxes: server mods are required, client mods are yours to pick. Libraries follow the mods that need them.
- On start it checks this repo for pack changes and applies them on one button. Every file comes from Modrinth and is checked by sha512 before it lands.
- Your own settings stay yours: configs, `options.txt` and shader settings are only written where the file does not exist yet. Mods you added yourself are left alone.
- Fabric 26.3 keeps the vanilla/full profiles and the world guard.
- The app updates itself from GitHub Releases.

## For friends

1. Download `minecraft-setup.exe` from the [latest release](https://github.com/vkarach/minecraft-setup/releases/latest) once. Put it anywhere.
2. Run it. Windows SmartScreen may warn about an unknown app: "More info" -> "Run anyway".
3. If a pack says "Not installed", install the version it names in TLauncher, then press Refresh.
4. Pick your mods, press Apply. Close Minecraft first - the app refuses to change a running game.

Later starts show what changed in the pack; press Apply again. The exe never needs to be sent again.

## For the owner

Publishing needs a clone of this repo with git (the Publish button and the `publish` command appear only then).

- Change mods in the live game dir by hand. Every jar and pack archive must be a Modrinth file.
- Publish: the Publish button, or `uv run minecraft-setup publish <pack>`. It writes `packs/<pack>/manifest.json` and the configs, then commits and pushes `packs/`. A new pack needs `--title`, `--install` and, for 26.3-style packs, `--profiles`.
- Side fixes: `packs/<pack>/overrides.json` maps a jar name or Modrinth project id to `required` or `client` when Modrinth's flags are wrong.
- App release: bump `__version__` and `pyproject.toml`, then push a tag `v<version>`. GitHub Actions builds the exe and attaches it with its sha256.

## Development

```
uv sync
uv run pytest
uv run minecraft-setup-gui
uv run minecraft-setup --help
uv run pyinstaller minecraft-setup.spec
```

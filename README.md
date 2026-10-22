<div align="center">

# Minecraft Setup

**One exe that installs, updates and tunes our Minecraft modpacks.**

[![Release](https://img.shields.io/github/v/release/vkarach/minecraft-setup?label=download&color=3f8f3f)](https://github.com/vkarach/minecraft-setup/releases/latest)
![Windows](https://img.shields.io/badge/platform-Windows-4a90d9)
![Packs](https://img.shields.io/badge/packs-Fabric%2026.3%20%7C%20Solo%20(NeoForge)-5cb85c)

<img src="docs/images/overview.png" alt="Fabric 26.3 tab: pending changes, profile and worlds" width="800">

</div>

## Why

No more zips in the chat. Grab the exe once - from then on the app pulls the current mod list from this repo, shows
what changed and applies it with one button. Mods come straight from Modrinth and are checked by sha512 before they
touch your game.

## What it does

- **Finds your game.** Detects `.minecraft` and the TLauncher versions in it, and matches each pack to the right one.
  Tells you what to install when a version is missing.
- **Your mods, your choice.** Server mods have no checkbox - everyone needs them to join. Client mods (shaders, HUD,
  sounds...) are checkboxes; your picks are saved when you press **Apply**.
- **Libraries sort themselves out.** A library stays on while anything ticked needs it, and goes away with the last
  mod that used it - Kotlin language mods included.
- **Updates on start.** New, updated and removed mods are listed before anything happens. Replaced and removed files
  go to `backup\update-<date>_<time>`.
- **Leaves your stuff alone.** Configs, `options.txt` and shader settings are written only when missing. Mods you
  added yourself are left alone (one exception on 26.3, see the FAQ).
- **Safe while playing.** Refuses to change a pack whose game is running.
- **Profiles for 26.3.** Vanilla / Full switch with the world guard: worlds of the other profile are hidden so they
  never load with the wrong mods. A world left hidden in the current profile comes back with the profile button.
- **Updates itself.** New app versions come from GitHub Releases, verified by sha256.

<p align="center">
  <img src="docs/images/mods.png" alt="Mod list with checkboxes grouped by kind" width="49%">
  <img src="docs/images/libraries.png" alt="Libraries with the mods that need them, server mods marked" width="49%">
</p>

## Get started (friends)

1. Download **[minecraft-setup.exe](https://github.com/vkarach/minecraft-setup/releases/latest/download/minecraft-setup.exe)** and put it anywhere.
2. Run it. If Windows SmartScreen warns about an unknown app: **More info -> Run anyway** (the exe is not code-signed).
3. A pack says **Not installed**? Install the version it names in TLauncher, then press **Refresh**.
4. Untick what you do not want and press **Apply**. Close Minecraft first.

Next time just start the app again: if the pack changed, it shows what is new - press **Apply**.
The exe never has to be sent around again.

## FAQ

**Will it reset my settings?** No. Config files are only written where they do not exist yet, so your keybinds,
video settings and shader options stay.

**I added a mod myself.** It is listed under "Your own mods" and left alone. On 26.3 do not name it `4x ...` or
`5x ...`: those prefixes belong to the Full profile, and the profile switch turns such jars on and off with it.

**Something went wrong mid-update.** Every replaced or removed file is in `backup\update-<date>_<time>` inside the game
folder. Mods that were only turned on or off are renamed to and from `.disabled`, not copied.

**Where does it keep its data?** Your choices go to `minecraft-setup.json` in the game folder when you press
**Apply** (ticking alone keeps them only until the app closes), a copy of the pack list to
`%LOCALAPPDATA%\minecraft-setup` (so it also opens offline).

## For the owner

Publishing appears only when the app finds a clone of this repo and git.

- Change mods in the live game folder. Every jar and pack archive must be a Modrinth file.
- **Publish** (button, or `uv run minecraft-setup publish <pack>`) writes `packs/<pack>/manifest.json` and the
  configs, then commits and pushes `packs/`. A new pack also needs `--title`, `--install` and, for 26.3-style packs,
  `--profiles`. Files that look like they hold a token or password are refused.
- Modrinth got a side wrong? Put `{"<jar name or project id>": "required" | "client"}` in `packs/<pack>/overrides.json`.
- New app version: bump `__version__` and `pyproject.toml`, push a tag `v<version>`. GitHub Actions tests, builds the
  exe and publishes it with its sha256.

## Development

```
uv sync
uv run pytest                      # offline tests; -m network for the live Modrinth ones
uv run minecraft-setup-gui         # the app
uv run minecraft-setup --help      # the same engine from the command line
uv run pyinstaller minecraft-setup.spec
```

Python 3.13+ (3.14 pinned for development and the exe), pywebview for the window, plain HTML/CSS/JS for the screen, PyInstaller for the one-file exe.

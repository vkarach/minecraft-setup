# PyInstaller build of the one-file GUI exe: uv run pyinstaller minecraft-setup.spec

a = Analysis(
    ["src/minecraft_setup/gui/app.py"],
    pathex=["src"],
    datas=[("src/minecraft_setup/gui/web", "minecraft_setup/gui/web")],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="minecraft-setup",
    console=False,
    upx=False,
)

# -*- mode: python ; coding: utf-8 -*-
import json, os

# Читаем brand.json для имени exe и иконки
brand = {}
if os.path.exists("brand.json"):
    with open("brand.json", "r", encoding="utf-8") as f:
        brand = json.load(f)

EXE_NAME = brand.get("server_name", "Launcher").replace(" ", "_") + "_Launcher"
ICON     = "icon.ico" if os.path.exists("icon.ico") else None

block_cipher = None

a = Analysis(
    ["launcher.py", "gtnh_launch.py", "profiles.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("brand.json", "."),
        ("icon.ico",   ".") if ICON else ("brand.json", "."),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "tkinter.filedialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy", "PIL",
        "psycopg2", "bcrypt", "_bcrypt",
        "minecraft_launcher_lib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version_file=None,
)

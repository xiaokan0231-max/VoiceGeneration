# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path


repo = Path(SPECPATH).resolve().parents[1]
conda_dll_dir = Path(sys.prefix) / "Library" / "bin"
if conda_dll_dir.is_dir():
    os.environ["PATH"] = str(conda_dll_dir) + os.pathsep + os.environ.get("PATH", "")

a = Analysis(
    [str(repo / "scripts" / "tray.py")],
    pathex=[str(repo)],
    binaries=[],
    datas=[
        (str(repo / "packaging" / "tray.png"), "packaging"),
        (str(repo / "packaging" / "tray_off.png"), "packaging"),
    ],
    hiddenimports=["pystray._win32", "PIL.Image", "gateway.config"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VoiceGeneration",
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
    icon=[str(repo / "packaging" / "AppIcon.ico")],
)

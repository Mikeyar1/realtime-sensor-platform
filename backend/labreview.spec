# -*- mode: python ; coding: utf-8 -*-
#
# labreview.spec — PyInstaller build spec for LabReview backend
#
# Build command (run from backend/ directory):
#   uv run pyinstaller labreview.spec --clean --noconfirm
#
# Output: backend/dist/LabReview/LabReview.exe
#

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #

BACKEND_DIR  = Path(SPECPATH)                        # backend/
PROJECT_ROOT = BACKEND_DIR.parent                    # LabReview/

# pylsl ships lsl.dll inside its package — PyInstaller won't find it on its own
PYLSL_LIB_DIR = BACKEND_DIR / ".venv" / "Lib" / "site-packages" / "pylsl" / "lib"

block_cipher = None

# --------------------------------------------------------------------------- #
# Analysis                                                                     #
# --------------------------------------------------------------------------- #

a = Analysis(
    [str(BACKEND_DIR / "main.py")],

    # Make sure the backend package root is resolvable at analysis time
    pathex=[str(BACKEND_DIR)],

    # Native binaries that PyInstaller can't auto-detect
    binaries=[
        # lsl.dll — required by pylsl at runtime
        (str(PYLSL_LIB_DIR / "lsl.dll"), "pylsl/lib"),
    ],

    # Data files bundled into the package
    datas=[
        # Backend runtime config (editable by operator)
        (str(BACKEND_DIR / "config.toml"),               "."),

        # Shared registry, stream policy, and bundled scenario contracts.
        (str(PROJECT_ROOT / "config" / "drill_registry.toml"), "config"),
        (str(PROJECT_ROOT / "config" / "virtra_streams.toml"), "config"),
        (str(BACKEND_DIR / "scenarios"),                "scenarios"),

        # Frontend — served by the embedded HTTP server at runtime
        (str(PROJECT_ROOT / "frontend"),                 "frontend"),

        # pylsl pure-Python layer (lib/__init__.py loads the DLL via ctypes)
        (str(PYLSL_LIB_DIR / "__init__.py"),             "pylsl/lib"),
    ],

    # Packages whose submodules are loaded dynamically (imported by string at runtime)
    hiddenimports=[
        # cognitive_attributes are registered by name and instantiated dynamically
        # Note: gng/ is currently an empty placeholder — add tracker/metrics here when implemented
        "cognitive_attributes",
        "cognitive_attributes.pvt",
        "cognitive_attributes.pvt.tracker",
        "cognitive_attributes.pvt.metrics",
        "cognitive_attributes.gng",
        "cognitive_attributes.multicolor_gng",
        "cognitive_attributes.multicolor_gng.tracker",
        "cognitive_attributes.multicolor_gng.metrics",
        "cognitive_attributes.discrimination",
        "cognitive_attributes.discrimination.tracker",
        "cognitive_attributes.discrimination.metrics",

        # websockets uses some internals that can be missed
        "websockets.legacy",
        "websockets.legacy.server",
        "websockets.legacy.client",
        "websockets.asyncio",
        "websockets.asyncio.server",

        # numpy internal methods (name changed in newer numpy)
        "numpy._core._methods",
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not used at runtime
        "tkinter",
        "matplotlib",
        "PIL",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --------------------------------------------------------------------------- #
# PYZ archive                                                                  #
# --------------------------------------------------------------------------- #

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --------------------------------------------------------------------------- #
# EXE                                                                          #
# --------------------------------------------------------------------------- #

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,           # one-folder mode — faster startup than one-file
    name="LabReview",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                    # keep console window — useful for seeing errors
                                     # set to False once the app is stable
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",        # uncomment once you have a .ico file
)

# --------------------------------------------------------------------------- #
# COLLECT — gathers exe + binaries + datas into dist/LabReview/               #
# --------------------------------------------------------------------------- #

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LabReview",
)

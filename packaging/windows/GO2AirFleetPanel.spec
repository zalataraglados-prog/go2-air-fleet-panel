# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata


project_root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sdk_datas, sdk_binaries, sdk_hiddenimports = collect_all(
    "unitree_webrtc_connect"
)
wasmtime_binaries = collect_dynamic_libs("wasmtime")

analysis = Analysis(
    [os.path.join(project_root, "scripts", "panel.py")],
    pathex=[project_root],
    binaries=sdk_binaries + wasmtime_binaries,
    datas=(
        sdk_datas
        + copy_metadata("unitree_webrtc_connect")
        + [
            (os.path.join(project_root, "panel"), "panel"),
            (
                os.path.join(
                    project_root,
                    "config",
                    "config.example.yaml",
                ),
                "config",
            ),
        ]
    ),
    hiddenimports=sdk_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="GO2AirFleetPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(SPECPATH, "version_info.txt"),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GO2AirFleetPanel",
)

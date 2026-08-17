# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir COLLECT + BUNDLE → dist/RemoteAgent.app

Run from the repo root:
    pyinstaller packaging/RemoteAgent.spec
"""

from pathlib import Path

root = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(root / "agent" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "agent",
        "agent.server",
        "agent.protocol",
        "agent.macos",
        "agent.macos.mouse",
        "agent.macos.statusbar",
        "agent.macos.statusicon",
        "objc",
        "Quartz",
        "Quartz.CoreGraphics",
        "ApplicationServices",
        "Cocoa",
        "AppKit",
        "Foundation",
        "PyObjCTools",
        "PyObjCTools.AppHelper",
        "websockets",
        "websockets.asyncio.server",
    ],
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
    [],
    exclude_binaries=True,
    name="RemoteAgent",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RemoteAgent",
)
app = BUNDLE(
    coll,
    name="RemoteAgent.app",
    icon=str(root / "packaging" / "icon" / "RemoteAgent.icns"),
    bundle_identifier="dev.squidly.remotecontrol.agent",
    info_plist={
        "CFBundleName": "RemoteAgent",
        "CFBundleDisplayName": "RemoteAgent",
        "CFBundleIdentifier": "dev.squidly.remotecontrol.agent",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": (
            "RemoteAgent injects mouse events for local remote-control testing."
        ),
    },
)

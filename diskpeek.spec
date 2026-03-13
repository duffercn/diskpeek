# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for diskpeek GUI

a = Analysis(
    ['diskpeek_gui.py'],
    pathex=['.'],
    binaries=[
        ('scanner/diskpeek-scanner', '.'),   # bundle the Go scanner binary
    ],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='diskpeek',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # no terminal window
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
    name='diskpeek',
)

app = BUNDLE(
    coll,
    name='diskpeek.app',
    icon=None,              # add an .icns file path here if you have one
    bundle_identifier='com.diskpeek.app',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
        # Allow access to all folders (user will be prompted by macOS)
        'NSDesktopFolderUsageDescription': 'diskpeek needs access to scan folders.',
        'NSDocumentsFolderUsageDescription': 'diskpeek needs access to scan folders.',
        'NSDownloadsFolderUsageDescription': 'diskpeek needs access to scan folders.',
    },
)

# PyInstaller spec file for PLANA
# This file can be used for more advanced packaging configurations

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.default.yaml', '.'),
        ('PLANA', 'PLANA'),
    ],
    hiddenimports=[
        'discord',
        'discord.ext.commands',
        'yaml',
        'openai',
        'google.genai',
        'PIL',
        'matplotlib',
        'cartopy',
        'langdetect',
        'aiohttp',
        'aiofiles',
        'nacl',
        'yt_dlp',
        'discord.player',
        'discord.ext',
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
    a.binaries,
    a.datas,
    [],
    name='PLANA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('.env', '.')]
binaries = []
hiddenimports = ['jarvis', 'jarvis.brain', 'jarvis.ui', 'jarvis.ui.main_window', 'jarvis.ui.orb_rings', 'jarvis.ui.chat_panel', 'jarvis.ui.hud_panels', 'jarvis.ui.waveform', 'jarvis.ui.backend_connector', 'jarvis.ui.voice', 'jarvis.scheduler', 'jarvis.database', 'jarvis.config', 'jarvis.brain.prompts', 'jarvis.brain.commands', 'composio', 'composio.core', 'composio.exceptions']
tmp_ret = collect_all('composio')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='Jarvis',
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
    icon=['icon.png'],
)

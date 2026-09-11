# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('jarvis/.env', 'jarvis')]
binaries = []
hiddenimports = [
    # jarvis package
    'jarvis', 'jarvis.brain', 'jarvis.brain.prompts', 'jarvis.brain.commands',
    'jarvis.brain.screen_prompt', 'jarvis.ui', 'jarvis.ui.orb',
    'jarvis.ui.main_window', 'jarvis.ui.voice', 'jarvis.ui.tts',
    'jarvis.ui.themes', 'jarvis.ui.chat_panel', 'jarvis.ui.hud_panels',
    'jarvis.voice_loop', 'jarvis.memory', 'jarvis.screen_watcher',
    'jarvis.autostart', 'jarvis.scheduler', 'jarvis.database', 'jarvis.config',
    'jarvis.tools.pc_control',
    'jarvis.tools.gui_automation', 'jarvis.tools.web_scraper',
    'jarvis.tools.browser_automation',
    # third-party
    'composio', 'google', 'google.genai', 'groq', 'openai',
    'pyttsx3', 'speech_recognition', 'edge_tts', 'sounddevice', 'soundfile',
    'sounddevice._sounddevice', 'psutil', 'requests', 'certifi',
    'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
    'PyQt6.QtMultimedia',
]
# NOTE: never collect_all('jarvis') -- it sweeps non-.py files under the
# package folder, including stale build/dist artifacts, into the exe. Each
# rebuild would then embed the previous exe (~300 MB growth per build).
# The jarvis package is analyzed normally from the entry script instead.
for pkg in ('composio', 'google.genai', 'edge_tts',
            'soundfile', 'psutil'):
    try:
        tmp = collect_all(pkg)
        datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
    except Exception:
        pass

# sounddevice/soundfile are single-module packages: collect_all skips their
# binary DLL dirs, so grab them explicitly (PortAudio/libsndfile).
from PyInstaller.utils.hooks import collect_dynamic_libs
for pkg in ('sounddevice', 'soundfile'):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

a = Analysis(
    ['jarvis\\__main__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy optional deps that get dragged in via composio's optional
    # framework adapters (only if installed in site-packages). Jarvis does
    # not use any of them; excluding keeps the exe at a sane size.
    excludes=[
        'tkinter', 'matplotlib', 'pandas', 'notebook', 'IPython', 'jupyter',
        'torch', 'torchvision', 'torchaudio', 'triton',
        'transformers', 'datasets', 'tokenizers', 'safetensors',
        'sympy', 'networkx', 'scipy', 'sklearn', 'tensorflow', 'keras',
        'yt_dlp', 'Crypto', 'pycryptodome',
        'pyaudio', 'pygame', 'PyQt5', 'Pyside2', 'PySide6',
        'pytest', 'setuptools', 'pip', 'wheel',
    ],
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='jarvis.ico',
)

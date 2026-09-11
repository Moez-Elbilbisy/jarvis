"""
Jarvis Auto-Start -- launch with Windows.

Uses the per-user registry Run key (HKCU\\...\\Run): no admin rights needed,
survives reboots, removable with one call. The entry launches the packaged
Jarvis.exe when it exists, otherwise `python -m jarvis` with the current
interpreter (dev mode).
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger("jarvis.autostart")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "JarvisAssistant"


def _open_run_key(write: bool = False):
    import winreg
    access = winreg.KEY_SET_VALUE if write else winreg.KEY_READ
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, access)


def _launch_target() -> str:
    """Command line used to start Jarvis at login."""
    if getattr(sys, "frozen", False):
        # PyInstaller exe: launch the exe itself.
        return f'"{sys.executable}"'
    # Dev mode: pythonw (no console window) with the project dir injected,
    # so it works from ANY working directory (boot uses system32).
    import jarvis
    project_dir = str(Path(jarvis.__file__).resolve().parent.parent)
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    code = f"import sys; sys.path.insert(0, r'{project_dir}'); from jarvis.main import main; main()"
    return f'"{pythonw}" -c "{code}"'


def get_command() -> str:
    """The command currently configured (or what would be configured)."""
    try:
        with _open_run_key() as key:
            value, _ = __import__("winreg").QueryValueEx(key, _VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return _launch_target()
    except Exception:
        return _launch_target()


def is_installed() -> bool:
    """True if the autostart entry exists and points at this Jarvis."""
    try:
        with _open_run_key() as key:
            value, _ = __import__("winreg").QueryValueEx(key, _VALUE_NAME)
        return "jarvis" in str(value).lower()
    except Exception:
        return False


def install() -> bool:
    """Create/refresh the HKCU Run entry. Returns True on success."""
    try:
        import winreg
        target = _launch_target()
        with _open_run_key(write=True) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, target)
        logger.info("Autostart installed: %s", target)
        return True
    except Exception as e:
        logger.error("Autostart install failed: %s", e)
        return False


def uninstall() -> bool:
    """Remove the autostart entry. Returns True if absent or removed."""
    try:
        import winreg
        with _open_run_key(write=True) as key:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass  # already gone
        logger.info("Autostart removed")
        return True
    except Exception as e:
        logger.error("Autostart uninstall failed: %s", e)
        return False


def toggle() -> tuple[bool, bool]:
    """Install if absent, remove if present. Returns (installed_now, ok)."""
    if is_installed():
        return False, uninstall()
    return True, install()

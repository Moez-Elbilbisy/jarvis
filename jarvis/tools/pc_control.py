"""
Jarvis PC Control -- Native PC automation and control.
Provides functions for controlling the user's PC: opening apps, managing files,
running commands, taking screenshots, controlling volume, and more.
"""

import ctypes
import glob
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.tools")

# ── Windowed-exe subprocess support ────────────────────────────
# In a PyInstaller console=False build there are NO valid stdio handles,
# and subprocess.Popen inherits them by default -> WinError 6 "invalid
# handle". Every spawn must either detach the child's stdio explicitly or
# point it at the null device.

if platform.system() == "Windows":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESTDHANDLES
    _devnull_handle = os.open(os.devnull, os.O_RDWR | os.O_BINARY)

def _popen_kwargs(extra: dict) -> dict:
        """Merge null-stdio kwargs into a Popen argument dict."""
        kw = {
            "stdin": _devnull_handle,
            "stdout": _devnull_handle,
            "stderr": _devnull_handle,
            "close_fds": False,  # keep our null handles valid
        }
        kw.update(extra or {})
        return kw


# ── Subprocess lifetime cap ─────────────────────────────────────────────
# envvar overrides the default 5s cap (seconds, 0 = no cap).
_SUBPROCESS_CAP_SECONDS = int(os.environ.get("JARVIS_SUBPROCESS_CAP_SECONDS", "5"))

if platform.system() != "Windows":
    _si = None

    def _popen_kwargs(extra: dict) -> dict:
        kw = {"start_new_session": True}
        kw.update(extra or {})
        return kw


def _spawn(cmd: list, timeout_cap: Optional[float] = None, **extra) -> subprocess.Popen:
    """subprocess.Popen that works in console-less exe builds.

    An optional `timeout_cap` (seconds) registers a safety net: the child
    is killed if it outlives this cap. Mirrors the capped run path so
    long-running commands can't stall a hot-path reply.
    """
    if platform.system() == "Windows":
        extra.setdefault("startupinfo", _si)
        extra.setdefault("creationflags", 0)
    proc = subprocess.Popen(cmd, **_popen_kwargs(extra))
    if timeout_cap and timeout_cap > 0:
        _ = threading.Timer(timeout_cap, _kill_if_alive, args=[proc])
        _.daemon = True
        _.start()
    return proc


def _kill_children_and_wait(proc: subprocess.Popen, timeout_sec: float = 2.0) -> None:
    """Terminate proc + all its children, then wait a little."""
    try:
        if platform.system() == "Windows" and proc.pid:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=timeout_sec,
            )
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
    except Exception:
        pass


def _kill_if_alive(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            _kill_children_and_wait(proc, timeout_sec=1.0)
        except Exception:
            pass

# Where undoable file operations stash originals before overwrite/delete
UNDO_TRASH = Path.home() / ".jarvis_undo"


# ── Confirmation gate ──────────────────────────────────────────

class ConfirmationGate:
    """
    Confirmation gate for irreversible system actions.

    Jarvis must NOT execute shutdown/restart/sleep immediately. It registers
    a pending action; the UI shows a Confirm/Abort bar and only calls
    confirm() on an explicit user click. The model can never confirm its
    own destructive action.
    """

    def __init__(self):
        self._pending: Optional[dict] = None
        self._event = threading.Event()
        self._outcome: Optional[str] = None  # 'confirmed' | 'aborted' | None
        self._lock = threading.Lock()

    def request(self, action: str, detail: str = "", timeout: float = 60.0) -> Dict[str, Any]:
        """Register a pending action, notify the UI, and block until confirmed/aborted/timeout."""
        with self._lock:
            self._event.clear()
            self._outcome = None
            self._pending = {"action": action, "detail": detail}
        # Notify the UI only AFTER the pending action is registered,
        # so a synchronous confirm() in the callback cannot be lost.
        _notify_confirmation(action, detail)
        self._event.wait(timeout)
        with self._lock:
            self._pending = None
            outcome = self._outcome
        if outcome == "confirmed":
            return {"success": True, "data": f"User confirmed {action}."}
        if outcome == "aborted":
            return {"success": False, "error": f"User aborted {action}."}
        return {"success": False, "error": f"Confirmation for {action} timed out. Action cancelled."}

    def confirm(self) -> bool:
        with self._lock:
            if self._pending is None:
                return False
            self._outcome = "confirmed"
        self._event.set()
        return True

    def abort(self) -> bool:
        with self._lock:
            had_pending = self._pending is not None
            self._pending = None
            self._outcome = "aborted"
        if had_pending:
            self._event.set()
        return had_pending

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None

    def pending_action(self) -> Optional[dict]:
        with self._lock:
            return dict(self._pending) if self._pending else None


CONFIRMATION_GATE = ConfirmationGate()
_on_confirmation_requested = None


def set_confirmation_callback(callback) -> None:
    """Install the UI callback invoked when a confirmation is requested."""
    global _on_confirmation_requested
    _on_confirmation_requested = callback


def _notify_confirmation(action: str, detail: str) -> None:
    if _on_confirmation_requested:
        try:
            _on_confirmation_requested(action, detail)
        except Exception as e:
            logger.warning("Confirmation callback failed: %s", e)


def undo_pending() -> bool:
    """True if a power-action confirmation is awaiting the user."""
    return CONFIRMATION_GATE.has_pending()


def capture_screen_png(max_dim: int = 1600) -> Optional[bytes]:
    """
    Capture the current screen as downscaled PNG bytes, ready to feed to
    Gemini as an inline image. Returns None if capture fails.
    """
    try:
        import io
        from PIL import ImageGrab
        img = ImageGrab.grab()
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        logger.info("Screen captured: %dx%d, %d KB", img.size[0], img.size[1], len(data) // 1024)
        return data
    except Exception as e:
        logger.warning("Screen capture failed: %s", e)
        return None


# ── Undo stack (trash-based) ──────────────────────────────

def _stash_original(path: Path) -> None:
    """Copy an existing file/dir into the undo trash before overwriting/deleting."""
    try:
        UNDO_TRASH.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = UNDO_TRASH / f"{stamp}_{path.name}"
        if path.is_dir():
            shutil.copytree(str(path), str(dest))
        else:
            shutil.copy2(str(path), str(dest))
    except Exception as e:
        logger.debug("Undo stash failed for %s: %s", path, e)


def _record_undo(entry: dict) -> None:
    """Append an undo entry to the undo log."""
    try:
        UNDO_TRASH.mkdir(parents=True, exist_ok=True)
        idx = UNDO_TRASH / "undo_log.json"
        log = []
        if idx.exists():
            try:
                log = json.loads(idx.read_text(encoding="utf-8"))
            except Exception:
                log = []
        log.append(entry)
        idx.write_text(json.dumps(log[-200:], indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Undo log write failed: %s", e)


def undo_last_operation() -> Dict[str, Any]:
    """Undo the most recent undoable file operation (move/copy/delete/write)."""
    idx = UNDO_TRASH / "undo_log.json"
    if not idx.exists():
        return {"success": False, "error": "Nothing to undo."}
    try:
        log = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return {"success": False, "error": "Undo log unreadable."}
    while log:
        entry = log.pop()
        try:
            idx.write_text(json.dumps(log, indent=2), encoding="utf-8")
        except Exception:
            pass
        kind = entry.get("kind")
        try:
            if kind == "move":
                src, dst = Path(entry["src"]), Path(entry["dst"])
                if dst.exists():
                    if src.exists():
                        continue
                    shutil.move(str(dst), str(src))
                    return {"success": True, "data": f"Undid move: {dst.name} back to {src}"}
            elif kind == "copy":
                dst = Path(entry["dst"])
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(str(dst))
                    else:
                        dst.unlink()
                    return {"success": True, "data": f"Undid copy: removed {dst}"}
            elif kind == "delete":
                backup = Path(entry["backup"])
                orig = Path(entry["original"])
                if backup.exists() and not orig.exists():
                    orig.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir():
                        shutil.copytree(str(backup), str(orig))
                    else:
                        shutil.copy2(str(backup), str(orig))
                    return {"success": True, "data": f"Restored {orig.name}"}
            elif kind == "write":
                backup = Path(entry["backup"])
                orig = Path(entry["original"])
                orig.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    shutil.copy2(str(backup), str(orig))
                else:
                    orig.unlink(missing_ok=True)
                return {"success": True, "data": f"Restored previous version of {orig.name}"}
        except Exception:
            continue
    return {"success": False, "error": "Nothing more to undo."}

# ── Path shortcuts ──────────────────────────────────────────────
HOME = Path.home()
DESKTOP = HOME / "Desktop"
DOWNLOADS = HOME / "Downloads"
DOCUMENTS = HOME / "Documents"
DRIVE_C = Path("C:\\")


class PCController:
    """Execute PC control actions. All methods return a dict with 'success' and 'data'/'error'."""

    def focus_window(self, window_name: str, **kwargs) -> Dict[str, Any]:
        """Bring a window whose title/app name matches window_name to the foreground.

        Matches case-insensitively against window titles AND process names
        (so 'discord' finds 'Discord - #general'). Falls back to Alt-Tab
        restore via ShowWindow when the window is minimized.
        """
        if platform.system() != "Windows":
            return {"success": False, "error": "focus_window is Windows-only"}
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            target = window_name.lower().strip()
            if not target:
                return {"success": False, "error": "No window name given"}

            results: list[dict] = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )

            def _cb(hwnd, lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pname = ""
                try:
                    import psutil
                    proc = psutil.Process(pid.value)
                    pname = proc.name().lower()
                except Exception:
                    pass
                t_low = title.lower()
                if target in t_low or target.replace(".exe", "") in pname:
                    results.append({
                        "hwnd": hwnd, "title": title, "process": pname,
                        "minimized": bool(user32.IsIconic(hwnd)),
                    })
                return True

            user32.EnumWindows(EnumWindowsProc(_cb), 0)

            if not results:
                return {
                    "success": False,
                    "error": (f"No open window matches '{window_name}'. "
                              "The app may not be running -- try pc_open_app first."),
                }

            win = results[0]
            hwnd = win["hwnd"]
            if win["minimized"]:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            # Gentle retry: foreground rights can be denied on first call
            import time as _t
            _t.sleep(0.15)
            if user32.GetForegroundWindow() != hwnd:
                user32.SetForegroundWindow(hwnd)
                _t.sleep(0.15)
            focused = user32.GetForegroundWindow() == hwnd
            return {
                "success": True,
                "focused": focused,
                "window": win["title"],
                "process": win["process"],
                "data": f"Focused '{win['title']}'"
                        + ("" if focused else " (best effort; Windows blocked full focus)"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_app(self, app_name: str, **kwargs) -> Dict[str, Any]:
        """Open an application by name. Multiple strategies for Windows."""
        if platform.system() == "Windows":
            return self._open_app_windows(app_name)
        elif platform.system() == "Darwin":
            try:
                subprocess.Popen(["open", "-a", app_name])
                return {"success": True, "data": f"Opened '{app_name}'"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            try:
                subprocess.Popen([app_name], start_new_session=True)
                return {"success": True, "data": f"Launched '{app_name}'"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def _open_app_windows(self, app_name: str) -> Dict[str, Any]:
        """Open an app on Windows. Searches ALL locations before reporting success."""
        name_lower = app_name.lower().strip()
        name_clean = name_lower.replace(".exe", "").replace(".lnk", "")

        # Arabic/Franco app names -> real exe names ("\u0633\u0628\u0648\u062a\u064a\u0641\u0627\u064a" -> spotify).
        # Without this, an Arabic-spoken app name can never match a binary.
        _AR_APP_ALIASES = {
            "\u0633\u0628\u0648\u062a\u064a\u0641\u0627\u064a": "spotify",
            "\u0627\u0644\u0633\u0628\u0648\u062a\u064a\u0641\u0627\u064a": "spotify",
            "\u062f\u064a\u0633\u0643\u0648\u0631\u062f": "discord",
            "\u0627\u0644\u062f\u064a\u0633\u0643\u0648\u0631\u062f": "discord",
            "\u062f\u0633\u0643\u0648\u0631\u062f": "discord",
            "\u0643\u0631\u0648\u0645": "chrome",
            "\u0627\u0644\u0643\u0631\u0648\u0645": "chrome",
            "\u062c\u0648\u062c\u0644 \u0643\u0631\u0648\u0645": "chrome",
            "\u0633\u062a\u064a\u0645": "steam",
            "\u0627\u0644\u0633\u062a\u064a\u0645": "steam",
            "\u064a\u0648\u062a\u064a\u0648\u0628": "youtube",
            "\u0648\u0627\u062a\u0633\u0627\u0628": "whatsapp",
            "\u0648\u0627\u062a\u0633": "whatsapp",
            "\u062a\u0644\u064a\u062c\u0631\u0627\u0645": "telegram",
            "\u0648\u0648\u0631\u062f": "winword",
            "\u0627\u0644\u0627\u0644\u0629 \u0627\u0644\u062d\u0627\u0633\u0628\u0629": "calculator",
            "\u0627\u0644\u062d\u0627\u0633\u0628\u0629": "calculator",
        }
        name_clean = _AR_APP_ALIASES.get(name_lower, name_clean)

        # Step 0: already running? Focus the existing window instead of
        # spawning a second instance that may just quit itself.
        try:
            focus_result = self.focus_window(name_clean)
            if focus_result.get("success"):
                return {
                    "success": True,
                    "data": f"'{app_name}' was already running -- brought its window to the foreground",
                    "focused": True,
                }
        except Exception:
            pass

        # Step 1: Search Start Menu shortcuts (.lnk files) -- most reliable
        start_menu_dirs = [
            HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
        ]
        for sm_dir in start_menu_dirs:
            if not sm_dir.exists():
                continue
            try:
                for lnk in sm_dir.rglob("*.lnk"):
                    if name_clean in lnk.stem.lower():
                        try:
                            os.startfile(str(lnk))
                            time.sleep(0.5)
                            return {"success": True, "data": f"Opened '{lnk.stem}' from Start Menu"}
                        except Exception:
                            continue
            except (PermissionError, OSError):
                continue

        # Step 2: Search for .exe in Program Files and AppData
        found = self._find_exe_on_windows(name_clean)
        if found:
            try:
                os.startfile(str(found))
                time.sleep(0.5)
                return {"success": True, "data": f"Opened '{found.name}' from {found.parent}"}
            except Exception:
                pass

        # Step 3: Try `where` command to find in PATH
        # (name_clean, NOT app_name -- Arabic aliases are resolved into
        # name_clean, and `where سبوتيفاي` can never succeed)
        try:
            result = subprocess.run(
                ["where", name_clean],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                exe_path = result.stdout.strip().splitlines()[0]
                _spawn([exe_path], timeout_cap=_SUBPROCESS_CAP_SECONDS)
                time.sleep(0.5)
                return {"success": True, "data": f"Opened '{exe_path}'"}
        except Exception:
            pass

        # Step 4: Try direct Popen with common extensions
        for ext in ["", ".exe", ".bat", ".cmd"]:
            try:
                proc = _spawn([f"{name_clean}{ext}"],
                              creationflags=subprocess.CREATE_NO_WINDOW,
                              timeout_cap=_SUBPROCESS_CAP_SECONDS)
                time.sleep(0.5)
                if proc.poll() is None:
                    return {"success": True, "data": f"Launched '{name_clean}{ext}'"}
            except (FileNotFoundError, OSError):
                continue

        # Step 5: URI handlers only (spotify:, steam:, mailto:...). A blind
        # `cmd /c start <word>` returns exit 0 even when the app does not
        # exist -- it would report success for garbage like "\u0641\u064a\u0643".
        if ":" in app_name and not app_name.startswith(("/", "\\")):
            try:
                _spawn(["cmd", "/c", "start", "", app_name],
                       creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(0.5)
                return {"success": True, "data": f"Opened URI '{app_name}'"}
            except Exception:
                pass

        return {
            "success": False,
            "error": (
                f"Could not find or open '{app_name}'. "
                f"I searched: Start Menu, Program Files, AppData, PATH, and system directories. "
                f"Try the full path (e.g. C:/Program Files/.../app.exe) "
                f"or check the exact name."
            ),
        }

        # Strategy 2: Search Start Menu shortcuts (.lnk files)
        start_menu_dirs = [
            HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path("C:\\ProgramData" / "Microsoft" / "Windows" / "Start Menu" / "Programs"),
        ]
        for sm_dir in start_menu_dirs:
            if not sm_dir.exists():
                continue
            try:
                for lnk in sm_dir.rglob("*.lnk"):
                    if name_clean in lnk.stem.lower():
                        try:
                            os.startfile(str(lnk))
                            return {"success": True, "data": f"Opened '{lnk.stem}'"}
                        except Exception:
                            continue
            except (PermissionError, OSError):
                continue

        # Strategy 3: Search for .exe in common install directories
        found = self._find_exe_on_windows(name_clean)
        if found:
            try:
                os.startfile(str(found))
                return {"success": True, "data": f"Opened '{found.name}'"}
            except Exception:
                pass

        # Strategy 4: Use PowerShell to launch via AppxPackage (Store/UWP apps)
        try:
            ps_cmd = f"(Get-AppxPackage -AllUsers | Where-Object {{$_.Name -like '*{name_clean}*'}}).InstallLocation + '\\AppxManifest.xml'"
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            manifest = result.stdout.strip()
            if manifest and os.path.exists(manifest):
                ps_launch = f"Start-Process 'shell:AppsFolder\\{(Path(manifest).parent.name)}'"
                subprocess.run(
                    ["powershell", "-Command", ps_launch],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10,
                )
                return {"success": True, "data": f"Opened '{app_name}' (Store app)"}
        except Exception:
            pass

        # Strategy 5: Use `where` to find in PATH
        try:
            result = subprocess.run(
                ["where", app_name],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                exe_path = result.stdout.strip().split("\n")[0]
                os.startfile(exe_path)
                return {"success": True, "data": f"Opened '{exe_path}'"}
        except Exception:
            pass

        # Strategy 6: Try `start` with common extensions
        for ext in [".exe", ".bat", ".cmd", ".ps1", ".msc"]:
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", f"{name_clean}{ext}"],
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(0.3)
                return {"success": True, "data": f"Opened '{name_clean}{ext}'"}
            except Exception:
                continue

        # Strategy 7: Try `explorer` for folder-like names
        if any(kw in name_lower for kw in ["folder", "dir", "explorer"]):
            try:
                _spawn(["explorer"], timeout_cap=_SUBPROCESS_CAP_SECONDS)
                return {"success": True, "data": "Opened File Explorer"}
            except Exception:
                pass

        return {
            "success": False,
            "error": (
                f"Could not find '{app_name}'. "
                f"Try the full path (e.g. C:\\Program Files\\...\\app.exe) "
                f"or the exact executable name."
            ),
        }

    def run_command(self, command: str, timeout: int = 30, **kwargs) -> Dict[str, Any]:
        """Run a shell command and return output.

        The command is capped at the smaller of `timeout` and the global
        _SUBPROCESS_CAP_SECONDS so a runaway command can't violate the 5s
        reply ceiling.
        """
        effective_timeout = min(timeout, max(1, _SUBPROCESS_CAP_SECONDS))
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
            )
            output = result.stdout.strip()
            errors = result.stderr.strip()
            return {
                "success": result.returncode == 0,
                "data": output or errors or "(no output)",
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False,
                    "error": f"Command timed out after {effective_timeout}s (capped)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def system_info(self, **kwargs) -> Dict[str, Any]:
        """Get system information: OS, CPU, RAM, disk, etc."""
        try:
            import psutil
            info = {
                "os": f"{platform.system()} {platform.release()}",
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python": platform.python_version(),
                "cpu_count": psutil.cpu_count(),
                "cpu_percent": f"{psutil.cpu_percent(interval=1)}%",
                "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
                "ram_used_gb": round(psutil.virtual_memory().used / (1024**3), 1),
                "ram_percent": f"{psutil.virtual_memory().percent}%",
                "disk_total_gb": round(psutil.disk_usage("/").total / (1024**3), 1),
                "disk_used_gb": round(psutil.disk_usage("/").used / (1024**3), 1),
                "disk_percent": f"{psutil.disk_usage('/').percent}%",
            }
            # Battery
            bat = psutil.sensors_battery()
            if bat:
                info["battery_percent"] = f"{bat.percent}%"
                info["battery_plugged"] = bat.power_plugged
            return {"success": True, "data": info}
        except ImportError:
            # Fallback without psutil
            info = {
                "os": f"{platform.system()} {platform.release()}",
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python": platform.python_version(),
            }
            return {"success": True, "data": info}

    def list_processes(self, filter_name: str = "", **kwargs) -> Dict[str, Any]:
        """List running processes, optionally filtered by name."""
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if filter_name and filter_name.lower() not in info["name"].lower():
                        continue
                    procs.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "cpu": f"{info['cpu_percent']:.1f}%",
                        "memory": f"{info['memory_percent']:.1f}%",
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            # Sort by memory usage
            procs.sort(key=lambda x: float(x["memory"].rstrip("%")), reverse=True)
            return {"success": True, "data": procs[:30]}  # Top 30
        except ImportError:
            # Fallback: use tasklist on Windows
            result = self.run_command("tasklist /FO CSV /NH")
            if result["success"]:
                lines = result["data"].split("\n")
                procs = []
                for line in lines[:30]:
                    parts = line.strip().strip('"').split('","')
                    if len(parts) >= 2:
                        name = parts[0]
                        if filter_name and filter_name.lower() not in name.lower():
                            continue
                        procs.append({"name": name, "pid": parts[1], "memory": parts[4] if len(parts) > 4 else "?"})
                return {"success": True, "data": procs}
            return {"success": False, "error": "Could not list processes"}

    def kill_process(self, name: str = "", pid: int = 0, **kwargs) -> Dict[str, Any]:
        """Kill a process by name or PID."""
        try:
            if pid:
                import psutil
                p = psutil.Process(pid)
                p.terminate()
                return {"success": True, "data": f"Terminated process PID {pid} ({p.name()})"}
            elif name:
                if platform.system() == "Windows":
                    result = self.run_command(f"taskkill /IM {name} /F")
                else:
                    result = self.run_command(f"killall {name}")
                return {"success": result["success"], "data": result.get("data", result.get("error", ""))}
            else:
                return {"success": False, "error": "Provide name or pid"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def file_manager(
        self,
        action: str,
        path: str = "",
        destination: str = "",
        content: str = "",
        pattern: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """File management: list, read, write, copy, move, delete, search, mkdir."""
        try:
            p = Path(path).expanduser() if path else None

            if action == "list":
                target = p or HOME
                entries = []
                for entry in sorted(target.iterdir()):
                    entries.append({
                        "name": entry.name,
                        "type": "folder" if entry.is_dir() else "file",
                        "size": self._human_size(entry.stat().st_size) if entry.is_file() else "-",
                    })
                return {"success": True, "data": {"path": str(target), "entries": entries[:50]}}

            elif action == "read":
                if not p or not p.exists():
                    return {"success": False, "error": f"File not found: {path}"}
                text = p.read_text(encoding="utf-8", errors="replace")
                return {"success": True, "data": text[:5000]}  # Limit to 5KB

            elif action == "write":
                if not p:
                    return {"success": False, "error": "No path specified"}
                if p.exists():
                    _stash_original(p)
                    _record_undo({"kind": "write", "original": str(p), "backup": str(UNDO_TRASH / p.name)})
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return {"success": True, "data": f"Wrote {len(content)} bytes to {p}"}

            elif action == "copy":
                dest = Path(destination).expanduser()
                if not p or not p.exists():
                    return {"success": False, "error": f"Source not found: {path}"}
                if p.is_dir():
                    shutil.copytree(str(p), str(dest))
                else:
                    shutil.copy2(str(p), str(dest))
                _record_undo({"kind": "copy", "src": str(p), "dst": str(dest)})
                return {"success": True, "data": f"Copied {p.name} to {dest}"}

            elif action == "move":
                dest = Path(destination).expanduser()
                if not p or not p.exists():
                    return {"success": False, "error": f"Source not found: {path}"}
                shutil.move(str(p), str(dest))
                _record_undo({"kind": "move", "src": str(p), "dst": str(dest)})
                return {"success": True, "data": f"Moved {p.name} to {dest}"}

            elif action == "delete":
                if not p or not p.exists():
                    return {"success": False, "error": f"Not found: {path}"}
                _stash_original(p)
                if p.is_dir():
                    shutil.rmtree(str(p))
                else:
                    p.unlink()
                _record_undo({"kind": "delete", "original": str(p), "backup": str(UNDO_TRASH / p.name)})
                return {"success": True, "data": f"Deleted {p.name} (undoable)"}

            elif action == "mkdir":
                if not p:
                    return {"success": False, "error": "No path specified"}
                p.mkdir(parents=True, exist_ok=True)
                return {"success": True, "data": f"Created directory {p}"}

            elif action == "search":
                # Search for files matching a pattern
                if not p:
                    p = HOME
                pat = pattern or "*.*"
                matches = []
                for match in p.rglob(pat):
                    if match.is_file():
                        matches.append({
                            "name": match.name,
                            "path": str(match),
                            "size": self._human_size(match.stat().st_size),
                        })
                        if len(matches) >= 50:
                            break
                return {"success": True, "data": matches}

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def screenshot(self, save_path: str = "", **kwargs) -> Dict[str, Any]:
        """Take a screenshot. Returns the file path."""
        try:
            if not save_path:
                save_path = str(DESKTOP / f"screenshot_{int(time.time())}.png")

            if platform.system() == "Windows":
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grab()
                    img.save(save_path)
                    return {"success": True, "data": f"Screenshot saved to {save_path}"}
                except ImportError:
                    # Fallback: use PowerShell for screenshot
                    ps_script = (
                        'Add-Type -AssemblyName System.Windows.Forms; '
                        'Add-Type -AssemblyName System.Drawing; '
                        '$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; '
                        '$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height); '
                        '$gfx = [System.Drawing.Graphics]::FromImage($bmp); '
                        '$gfx.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); '
                        f"$bmp.Save('{save_path}')"
                    )
                    result = self.run_command(f'powershell -Command "{ps_script}"')
                    if os.path.exists(save_path):
                        return {"success": True, "data": f"Screenshot saved to {save_path}"}
                    return {"success": False, "error": "Screenshot failed. Install Pillow: pip install Pillow"}

            elif platform.system() == "Darwin":
                subprocess.run(["screencapture", save_path])
                return {"success": True, "data": f"Screenshot saved to {save_path}"}
            else:
                subprocess.run(["scrot", save_path])
                return {"success": True, "data": f"Screenshot saved to {save_path}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def clipboard(self, action: str = "get", text: str = "", **kwargs) -> Dict[str, Any]:
        """Get or set clipboard content."""
        try:
            if action == "get":
                if platform.system() == "Windows":
                    result = subprocess.run(
                        ["powershell", "-command", "Get-Clipboard"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    return {"success": True, "data": result.stdout.strip() or "(empty clipboard)"}
                elif platform.system() == "Darwin":
                    result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
                    return {"success": True, "data": result.stdout.strip() or "(empty clipboard)"}
                else:
                    result = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=5)
                    return {"success": True, "data": result.stdout.strip() or "(empty clipboard)"}

            elif action == "set":
                if platform.system() == "Windows":
                    # _spawn with explicit stdin=PIPE: the clip process NEEDS
                    # a pipe for its input, while stdout/stderr must point at
                    # null (invalid inherited handles crash frozen builds).
                    process = _spawn(["clip"], stdin=subprocess.PIPE)
                    process.communicate(text.encode("utf-16le"))
                    return {"success": True, "data": f"Clipboard set ({len(text)} chars)"}
                elif platform.system() == "Darwin":
                    process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                    process.communicate(text.encode("utf-8"))
                    return {"success": True, "data": f"Clipboard set ({len(text)} chars)"}
                else:
                    process = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
                    process.communicate(text.encode("utf-8"))
                    return {"success": True, "data": f"Clipboard set ({len(text)} chars)"}

            return {"success": False, "error": f"Unknown clipboard action: {action}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def volume(self, action: str = "get", level: int = 0, **kwargs) -> Dict[str, Any]:
        """Get or set system volume."""
        try:
            if platform.system() == "Windows":
                nircmd = shutil.which("nircmd")
                if action == "set" and nircmd:
                    subprocess.run([nircmd, "setsysvolume", str(int(level * 655.35))])
                    return {"success": True, "data": f"Volume set to {level}%"}
                return {"success": True, "data": "Volume control available. Use system tray or install nircmd for full control."}
            return {"success": False, "error": "Volume control requires additional setup"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_url(self, url: str, **kwargs) -> Dict[str, Any]:
        """Open a URL in the default browser."""
        try:
            import webbrowser
            webbrowser.open(url)
            return {"success": True, "data": f"Opened {url}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def spotify_play(self, query: str, **kwargs) -> Dict[str, Any]:
        """Search Spotify and actually PLAY the top result.

        Strategy (synthetic keys are ignored by the Spotify desktop app,
        and its UI is a Chrome view that hides its accessibility tree):
          1. open spotify:search:<query> via the OS protocol handler
          2. Spotify's renderer exposes buttons named "Play <track>"
             once accessibility is forced -- click the first one through
             the UI Automation tree (pywinauto)
          3. VERIFY playback: a playing window title contains " - "
             ("Artist - Track"); if it never changes, report failure
             honestly.
        """
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "No search query given."}
        try:
            from urllib.parse import quote
            uri = f"spotify:search:{quote(query)}"
            if platform.system() == "Windows":
                os.startfile(uri)
            else:
                import webbrowser
                webbrowser.open(uri)
        except Exception as e:
            return {"success": False,
                    "error": (f"Could not launch Spotify (is it installed "
                              f"and its protocol registered?): {e}")}

        # Wait for the Spotify window, then for results to render
        focused = False
        for _ in range(8):
            time.sleep(1.0)
            try:
                res = self.focus_window("spotify")
                focused = bool(res.get("focused"))
                if focused:
                    break
            except Exception:
                pass
        if not focused:
            return {"success": False,
                    "error": ("Spotify window did not appear within 8s -- "
                              "is it installed and logged in?")}
        time.sleep(4.0)  # results render

        played, track_label = self._spotify_uia_click_play(query)
        if not played:
            return {"success": False,
                    "error": (f"Found the Spotify search page for '{query}' "
                              "but could not click a Play button -- the "
                              "results may still be loading. Try again.")}

        # Verify: on Spotify Free an AD may play first and the track can
        # take ~10s to start, so poll the window title cheaply (ctypes, no
        # subprocesses) for up to ~30s for it to leave "Spotify Free".
        # While playing, the title is "Artist - Track" (no "Spotify" in it).
        pids = self._spotify_pids()
        for _ in range(30):
            time.sleep(1.0)
            t = self._spotify_window_title(pids)
            if t and t.strip().lower() not in ("", "spotify free"):
                note = ""
                if " - " not in t:
                    note = (" (an ad may be playing first on Spotify Free "
                            "-- your track follows)")
                return {"success": True,
                        "data": f"Playing now: {t.strip()}{note}"}
        return {"success": False,
                "error": (f"Clicked play for '{query}' but playback never "
                          "started -- the client may be logged out or the "
                          "track blocked in your region.")}

    def _spotify_window_title(self, pids: Optional[set] = None) -> str:
        """Main-window title of the Spotify process via ctypes (cheap)."""
        if not pids:
            pids = self._spotify_pids()
        if not pids:
            return ""
        try:
            import ctypes
            import ctypes.wintypes as wt
            user32 = ctypes.windll.user32
            titles: list = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wt.HWND, wt.LPARAM)

            def _cb(hwnd, _lparam):
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids and user32.IsWindowVisible(hwnd):
                    n = user32.GetWindowTextLengthW(hwnd)
                    if n:
                        buf = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(hwnd, buf, n + 1)
                        titles.append(buf.value)
                return True

            user32.EnumWindows(EnumWindowsProc(_cb), 0)
            return titles[0] if titles else ""
        except Exception:
            return ""

    def _spotify_pids(self) -> set:
        """PIDs of all running spotify.exe processes (empty on failure)."""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq spotify.exe", "/FO", "CSV"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            return {int(line.split(",")[1].strip('"'))
                    for line in out.splitlines()
                    if line.lower().startswith('"spotify')}
        except Exception:
            return set()

    def _spotify_uia_click_play(self, query: str,
                                timeout: float = 10.0) -> tuple:
        """Click the first 'Play <track>' button in Spotify's UIA tree.

        Returns (clicked: bool, label: str). Never raises.
        """
        try:
            from pywinauto import Desktop
        except ImportError:
            logger.warning("pywinauto not installed -- cannot click Play.")
            return False, ""
        deadline = time.time() + timeout
        best = ""
        while time.time() < deadline:
            try:
                # Match by PROCESS + class, not title: while music plays the
                # title is "Artist - Track" with no "Spotify" in it, and the
                # Chrome_WidgetWin_1 class is shared with Discord/Chrome.
                spotify_pids = self._spotify_pids()
                win = None
                for w in Desktop(backend="uia").windows():
                    if (w.element_info.class_name == "Chrome_WidgetWin_1"
                            and w.element_info.process_id in spotify_pids):
                        win = w
                        break
                if win is None:
                    raise RuntimeError("no Spotify top-level window")
                for c in win.descendants():
                    if c.element_info.control_type != "Button":
                        continue
                    nm = c.element_info.name or ""
                    if nm.lower().startswith("play "):
                        # Prefer a row matching the query words
                        if not best:
                            best = nm
                        ql = query.lower()
                        words = [w for w in re.split(r"\W+", ql) if len(w) > 2]
                        score = sum(1 for w in words if w in nm.lower())
                        if score >= max(1, len(words) // 2):
                            best = nm
                            c.invoke()
                            time.sleep(1.0)
                            return True, nm
                if best:
                    # No query-matched button yet; click the first Play.
                    for c in win.descendants():
                        if (c.element_info.control_type == "Button"
                                and (c.element_info.name or "").lower()
                                .startswith("play ")):
                            c.invoke()
                            time.sleep(1.0)
                            return True, best
            except Exception as e:  # noqa: BLE001 -- window not found yet
                logger.warning("UIA play-click retry: %s", str(e)[:120])
            time.sleep(1.0)
        return False, ""

    def type_text(self, text: str, delay: float = 0.02, **kwargs) -> Dict[str, Any]:
        """Type text using keyboard simulation."""
        try:
            if platform.system() == "Windows":
                # Use pyautogui or ctypes
                try:
                    import pyautogui
                    pyautogui.typewrite(text, interval=delay)
                    return {"success": True, "data": f"Typed {len(text)} characters"}
                except ImportError:
                    # Use ctypes SendInput
                    import ctypes
                    for char in text:
                        if char == " ":
                            ctypes.windll.user32.SendInput(1, ctypes.windll.user32.VkKeyScan(0x20), 0)
                        elif char.isalnum() or char in ".,;:!?-()[]{}'\"/@#$%^&*+=<>~`|\\_":
                            # Use clipboard approach for Unicode
                            subprocess.run(["clip"], input=text.encode("utf-16le"), check=True)
                            pyautogui.hotkey("ctrl", "v")
                            return {"success": True, "data": f"Typed {len(text)} characters via clipboard"}
                    return {"success": True, "data": f"Typed {len(text)} characters"}
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'])
                return {"success": True, "data": f"Typed {len(text)} characters"}
            return {"success": False, "error": "Typing requires pyautogui: pip install pyautogui"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def hotkey(self, keys: str, **kwargs) -> Dict[str, Any]:
        """Press a keyboard shortcut. Keys format: 'ctrl+c', 'alt+f4', 'win+r'."""
        try:
            try:
                import pyautogui
                key_parts = [k.strip() for k in keys.split("+")]
                pyautogui.hotkey(*key_parts)
                return {"success": True, "data": f"Pressed {keys}"}
            except ImportError:
                # Windows fallback using ctypes
                if platform.system() == "Windows":
                    VK_MAP = {
                        "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
                        "tab": 0x09, "enter": 0x0D, "escape": 0x1B, "space": 0x20,
                        "delete": 0x2E, "backspace": 0x08,
                    }
                    key_parts = [k.strip().lower() for k in keys.split("+")]
                    pressed = []
                    for key in key_parts:
                        vk = VK_MAP.get(key)
                        if vk is None and len(key) == 1:
                            vk = ord(key.upper())
                        if vk:
                            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                            pressed.append(vk)
                    for vk in reversed(pressed):
                        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
                    return {"success": True, "data": f"Pressed {keys}"}
                return {"success": False, "error": "Install pyautogui: pip install pyautogui"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def control_pc(self, action: str, **kwargs) -> Dict[str, Any]:
        """System control: shutdown, restart, sleep, lock, mute, unmute.

        shutdown/restart/sleep/hibernate are gated behind an explicit user
        confirmation in the UI -- Jarvis cannot confirm its own action.
        """
        try:
            if platform.system() == "Windows":
                if action == "shutdown":
                    gate_result = CONFIRMATION_GATE.request("shutdown", "The PC will shut down.", timeout=90)
                    if not gate_result.get("success"):
                        return gate_result
                    self.run_command("shutdown /s /t 15")
                    return {"success": True, "data": "Shutting down in 15 seconds. Say 'cancel shutdown' to abort."}
                elif action == "restart":
                    gate_result = CONFIRMATION_GATE.request("restart", "The PC will restart.", timeout=90)
                    if not gate_result.get("success"):
                        return gate_result
                    self.run_command("shutdown /r /t 15")
                    return {"success": True, "data": "Restarting in 15 seconds. Say 'cancel shutdown' to abort."}
                elif action == "cancel":
                    CONFIRMATION_GATE.abort()
                    self.run_command("shutdown /a")
                    return {"success": True, "data": "Shutdown cancelled."}
                elif action == "lock":
                    self.run_command("rundll32.exe user32.dll,LockWorkStation")
                    return {"success": True, "data": "Workstation locked."}
                elif action == "sleep":
                    gate_result = CONFIRMATION_GATE.request("sleep", "The PC will go to sleep.", timeout=90)
                    if not gate_result.get("success"):
                        return gate_result
                    self.run_command("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
                    return {"success": True, "data": "Going to sleep..."}
                elif action == "hibernate":
                    gate_result = CONFIRMATION_GATE.request("hibernate", "The PC will hibernate.", timeout=90)
                    if not gate_result.get("success"):
                        return gate_result
                    self.run_command("rundll32.exe powrprof.dll,SetSuspendState 1")
                    return {"success": True, "data": "Hibernating..."}
            return {"success": False, "error": f"Unknown action: {action}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def notify(self, title: str, message: str, **kwargs) -> Dict[str, Any]:
        """Show a desktop notification."""
        try:
            if platform.system() == "Windows":
                # Use PowerShell toast notification
                ps_cmd = f'''
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
                [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
                $template = @"
                <toast>
                    <visual>
                        <binding template="ToastGeneric">
                            <text>{title}</text>
                            <text>{message}</text>
                        </binding>
                    </visual>
                </toast>
"@
                $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
                $xml.LoadXml($template)
                $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Jarvis").Show($toast)
                '''
                subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=10)
                return {"success": True, "data": f"Notification sent: {title}"}
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}"'])
                return {"success": True, "data": f"Notification sent: {title}"}
            else:
                subprocess.run(["notify-send", title, message])
                return {"success": True, "data": f"Notification sent: {title}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def web_search(self, query: str, **kwargs) -> Dict[str, Any]:
        """Open a web search in the default browser."""
        try:
            import webbrowser
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            webbrowser.open(url)
            return {"success": True, "data": f"Opened search: {query}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_file(self, url: str, filename: str = "", **kwargs) -> Dict[str, Any]:
        """Download a file from a URL."""
        try:
            import urllib.request
            if not filename:
                filename = url.split("/")[-1] or "download"
            dest = DOWNLOADS / filename
            urllib.request.urlretrieve(url, str(dest))
            return {"success": True, "data": f"Downloaded to {dest} ({self._human_size(dest.stat().st_size)})"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Helpers ──────────────────────────────────────────────────

    def _find_exe_on_windows(self, name: str) -> Optional[Path]:
        """Search for an application executable on Windows. Fast: only checks top-level dirs."""
        name_lower = name.lower().replace(".exe", "")

        # Fast search: check common install locations, only 2 levels deep
        search_bases = [
            HOME / "AppData" / "Local" / "Programs",
            HOME / "AppData" / "Roaming",
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            HOME / "Desktop",
        ]

        for base in search_bases:
            if not base.exists():
                continue
            try:
                # Level 1: direct children
                for child in base.iterdir():
                    if name_lower in child.stem.lower():
                        if child.suffix.lower() in (".exe", ".lnk", ".appref-ms"):
                            return child
                    # Level 2: one subdir deep
                    if child.is_dir():
                        try:
                            for sub in child.iterdir():
                                if name_lower in sub.stem.lower():
                                    if sub.suffix.lower() in (".exe", ".lnk", ".appref-ms"):
                                        return sub
                                # Level 3: common subfolder names
                                if sub.is_dir() and sub.name.lower() in ("bin", "app", "launcher", "current"):
                                    try:
                                        for exe in sub.iterdir():
                                            if name_lower in exe.stem.lower() and exe.suffix.lower() == ".exe":
                                                return exe
                                    except (PermissionError, OSError):
                                        continue
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
        return None

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Convert bytes to human-readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"


# ── Gemini Tool Declarations ────────────────────────────────────

def get_pc_tool_declarations():
    """Return Gemini FunctionDeclarations for all PC control tools."""
    from google.genai import types

    # GUI automation / web scraping / browser automation suites
    # (imported lazily so a missing optional dep can't break the core)
    try:
        from jarvis.tools.gui_automation import get_declarations as _gui_decls
        gui_decls = _gui_decls()
    except Exception as _e:  # noqa: BLE001
        logger.warning("GUI automation declarations unavailable: %s", _e)
        gui_decls = []
    try:
        from jarvis.tools.web_scraper import get_declarations as _ws_decls
        ws_decls = _ws_decls()
    except Exception as _e:  # noqa: BLE001
        logger.warning("Web scraper declarations unavailable: %s", _e)
        ws_decls = []
    try:
        from jarvis.tools.browser_automation import (
            get_declarations as _ba_decls)
        ba_decls = _ba_decls()
    except Exception as _e:  # noqa: BLE001
        logger.warning("Browser automation declarations unavailable: %s", _e)
        ba_decls = []

    return [
        types.FunctionDeclaration(
            name="pc_focus_window",
            description="Bring an application's window to the foreground (switch to it). Use when the user asks to 'switch to', 'focus', 'bring up', or 'show' an app that is likely already running. Prefer this over opening a second copy.",
            parameters={
                "type": "object",
                "properties": {
                    "window_name": {
                        "type": "string",
                        "description": "App or window name to focus, e.g. 'discord', 'chrome', 'Visual Studio Code'.",
                    }
                },
                "required": ["window_name"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_open_app",
            description="Open an application on the PC. Pass the app name (e.g. 'notepad', 'chrome', 'code', 'spotify', 'calculator'). Works with any installed application.",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application to open. Can be the app name, executable name, or full path.",
                    }
                },
                "required": ["app_name"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_spotify_play",
            description=("Search Spotify for a song, artist, or album and start "
                         "playing the top result. Use for requests like 'play Fake "
                         "Hacker by Danielle' or '\u0634\u0644\u0648\u0646 \u0627\u063a\u0646\u064a\u0629 \u0643\u0630\u0627'. "
                         "Pass the song/artist as the query. NOT for opening the "
                         "Spotify app itself -- use pc_open_app for that."),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search: song title, artist, or both (e.g. 'Fake Hacker Danielle').",
                    }
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_run_command",
            description="Run a shell command on the PC and return the output. Use for system tasks like checking IP, listing network info, running scripts, etc. Be careful with destructive commands.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30).",
                    },
                },
                "required": ["command"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_system_info",
            description="Get system information: OS, CPU, RAM, disk usage, battery status. Use when the user asks about their PC specs or system status.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ),
        types.FunctionDeclaration(
            name="pc_file_manager",
            description="Manage files on the PC. Actions: list (browse folders), read (open file), write (create/edit file), copy, move, delete, mkdir (create folder), search (find files).",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "read", "write", "copy", "move", "delete", "mkdir", "search"],
                        "description": "The file operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to the file or folder. Use ~ for home directory.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path for copy/move operations.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write to a file (for write action).",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern for search (e.g. '*.pdf', '*.py').",
                    },
                },
                "required": ["action"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_screenshot",
            description="Take a screenshot of the current screen and save it. Returns the file path.",
            parameters={
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "Where to save the screenshot. Defaults to Desktop.",
                    },
                },
            },
        ),
        types.FunctionDeclaration(
            name="pc_screen_vision",
            description=(
                "Capture the user's current screen and look at it. Use whenever the "
                "user asks about anything visible on their display -- e.g. 'what am I "
                "looking at', 'read this error message', 'what does this window say', "
                "'describe my screen', 'what is open right now'. You will receive the "
                "actual screenshot image to analyze in the tool result."
            ),
            parameters={"type": "object", "properties": {}},
        ),
        types.FunctionDeclaration(
            name="pc_list_processes",
            description="List running processes on the PC. Optionally filter by name. Shows PID, name, CPU and memory usage.",
            parameters={
                "type": "object",
                "properties": {
                    "filter_name": {
                        "type": "string",
                        "description": "Filter processes by name (partial match). Leave empty for all.",
                    },
                },
            },
        ),
        types.FunctionDeclaration(
            name="pc_kill_process",
            description="Kill/terminate a running process by name or PID.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Process name to kill (e.g. 'chrome.exe').",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "Process ID to kill.",
                    },
                },
            },
        ),
        types.FunctionDeclaration(
            name="pc_clipboard",
            description="Get or set the clipboard contents.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "set"],
                        "description": "Get reads clipboard, set writes to clipboard.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to set in clipboard (for set action).",
                    },
                },
                "required": ["action"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_control",
            description="System power control: shutdown, restart, sleep, hibernate, lock screen, or cancel a pending shutdown.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["shutdown", "restart", "sleep", "hibernate", "lock", "cancel"],
                        "description": "System control action to perform.",
                    },
                },
                "required": ["action"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_notify",
            description="Show a desktop notification popup on the PC.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification message body.",
                    },
                },
                "required": ["title", "message"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_open_url",
            description="Open a URL in the default web browser.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open (e.g. 'https://google.com').",
                    },
                },
                "required": ["url"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_web_search",
            description="Search the web using Google and open results in the browser.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_type_text",
            description="Type text into the currently focused window using keyboard simulation. Use this for filling forms, entering commands, etc.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to type.",
                    },
                },
                "required": ["text"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_hotkey",
            description="Press a keyboard shortcut. Keys are joined with '+'. Examples: 'ctrl+c' (copy), 'ctrl+v' (paste), 'alt+f4' (close), 'win+r' (run dialog), 'ctrl+shift+esc' (task manager).",
            parameters={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "string",
                        "description": "Key combination separated by '+'. E.g. 'ctrl+alt+delete', 'alt+tab'.",
                    },
                },
                "required": ["keys"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_undo",
            description="Undo the most recent undoable file operation (move, copy, delete, or file overwrite). Use when the user says 'undo' after a file operation.",
            parameters={"type": "object", "properties": {}},
        ),
        types.FunctionDeclaration(
            name="pc_download_file",
            description="Download a file from a URL and save it to the Downloads folder.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to download from.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Custom filename. If omitted, uses the URL filename.",
                    },
                },
                "required": ["url"],
            },
        ),
    ] + gui_decls + ws_decls + ba_decls


# ── Tool executor ───────────────────────────────────────────────

_CONTROLLER = PCController()


# ── Lazy wrappers for the automation suites (PyAutoGUI / bs4 / Selenium) ──
# Lazy imports keep module load fast and a missing optional dep only
# fails the individual tool call instead of breaking pc_control entirely.

def _gui_mouse(**kw):
    from jarvis.tools.gui_automation import mouse_action
    return mouse_action(**kw)


def _gui_locate(**kw):
    from jarvis.tools.gui_automation import locate_and_click
    return locate_and_click(**kw)


def _gui_drag(**kw):
    from jarvis.tools.gui_automation import drag_and_drop
    return drag_and_drop(**kw)


def _web_scrape(**kw):
    from jarvis.tools.web_scraper import scrape_page
    return scrape_page(**kw)


def _web_browser(**kw):
    from jarvis.tools.browser_automation import browser_interact
    return browser_interact(**kw)


# Map tool names to methods
_TOOL_MAP = {
    "pc_focus_window": _CONTROLLER.focus_window,
    "pc_open_app": _CONTROLLER.open_app,
    "pc_run_command": _CONTROLLER.run_command,
    "pc_system_info": _CONTROLLER.system_info,
    "pc_file_manager": _CONTROLLER.file_manager,
    "pc_screenshot": _CONTROLLER.screenshot,
    "pc_list_processes": _CONTROLLER.list_processes,
    "pc_kill_process": _CONTROLLER.kill_process,
    "pc_undo": undo_last_operation,
    "pc_clipboard": _CONTROLLER.clipboard,
    "pc_control": _CONTROLLER.control_pc,
    "pc_notify": _CONTROLLER.notify,
    "pc_open_url": _CONTROLLER.open_url,
    "pc_spotify_play": _CONTROLLER.spotify_play,

    # GUI automation (PyAutoGUI)
    "pc_mouse_action": _gui_mouse,
    "pc_locate_and_click": _gui_locate,
    "pc_drag_and_drop": _gui_drag,

    # Static web scraping (requests + BeautifulSoup4)
    "web_scrape_page": _web_scrape,

    # Dynamic browser automation (Selenium, managed session)
    "web_browser_interact": _web_browser,
    "pc_web_search": _CONTROLLER.web_search,
    "pc_type_text": _CONTROLLER.type_text,
    "pc_hotkey": _CONTROLLER.hotkey,
    "pc_download_file": _CONTROLLER.download_file,
    # Normally intercepted by the brain's vision pipeline (the PNG is
    # attached as an inline image part). This stub covers direct calls.
    "pc_screen_vision": lambda **kw: {
        "success": True,
        "data": ("Screenshot captured via vision pipeline; the image is "
                 "delivered to the model as an inline image part."),
    },
}


def execute_pc_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Execute a PC control tool by name. Returns JSON string result."""
    func = _TOOL_MAP.get(tool_name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        result = func(**arguments)
        return json.dumps(result, default=str, indent=2)
    except Exception as e:
        logger.error(f"PC tool '{tool_name}' failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


def is_pc_tool(tool_name: str) -> bool:
    """Check if a tool name is a local PC control tool."""
    return tool_name in _TOOL_MAP

"""
Jarvis -- Personal AI Assistant
Entry point: initializes brain, voice, and launches the GUI.

Usage:
    python -m jarvis.main
    python -m jarvis --cli
"""

import argparse
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

from jarvis.config import (
    COMPOSIO_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    WINDOW_TITLE,
)

# -- Logging Setup --

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# Windowed PyInstaller builds have NO stdout -- log to a file next to the
# data dir so exe crashes are diagnosable (dev console keeps stream output).
_handlers = []
try:
    sys.stdout  # raises AttributeError in pythonw/frozen builds
    _handlers.append(logging.StreamHandler(sys.stdout))
except (AttributeError, OSError):
    pass
try:
    from jarvis.config import DATA_DIR
    _log_file = DATA_DIR / "jarvis.log"
    _handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=_handlers)
logger = logging.getLogger("jarvis")

# Suppress noisy comtypes debug logs (Phoneme, Viseme, etc.)
logging.getLogger("comtypes").setLevel(logging.WARNING)
logging.getLogger("comtypes.client._events").setLevel(logging.WARNING)
logging.getLogger("comtypes._vtbl").setLevel(logging.WARNING)
logging.getLogger("comtypes._comobject").setLevel(logging.WARNING)
logging.getLogger("comtypes._post_coinit.unknwn").setLevel(logging.WARNING)
logging.getLogger("comtypes.client._managing").setLevel(logging.WARNING)
logging.getLogger("comtypes.client._create").setLevel(logging.WARNING)
logging.getLogger("comtypes.client._code_cache").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def print_banner():
    """Print the Jarvis startup banner."""
    print()
    print("    +----------------------------------------+")
    print("    |                                        |")
    print("    |           J A R V I S                  |")
    print("    |                                        |")
    print("    |      Personal AI Assistant v1.0        |")
    print("    |                                        |")
    print("    +----------------------------------------+")
    print()

    # Primary AI + fallback provider chain
    checks = [
        ("Gemini AI", GEMINI_API_KEY),
        ("Composio", COMPOSIO_API_KEY),
    ]
    for name, key in checks:
        status = "[OK]" if key else "[--] (not set)"
        print(f"    {name}: {status}")

    import os
    from jarvis.config import FALLBACK_PROVIDERS
    fallbacks = [
        p["provider"] for p in FALLBACK_PROVIDERS
        if os.getenv(f"{p['provider'].upper()}_API_KEY", "")
    ]
    if fallbacks:
        print(f"    Fallback chain: {', '.join(fallbacks)}")
    else:
        print("    Fallback chain: (none configured)")

    if not GEMINI_API_KEY and not GROQ_API_KEY:
        print()
        print("    WARNING: No AI API keys found!")
        print("    Set GEMINI_API_KEY or GROQ_API_KEY in your .env file.")
        print("    Jarvis will run in offline mode.")
        print()

    if not COMPOSIO_API_KEY:
        print()
        print("    NOTE: No Composio API key - Google integrations unavailable.")
        print("    Get your key at: https://dashboard.composio.dev")
        print("    Set COMPOSIO_API_KEY in your .env file.")
        print()


def run_gui():
    """Launch the graphical user interface."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon
    from jarvis.ui.main_window import JarvisWindow
    from jarvis.database import Database
    from jarvis.brain import JarvisBrain
    from jarvis.ui.voice import VoiceEngine

    # Initialize components
    db = Database()
    brain = JarvisBrain(db)
    voice = VoiceEngine()

    # Create Qt app
    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis")
    app.setStyle("Fusion")

    # Set app icon
    icon_path = Path(__file__).parent / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Stark Industries palette for Fusion style
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(6, 8, 13))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(232, 236, 240))
    palette.setColor(QPalette.ColorRole.Base, QColor(10, 13, 20))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(14, 18, 24))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(14, 18, 24))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(232, 236, 240))
    palette.setColor(QPalette.ColorRole.Text, QColor(232, 236, 240))
    palette.setColor(QPalette.ColorRole.Button, QColor(14, 18, 24))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(160, 168, 180))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(0, 212, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 212, 255))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(6, 8, 13))
    app.setPalette(palette)

    # Create and show main window (with db for theme persistence)
    window = JarvisWindow(brain=brain, voice=voice, db=db)
    window.show()

    # Initialize brain asynchronously after window is shown
    from jarvis.scheduler import JarvisScheduler
    scheduler = JarvisScheduler()
    scheduler.set_brain(brain)
    scheduler.set_alert_callback(window.show_alert)
    window._scheduler = scheduler

    def init_brain():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(brain.initialize())
        if success:
            QTimer_singleShot = __import__("PyQt6.QtCore", fromlist=["QTimer"]).QTimer.singleShot
            QTimer_singleShot(0, lambda: window.update_status(
                bool(brain.composio_session),
                f" {brain._tools_description.count(chr(10)) + 1} tools" if brain._tools_description else ""
            ))
            # Start proactive alert scheduler
            scheduler.start()
            QTimer_singleShot(0, lambda: window._append_system(
                "Proactive alerts enabled. Calendar checks every 5 min, email checks every 10 min."
            ))
        loop.close()

    threading.Thread(target=init_brain, daemon=True).start()

    # Exit
    sys.exit(app.exec())


def run_cli():
    """Run in command-line mode (no GUI)."""
    from jarvis.database import Database
    from jarvis.brain import JarvisBrain

    db = Database()
    brain = JarvisBrain(db)

    print()
    print("Jarvis CLI Mode (type 'quit' to exit)")
    print()

    async def run():
        success = await brain.initialize()
        if success:
            print("Jarvis ready!")
            print()
        else:
            print("Jarvis running in limited mode")
            print()

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print("Goodbye!")
                break

            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            if user_input.lower() == "briefing":
                response = await brain.morning_briefing()
            elif user_input.lower().startswith("quick ") or user_input.lower().startswith("max5 "):
                # "quick open spotify" / "max5 play fake hacker by danielle"
                command = user_input.split(None, 1)[1] if len(user_input.split(None, 1)) > 1 else user_input
                response = await brain.execute_quick(command)
            else:
                response = await brain.process(user_input)

            print()
            print(f"Jarvis: {response}")
            print()

    asyncio.run(run())


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Jarvis -- Personal AI Assistant",
        prog="jarvis",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line mode (no GUI)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--install-autostart",
        action="store_true",
        help="Register Jarvis to start with Windows (HKCU Run key), then exit",
    )
    parser.add_argument(
        "--remove-autostart",
        action="store_true",
        help="Remove the Windows autostart registration, then exit",
    )
    parser.add_argument(
        "--selftest-open",
        metavar="APP",
        help="Headless test: open APP via the PC tool executor, log the result, exit",
    )
    args = parser.parse_args()

    # Headless open-app self-test (for verifying frozen exe behavior)
    if args.selftest_open:
        from jarvis.tools.pc_control import execute_pc_tool
        result = execute_pc_tool("pc_open_app", {"app_name": args.selftest_open})
        logger.info("SELFTEST pc_open_app(%s) -> %s", args.selftest_open, result)
        print("SELFTEST RESULT:", result)
        return

    # Autostart management exits immediately after the registry change
    if args.install_autostart or args.remove_autostart:
        from jarvis import autostart
        if args.install_autostart:
            ok = autostart.install()
            print("Autostart ENABLED" if ok else "Autostart install FAILED")
        else:
            ok = autostart.uninstall()
            print("Autostart REMOVED" if ok else "Autostart removal FAILED")
        return

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        # Re-suppress noisy libraries even in debug mode
        for noisy in [
            "comtypes", "comtypes.client", "comtypes.client._events",
            "comtypes._vtbl", "comtypes._comobject", "comtypes.client._managing",
            "comtypes.client._create", "comtypes.client._code_cache",
            "comtypes._post_coinit.unknwn", "httpcore", "httpx",
        ]:
            logging.getLogger(noisy).setLevel(logging.WARNING)

    print_banner()

    if args.cli:
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()

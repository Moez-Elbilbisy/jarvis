"""
Backend connector module for JARVIS UI.

This module provides a clean interface between the visual JARVIS interface
and your backend AI assistant. To connect your existing backend, modify the
`BackendConnector.send_command` method to route requests to your backend
(HTTP API, websocket, direct function call, etc.).

The included DemoBackend simulates responses so the UI is fully testable
without a live backend.
"""

import random
import time
import threading
from typing import Callable, Optional


class DemoBackend:
    """Simulated backend that generates canned responses for demonstration."""

    RESPONSES = [
        "Systems are operating at full capacity, sir.",
        "I've analyzed the data. All parameters are within normal ranges.",
        "Shall I initiate the requested protocol?",
        "Processing complete. The results are displayed on your screen.",
        "I've taken the liberty of compiling that information for you.",
        "All systems online and running at peak efficiency.",
        "I'm here, sir. How may I assist you?",
        "The diagnostic sweep is complete. No anomalies detected.",
        "I've cross-referenced the available data sources.",
        "Right away, sir.",
    ]

    FOLLOWUPS = [
        "Is there anything else you require?",
        "Shall I continue monitoring?",
        "I await your next instruction.",
        "Standing by for further commands.",
    ]

    @staticmethod
    def send(message: str, callback: Callable[[str], None]) -> None:
        """Simulate sending a message to the backend with a delay."""

        def _respond():
            delay = random.uniform(1.2, 2.5)
            time.sleep(delay)
            response = random.choice(DemoBackend.RESPONSES)
            if random.random() > 0.5:
                response += " " + random.choice(DemoBackend.FOLLOWUPS)
            callback(response)

        thread = threading.Thread(target=_respond, daemon=True)
        thread.start()


class BrainBackend:
    """Real backend that uses the JarvisBrain AI orchestrator."""

    def __init__(self, brain):
        self._brain = brain

    @staticmethod
    def send(message, callback, brain=None):
        """Send message to the real AI brain."""
        import asyncio
        import threading
        import logging

        _log = logging.getLogger("jarvis.backend")

        # Store callback in a thread-safe way via the result container
        _result_container = {"callback": callback, "result": None, "done": False}

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(brain.process(message))
                result = str(response)
                _log.info(f"Brain responded: {result[:80]}...")
            except Exception as e:
                result = f"Error: {str(e)}"
                _log.error(f"Brain error: {e}")
            finally:
                loop.close()
            _result_container["result"] = result
            _result_container["done"] = True

        def _poll():
            """Poll from main thread until background work is done."""
            if _result_container["done"]:
                try:
                    _result_container["callback"](_result_container["result"])
                except Exception as e:
                    _log.error(f"Callback error: {e}")
            else:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(50, _poll)

        threading.Thread(target=_run, daemon=True).start()
        # Start polling from main thread
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, _poll)


class BackendConnector:
    """
    Connector that bridges the UI and the backend.
    Supports both demo mode and real brain mode.
    """

    def __init__(self, use_demo=True, brain=None):
        self.use_demo = use_demo
        self._brain = brain

    def set_brain(self, brain):
        self._brain = brain
        self.use_demo = False

    def send_command(self, message, callback):
        if self._brain and not self.use_demo:
            BrainBackend.send(message, callback, self._brain)
        else:
            DemoBackend.send(message, callback)

    def on_processing(self):
        pass

    def on_idle(self):
        pass

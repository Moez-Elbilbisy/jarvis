"""
Main window for the JARVIS AI Assistant interface.

Ties together all components: orb with rotating rings, HUD panels,
chat interface, waveform visualizer, and decorative overlays.
Manages the state machine that drives visual transitions.
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QStackedLayout, QFrame, QLabel, QGraphicsOpacityEffect,
    QPushButton
)
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QLinearGradient, QRadialGradient
)

from jarvis.ui.orb_rings import OrbRingsWidget
from jarvis.ui.waveform import WaveformWidget
from jarvis.ui.hud_panels import (
    HUDPanel, TopBarWidget, HUDOverlay, InfoPanel, GaugePanel,
    StatusLabel, CircularGauge
)
from jarvis.ui.chat_panel import ChatPanel
from jarvis.ui.backend_connector import BackendConnector
from jarvis.voice_loop import VoiceLoop, WakeListener
from jarvis.screen_watcher import ScreenWatcher
from jarvis import config
from jarvis.tools.pc_control import CONFIRMATION_GATE


class JarvisWindow(QMainWindow):
    """Main JARVIS interface window."""

    def __init__(self, brain=None, voice=None, db=None):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        self.setStyleSheet("QMainWindow { background: black; }")

        # State machine
        self._state = "idle"  # idle, listening, processing, responding

        # Backend connector
        self._brain = brain
        self._voice = voice
        self._db = db

        # Build the central widget
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet("background: black;")

        # Main layout is a grid so we can overlay things
        main_layout = QGridLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- HUD Overlay (covers entire window, lowest layer) ---
        self._hud_overlay = HUDOverlay(central)
        main_layout.addWidget(self._hud_overlay, 0, 0, 10, 6)

        # --- Top bar ---
        self._top_bar = TopBarWidget(central)
        main_layout.addWidget(self._top_bar, 0, 0, 1, 6)

        # --- Left column: Info panel + Gauge panel ---
        left_container = QWidget(central)
        left_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(15, 15, 5, 15)
        left_layout.setSpacing(10)

        self._info_panel = InfoPanel()
        left_layout.addWidget(self._info_panel)

        self._gauge_panel = GaugePanel()
        left_layout.addWidget(self._gauge_panel)

        left_layout.addStretch()

        # Extra left-side status labels
        self._left_extras = [
            StatusLabel("POWER: 100%", QColor(0, 255, 100)),
            StatusLabel("TEMP: 36.5C", QColor(0, 200, 255)),
            StatusLabel("UPTIME: 99.9%", QColor(0, 220, 180)),
        ]
        for sl in self._left_extras:
            sl.setStyleSheet("font-size: 8px; margin-left: 12px;")
            left_layout.addWidget(sl)

        # Memory bank status (persistent long-term memory)
        self._memory_label = StatusLabel("MEMORY: -- ITEMS", QColor(170, 140, 255))
        self._memory_label.setStyleSheet("font-size: 8px; margin-left: 12px;")
        left_layout.addWidget(self._memory_label)

        # EYES toggle (proactive screen awareness)
        self._eyes_btn = QPushButton("EYES: OFF")
        self._eyes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eyes_btn.setFixedHeight(26)
        self._eyes_btn.setStyleSheet("""
            QPushButton {
                color: rgb(120, 140, 160); background-color: rgba(0, 30, 50, 120);
                border: 1px solid rgba(0, 160, 220, 80); border-radius: 6px;
                font-family: Consolas; font-size: 9px; font-weight: bold;
                letter-spacing: 1px; margin-left: 8px;
            }
            QPushButton:checked {
                color: rgb(255, 200, 120); background-color: rgba(80, 45, 0, 160);
                border: 1px solid rgba(255, 170, 60, 200);
            }
        """)
        self._eyes_btn.setCheckable(True)
        self._eyes_btn.toggled.connect(self._on_eyes_toggled)
        left_layout.addWidget(self._eyes_btn)

        # Hands-free wake-word toggle ("Hey Jarvis")
        self._handsfree_btn = QPushButton("HANDS-FREE: OFF")
        self._handsfree_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._handsfree_btn.setFixedHeight(26)
        self._handsfree_btn.setStyleSheet(self._eyes_btn.styleSheet())
        self._handsfree_btn.setCheckable(True)
        self._handsfree_btn.toggled.connect(self._on_handsfree_toggled)
        left_layout.addWidget(self._handsfree_btn)

        # START WITH WINDOWS toggle (HKCU Run key -- no admin needed)
        self._autostart_btn = QPushButton("START W/ WINDOWS: OFF")
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setStyleSheet(self._eyes_btn.styleSheet())
        self._autostart_btn.setCheckable(True)
        self._autostart_btn.toggled.connect(self._on_autostart_toggled)
        left_layout.addWidget(self._autostart_btn)

        left_layout.addStretch()

        main_layout.addWidget(left_container, 1, 0, 9, 1)

        # --- Center: Orb/Rings ---
        self._orb_widget = OrbRingsWidget(central)
        main_layout.addWidget(self._orb_widget, 1, 1, 6, 4)

        # --- Right column: Chat panel ---
        right_container = QWidget(central)
        right_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(5, 15, 15, 15)
        right_layout.setSpacing(10)

        # Chat header
        chat_header = QLabel("COMMUNICATION LOG")
        chat_header.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        chat_header.setStyleSheet("color: rgb(0, 200, 255); margin-left: 4px; margin-bottom: 4px;")
        right_layout.addWidget(chat_header)

        # Chat panel with border
        chat_frame = QFrame()
        chat_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 20, 40, 60);
                border: 1px solid rgba(0, 180, 255, 80);
                border-radius: 8px;
            }
        """)
        chat_frame_layout = QVBoxLayout(chat_frame)
        chat_frame_layout.setContentsMargins(2, 2, 2, 2)

        self._chat_panel = ChatPanel()
        self._chat_panel.command_submitted.connect(self._on_command)
        self._chat_panel.mic_clicked.connect(self._on_mic)
        chat_frame_layout.addWidget(self._chat_panel)

        right_layout.addWidget(chat_frame, 1)

        # --- Confirmation gate bar (hidden until Jarvis needs approval) ---
        # Shutdown/restart/sleep/hibernate register a pending action in
        # CONFIRMATION_GATE; only a click here lets it proceed.
        self._confirm_bar = QFrame(central)
        self._confirm_bar.setVisible(False)
        self._confirm_bar.setStyleSheet("""
            QFrame {
                background-color: rgba(80, 40, 0, 140);
                border: 1px solid rgba(255, 170, 60, 200);
                border-radius: 8px;
            }
            QLabel { color: rgb(255, 200, 120); border: none; background: transparent; }
        """)
        confirm_layout = QHBoxLayout(self._confirm_bar)
        confirm_layout.setContentsMargins(12, 6, 12, 6)
        self._confirm_label = QLabel("JARVIS REQUESTS CONFIRMATION")
        self._confirm_label.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        confirm_layout.addWidget(self._confirm_label, 1)
        self._confirm_yes = QPushButton("CONFIRM")
        self._confirm_yes.setFixedWidth(90)
        self._confirm_yes.setStyleSheet("""
            QPushButton {
                color: rgb(0, 255, 130); background-color: rgba(0, 60, 40, 180);
                border: 1px solid rgba(0, 255, 130, 180); border-radius: 5px;
                padding: 5px 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(0, 100, 60, 220); }
        """)
        self._confirm_yes.clicked.connect(self._on_confirm_yes)
        confirm_layout.addWidget(self._confirm_yes)
        self._confirm_no = QPushButton("ABORT")
        self._confirm_no.setFixedWidth(80)
        self._confirm_no.setStyleSheet("""
            QPushButton {
                color: rgb(255, 90, 90); background-color: rgba(70, 10, 10, 180);
                border: 1px solid rgba(255, 90, 90, 180); border-radius: 5px;
                padding: 5px 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(120, 20, 20, 220); }
        """)
        self._confirm_no.clicked.connect(self._on_confirm_abort)
        confirm_layout.addWidget(self._confirm_no)

        # Right-side extra status
        self._right_extras = [
            StatusLabel("PROTOCOLS: ACTIVE", QColor(0, 220, 255)),
            StatusLabel("DATABASE: SYNCED", QColor(0, 255, 150)),
            StatusLabel("SENSORS: NOMINAL", QColor(0, 200, 200)),
        ]
        for sl in self._right_extras:
            sl.setStyleSheet("font-size: 8px; margin-left: 4px;")
            right_layout.addWidget(sl)

        main_layout.addWidget(right_container, 1, 5, 9, 1)

        # --- Bottom: Waveform + status text ---
        bottom_container = QWidget(central)
        bottom_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(15, 5, 15, 15)
        bottom_layout.setSpacing(4)

        # Status text
        self._status_label = QLabel("SYSTEM IDLE")
        self._status_label.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: rgb(0, 200, 255);")
        bottom_layout.addWidget(self._confirm_bar)
        bottom_layout.addWidget(self._status_label)

        # Waveform
        self._waveform = WaveformWidget()
        bottom_layout.addWidget(self._waveform)

        main_layout.addWidget(bottom_container, 9, 1, 1, 4)

        # --- Column stretch ---
        main_layout.setColumnStretch(0, 1)
        main_layout.setColumnStretch(1, 2)
        main_layout.setColumnStretch(2, 0)
        main_layout.setColumnStretch(3, 0)
        main_layout.setColumnStretch(4, 0)
        main_layout.setColumnStretch(5, 1)
        main_layout.setRowStretch(0, 0)
        main_layout.setRowStretch(1, 1)

        # --- Welcome message ---
        # Backend connector
        self._backend = BackendConnector(use_demo=(self._brain is None), brain=self._brain)

        QTimer.singleShot(500, self._show_welcome)

        # Focus the input
        QTimer.singleShot(600, self._chat_panel.focus_input)

        # Voice loop (mic -> speech -> text); degrades gracefully if
        # no microphone or sounddevice/SpeechRecognition are missing.
        self._voice_loop = VoiceLoop()

        # Confirmation gate poller: the gate blocks the tool thread until
        # the user clicks CONFIRM/ABORT here. Polling from the main thread
        # avoids any cross-thread Qt widget access.
        self._gate_timer = QTimer(self)
        self._gate_timer.timeout.connect(self._poll_gate)
        self._gate_timer.start(250)

        # Memory panel refresh
        self._mem_timer = QTimer(self)
        self._mem_timer.timeout.connect(self._refresh_memory_label)
        self._mem_timer.start(10000)
        QTimer.singleShot(800, self._refresh_memory_label)

        # Screen awareness (created here; started only by the EYES toggle)
        class _UiRelay(QObject):
            comment = pyqtSignal(str)
            wake = pyqtSignal()
            wake_command = pyqtSignal(str)
            wake_state = pyqtSignal(str)

        self._relay = _UiRelay()
        self._relay.comment.connect(self._on_proactive_comment)
        self._relay.wake.connect(self._on_wake_detected)
        self._relay.wake_command.connect(self._on_wake_command)
        self._relay.wake_state.connect(self._on_wake_state)
        self._watcher = ScreenWatcher(
            brain=self._brain,
            on_comment=lambda text: self._relay.comment.emit(str(text)),
        )

        # Hands-free wake-word listener (started by HANDS-FREE toggle)
        self._wake_listener = WakeListener(
            on_command=lambda t: self._relay.wake_command.emit(str(t)),
            on_wake=lambda: self._relay.wake.emit(),
            on_state=lambda s: self._relay.wake_state.emit(str(s)),
            # Real-time check: while TTS is actually playing, the wake
            # listener ignores everything (covers long replies exactly,
            # instead of a guessed duration that expires too early).
            while_speaking=lambda: bool(
                self._voice and self._voice._speaker.is_playing
            ),
        )

        # Restore hands-free preference once audio devices have settled
        try:
            if self._db and self._db.get_preference("hands_free", False):
                QTimer.singleShot(2500, lambda: self._handsfree_btn.setChecked(True))
        except Exception:
            pass

        # Reflect the actual registry state on the START W/ WINDOWS toggle
        try:
            from jarvis import autostart
            if autostart.is_installed():
                self._autostart_btn.blockSignals(True)
                self._autostart_btn.setChecked(True)
                self._autostart_btn.setText("START W/ WINDOWS: ON")
                self._autostart_btn.blockSignals(False)
        except Exception:
            pass

    def _show_welcome(self):
        self._chat_panel.add_jarvis_message(
            "J.A.R.V.I.S online. All systems operational. How may I assist you today?"
        )

    def set_state(self, state):
        self._state = state
        self._orb_widget.set_state(state)
        self._waveform.set_state(state)
        self._top_bar.set_state(state)

        status_texts = {
            "idle": "SYSTEM IDLE",
            "listening": "LISTENING...",
            "processing": "PROCESSING REQUEST...",
            "responding": "RESPONDING...",
        }
        self._status_label.setText(status_texts.get(state, "SYSTEM IDLE"))

    def _on_command(self, text):
        # Manners: pause proactive eyes while the user is talking
        self._watcher.pause_for_request(180)
        self._chat_panel.add_user_message(text)
        self.set_state("processing")
        self._backend.send_command(text, self._handle_response)

    def _on_mic(self):
        """Mic button: start/stop one voice utterance."""
        if self._voice_loop.busy:
            self._voice_loop.cancel()
            return
        container = {"text": None, "done": False}

        def _cb(text):
            container["text"] = text
            container["done"] = True

        ok = self._voice_loop.start_listening(_cb)
        if ok:
            self._chat_panel.set_mic_listening(True)
            self.set_state("listening")
            self._poll_voice(container)
        else:
            self._append_system(
                "Voice input unavailable (no microphone, or sounddevice/"
                "SpeechRecognition not installed). Text input still works."
            )

    def _poll_voice(self, container):
        """Main-thread poll for the voice capture result (thread-safe)."""
        if not container["done"]:
            QTimer.singleShot(100, lambda: self._poll_voice(container))
            return
        self._chat_panel.set_mic_listening(False)
        text = container["text"]
        if text:
            self._chat_panel.add_user_message(text)
            self.set_state("processing")
            self._backend.send_command(text, self._handle_response)
        else:
            # Cancelled, nothing heard, or transcription failed
            if self._state == "listening":
                self.set_state("idle")

    def _on_confirm_yes(self):
        """User approved the pending irreversible action."""
        CONFIRMATION_GATE.confirm()
        self._confirm_bar.setVisible(False)
        self._append_system("Confirmed. Executing requested action...")

    def _on_confirm_abort(self):
        """User cancelled the pending irreversible action."""
        CONFIRMATION_GATE.abort()
        self._confirm_bar.setVisible(False)
        self._append_system("Aborted. Action cancelled.")

    def _on_eyes_toggled(self, checked):
        """Start/stop proactive screen awareness."""
        if checked:
            started = self._watcher.start()
            self._eyes_btn.setText("EYES: ON")
            self._append_system(
                "Screen awareness enabled. Jarvis will occasionally comment "
                "on what you're doing (max 8 comments/hour)."
                if started else "Screen watcher already running."
            )
        else:
            self._watcher.stop()
            self._eyes_btn.setText("EYES: OFF")
            self._append_system("Screen awareness disabled.")

    def _on_proactive_comment(self, text):
        """Deliver an unprompted Jarvis observation (main thread, via relay)."""
        self._chat_panel.add_proactive_message(text)
        self._wake_listener.mute(1.0)  # pre-roll; playback-synced mute covers the rest
        if self._voice:
            import threading
            threading.Thread(
                target=lambda: self._voice.speak(text), daemon=True
            ).start()

    def _on_autostart_toggled(self, checked):
        """Install/remove the HKCU Run-key entry for launching at login."""
        from jarvis import autostart
        if checked:
            ok = autostart.install()
            self._autostart_btn.setText("START W/ WINDOWS: ON" if ok else "START W/ WINDOWS: OFF")
            self._append_system(
                "Jarvis will now start automatically when Windows boots."
                if ok else "Could not set autostart (registry write failed)."
            )
        else:
            ok = autostart.uninstall()
            self._autostart_btn.setText("START W/ WINDOWS: OFF")
            self._append_system(
                "Autostart disabled. Jarvis will not launch at boot."
                if ok else "Could not remove autostart entry."
            )

    def _on_handsfree_toggled(self, checked):
        """Start/stop the always-on wake-word listener."""
        if checked:
            started = self._wake_listener.start()
            if not started:
                # Revert the toggle; no mic available
                self._handsfree_btn.blockSignals(True)
                self._handsfree_btn.setChecked(False)
                self._handsfree_btn.blockSignals(False)
                self._handsfree_btn.setText("HANDS-FREE: OFF")
                self._append_system("Hands-free unavailable: no microphone found.")
                return
            self._handsfree_btn.setText("HANDS-FREE: ON")
            if getattr(config, "ALWAYS_LISTEN", False):
                self._append_system(
                    "Hands-free enabled -- ALWAYS LISTENING. Just talk: "
                    "questions and requests are answered without a wake "
                    f'word. (Say "{config.WAKE_WORD}" to be extra sure, or '
                    "flip JARVIS_ALWAYS_LISTEN=0 to require it.)"
                )
            else:
                self._append_system(
                    f'Hands-free enabled. Say "{config.WAKE_WORD}" followed by '
                    "your request (e.g. \"Hey Jarvis, open Chrome\")."
                )
            if self._db:
                self._db.set_preference("hands_free", True)
        else:
            self._wake_listener.stop()
            self._handsfree_btn.setText("HANDS-FREE: OFF")
            self._append_system("Hands-free disabled.")
            if self._db:
                self._db.set_preference("hands_free", False)

    def _on_wake_detected(self):
        """Wake phrase heard (main thread via relay)."""
        if self._voice and self._voice.is_speaking:
            return  # Jarvis's own TTS -- ignore echo/self-trigger
        self._watcher.pause_for_request(60)
        self.set_state("listening")
        self._status_label.setText("LISTENING (wake word heard)...")

    def _on_wake_command(self, text):
        """Voice command captured after the wake word."""
        self._on_command(text)  # shared path: pause eyes, log, send to brain

    def _on_wake_state(self, state):
        """Wake listener state transitions (passive/attention/timeout)."""
        if state == "attention":
            self.set_state("listening")
        elif state == "timeout":
            self._append_system("Voice: no command heard after the wake word.")
            if self._state == "listening":
                self.set_state("idle")

    def _poll_gate(self):
        """Show/hide the confirmation bar based on the gate's pending state."""
        pending = CONFIRMATION_GATE.pending_action()
        if pending and not self._confirm_bar.isVisible():
            action = str(pending.get("action", "ACTION")).upper()
            detail = str(pending.get("detail", ""))
            self._confirm_label.setText(
                f"JARVIS REQUESTS: {action} — {detail} Proceed?"
            )
            self._confirm_bar.setVisible(True)
        elif not pending and self._confirm_bar.isVisible():
            self._confirm_bar.setVisible(False)

    def _refresh_memory_label(self):
        """Update the MEMORY status line from the brain's memory store."""
        count = None
        if self._brain is not None and getattr(self._brain, "memory", None):
            try:
                count = self._brain.memory.count()
            except Exception:
                count = None
        self._memory_label.setText(
            f"MEMORY: {count if count is not None else '--'} ITEMS"
        )

    def _handle_response(self, response):
        """Handle AI response on the main thread."""
        self.set_state("responding")
        self._chat_panel.add_jarvis_message(str(response))
        self._watcher.pause_for_request(120)
        # Mute wake detection while Jarvis speaks so its own TTS voice
        # cannot re-trigger hands-free mode (self-listening loop).
        text = str(response)
        # Playback-synced self-mute is automatic via while_speaking; this
        # short pre-roll just covers queue latency before playback begins.
        self._wake_listener.mute(1.0)
        if self._voice:
            import threading
            threading.Thread(
                target=lambda: self._voice.speak(text), daemon=True
            ).start()
        QTimer.singleShot(2000, lambda: self.set_state("idle"))

    def keyPressEvent(self, event):
        # Spacebar cycles states for demo
        if event.key() == Qt.Key.Key_Space and not self._chat_panel._command_bar._input.hasFocus():
            states = ["idle", "listening", "processing", "responding"]
            idx = states.index(self._state) if self._state in states else 0
            next_state = states[(idx + 1) % len(states)]
            self.set_state(next_state)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        # Dark gradient background
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        grad = QRadialGradient(w / 2, h / 2, max(w, h) / 1.5)
        grad.setColorAt(0, QColor(5, 15, 30))
        grad.setColorAt(0.5, QColor(2, 8, 18))
        grad.setColorAt(1, QColor(0, 0, 0))
        painter.fillRect(0, 0, w, h, QBrush(grad))
        painter.end()



    def show_alert(self, alert):
        """Show a notification alert as a system line in the chat."""
        text = str(getattr(alert, "title", alert))
        self._append_system(text)

    def update_status(self, composio_ok=False, tools_str=""):
        """Update status display after brain init."""
        if hasattr(self, '_top_bar'):
            self._top_bar.set_state("idle")
        if tools_str:
            self._append_system(f"Tools loaded: {tools_str}")

    def _append_system(self, message):
        """Append a system message to the chat panel."""
        if hasattr(self, '_chat_panel') and hasattr(self._chat_panel, 'add_message'):
            self._chat_panel.add_message(message, is_user=False)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    palette = app.palette()
    palette.setColor(palette.Window, QColor(0, 0, 0))
    palette.setColor(palette.WindowText, QColor(0, 220, 255))
    app.setPalette(palette)

    window = JarvisWindow(brain=None, voice=None, db=None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

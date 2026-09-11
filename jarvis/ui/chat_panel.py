"""
Chat and command interface for the JARVIS interface.

Provides a scrolling chat log showing conversation history and a text input
bar at the bottom for typing commands to JARVIS. User messages and JARVIS
responses are styled distinctly.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QSize
from PyQt6.QtGui import (
    QFont, QColor, QTextCursor, QTextCharFormat, QTextBlockFormat, QBrush,
    QPalette, QIcon
)


class ChatBubble(QLabel):
    """A single chat message bubble."""

    def __init__(self, text, is_user=False, parent=None, style=None):
        super().__init__(parent)
        self._is_user = is_user
        self.setText(text)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        if style == "proactive":
            font = QFont("Consolas", 10)
            font.setBold(True)
            self.setFont(font)
            self.setStyleSheet("""
                QLabel {
                    color: rgb(255, 200, 120);
                    background-color: rgba(80, 50, 0, 60);
                    border: 1px solid rgba(255, 170, 60, 140);
                    border-radius: 8px;
                    padding: 8px 14px;
                    margin: 4px 10px 4px 40px;
                }
            """)
            return

        if is_user:
            font = QFont("Consolas", 10)
            self.setFont(font)
            self.setStyleSheet("""
                QLabel {
                    color: rgb(180, 220, 255);
                    background-color: rgba(0, 60, 100, 40);
                    border: 1px solid rgba(0, 150, 220, 80);
                    border-radius: 8px;
                    padding: 8px 14px;
                    margin: 4px 40px 4px 10px;
                }
            """)
        else:
            font = QFont("Consolas", 10)
            font.setBold(True)
            self.setFont(font)
            self.setStyleSheet("""
                QLabel {
                    color: rgb(0, 230, 255);
                    background-color: rgba(0, 40, 60, 60);
                    border: 1px solid rgba(0, 200, 255, 120);
                    border-radius: 8px;
                    padding: 8px 14px;
                    margin: 4px 10px 4px 40px;
                }
            """)


class ChatLogWidget(QWidget):
    """Scrollable chat log showing conversation history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(0, 30, 50, 80);
                width: 6px;
                border: none;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 180, 255, 120);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self._container = QWidget()
        self._container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(2)
        self._container_layout.addStretch()

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

    def add_message(self, text, is_user=False, style=None):
        bubble = ChatBubble(text, is_user, style=style)
        # Insert before the stretch
        count = self._container_layout.count()
        self._container_layout.insertWidget(count - 1, bubble)

        # Auto-scroll to bottom
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


class CommandBar(QWidget):
    """Text input bar for typing commands to JARVIS."""

    command_submitted = pyqtSignal(str)
    mic_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(10)

        # Prompt indicator
        prompt = QLabel(">")
        prompt.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        prompt.setStyleSheet("color: rgb(0, 220, 255);")
        prompt.setFixedWidth(20)
        layout.addWidget(prompt)

        # Text input
        self._input = QLineEdit()
        self._input.setFont(QFont("Consolas", 11))
        self._input.setStyleSheet("""
            QLineEdit {
                color: rgb(200, 240, 255);
                background-color: rgba(0, 30, 50, 120);
                border: 1px solid rgba(0, 180, 255, 100);
                border-radius: 6px;
                padding: 6px 12px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(0, 220, 255, 200);
                background-color: rgba(0, 40, 70, 150);
            }
        """)
        self._input.setPlaceholderText("Type a command to JARVIS...")
        self._input.returnPressed.connect(self._on_submit)
        layout.addWidget(self._input)

        # Mic button (voice input)
        self._mic_btn = QPushButton("MIC")
        self._mic_btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        self._mic_btn.setFixedWidth(60)
        self._mic_btn.setToolTip("Click, then speak. Press again to stop early.")
        self._mic_btn.setStyleSheet("""
            QPushButton {
                color: rgb(0, 255, 160);
                background-color: rgba(0, 50, 80, 150);
                border: 1px solid rgba(0, 255, 160, 120);
                border-radius: 6px;
                padding: 6px 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 80, 120, 180);
                border: 1px solid rgba(0, 255, 160, 220);
            }
            QPushButton:pressed {
                background-color: rgba(0, 120, 90, 200);
            }
        """)
        self._mic_btn.clicked.connect(self.mic_clicked.emit)
        layout.addWidget(self._mic_btn)

        # Send button
        self._send_btn = QPushButton("SEND")
        self._send_btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        self._send_btn.setFixedWidth(70)
        self._send_btn.setStyleSheet("""
            QPushButton {
                color: rgb(0, 230, 255);
                background-color: rgba(0, 50, 80, 150);
                border: 1px solid rgba(0, 200, 255, 150);
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 80, 120, 180);
                border: 1px solid rgba(0, 230, 255, 220);
            }
            QPushButton:pressed {
                background-color: rgba(0, 100, 150, 200);
            }
        """)
        self._send_btn.clicked.connect(self._on_submit)
        layout.addWidget(self._send_btn)

    def _on_submit(self):
        text = self._input.text().strip()
        if text:
            self.command_submitted.emit(text)
            self._input.clear()

    def focus_input(self):
        self._input.setFocus()


class ChatPanel(QWidget):
    """Combined chat log and command bar panel."""

    command_submitted = pyqtSignal(str)
    mic_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._chat_log = ChatLogWidget()
        layout.addWidget(self._chat_log, 1)

        self._command_bar = CommandBar()
        self._command_bar.command_submitted.connect(self._relay_command)
        self._command_bar.mic_clicked.connect(self.mic_clicked.emit)
        layout.addWidget(self._command_bar)

    def _relay_command(self, text):
        self.command_submitted.emit(text)

    def set_mic_listening(self, listening: bool):
        """Visually flag the mic button while capturing."""
        btn = self._command_bar._mic_btn
        btn.setText("STOP" if listening else "MIC")
        btn.setStyleSheet(btn.styleSheet())  # force repaint

    def add_user_message(self, text):
        self._chat_log.add_message(text, is_user=True)

    def add_jarvis_message(self, text):
        self._chat_log.add_message(text, is_user=False)

    def add_proactive_message(self, text):
        """Amber-tinted bubble for Jarvis's unprompted observations."""
        self._chat_log.add_message(f"[JARVIS] {text}", is_user=False, style="proactive")

    def add_message(self, text, is_user=False):
        self._chat_log.add_message(text, is_user=is_user)

    def focus_input(self):
        self._command_bar.focus_input()

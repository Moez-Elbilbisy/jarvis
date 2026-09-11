"""
Audio waveform visualizer widget for the JARVIS interface.

Renders an animated bar-style waveform that responds to the assistant's
state: idle (flat), listening (gentle), processing (active), responding (energetic).
"""

import math
import random
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QLinearGradient, QBrush


class WaveformWidget(QWidget):
    """Animated audio waveform visualizer."""

    BAR_COUNT = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumHeight(60)
        self.setMaximumHeight(80)

        self.state = "idle"
        self._bars = [0.0] * self.BAR_COUNT
        self._targets = [0.0] * self.BAR_COUNT
        self._phase = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_state(self, state: str):
        self.state = state

    def _tick(self):
        self._phase += 0.05

        if self.state == "idle":
            # Flat with very slight movement
            for i in range(self.BAR_COUNT):
                self._targets[i] = 0.05 + 0.02 * math.sin(self._phase + i * 0.3)
        elif self.state == "listening":
            # Gentle wave
            for i in range(self.BAR_COUNT):
                center = self.BAR_COUNT / 2
                dist = abs(i - center) / center
                self._targets[i] = (1.0 - dist) * (0.3 + 0.2 * random.random())
        elif self.state == "processing":
            # Active, irregular
            for i in range(self.BAR_COUNT):
                self._targets[i] = 0.2 + 0.5 * random.random() * (
                    0.5 + 0.5 * math.sin(self._phase * 2 + i * 0.5)
                )
        elif self.state == "responding":
            # Energetic, center-heavy
            for i in range(self.BAR_COUNT):
                center = self.BAR_COUNT / 2
                dist = abs(i - center) / center
                self._targets[i] = (1.0 - dist * 0.6) * (0.4 + 0.5 * random.random())
        else:
            for i in range(self.BAR_COUNT):
                self._targets[i] = 0.05

        # Smooth bars toward targets
        for i in range(self.BAR_COUNT):
            self._bars[i] += (self._targets[i] - self._bars[i]) * 0.3

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        mid = h / 2

        bar_w = w / self.BAR_COUNT
        gap = bar_w * 0.3
        draw_w = bar_w - gap

        # Color based on state
        if self.state == "idle":
            color = QColor(0, 150, 200, 120)
        elif self.state == "listening":
            color = QColor(50, 200, 255, 200)
        elif self.state == "processing":
            t = (math.sin(self._phase * 3) + 1) / 2
            color = QColor(int(50 + t * 100), int(200 - t * 80), 255, 220)
        else:
            color = QColor(100, 220, 255, 220)

        for i in range(self.BAR_COUNT):
            bar_h = self._bars[i] * (h * 0.45)
            if bar_h < 1:
                bar_h = 1
            x = i * bar_w + gap / 2
            y_top = mid - bar_h
            y_bot = mid + bar_h

            # Gradient for each bar
            grad = QLinearGradient(0, y_top, 0, y_bot)
            grad.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 60))
            grad.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), color.alpha()))
            grad.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 60))

            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(x, y_top, draw_w, bar_h * 2), 1.5, 1.5)

        painter.end()

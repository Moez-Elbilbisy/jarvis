"""
HUD status panels for the JARVIS interface.

Renders corner panels with live time/date, system status indicators,
CPU/memory-style gauges, and decorative tech-style brackets and grid overlays.
"""

import math
import random
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient, QBrush,
    QPainterPath
)


class HUDPanel(QWidget):
    """A semi-transparent HUD panel with a tech-style border."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Panel background
        bg_color = QColor(0, 30, 50, 80)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # Border
        border_color = QColor(0, 180, 255, 100)
        pen = QPen(border_color)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # Corner brackets
        bracket_len = 12
        bracket_color = QColor(0, 220, 255, 200)
        pen = QPen(bracket_color)
        pen.setWidthF(2.0)
        painter.setPen(pen)

        # Top-left bracket
        painter.drawLine(2, 2, 2 + bracket_len, 2)
        painter.drawLine(2, 2, 2, 2 + bracket_len)
        # Top-right bracket
        painter.drawLine(w - 2, 2, w - 2 - bracket_len, 2)
        painter.drawLine(w - 2, 2, w - 2, 2 + bracket_len)
        # Bottom-left bracket
        painter.drawLine(2, h - 2, 2 + bracket_len, h - 2)
        painter.drawLine(2, h - 2, 2, h - 2 - bracket_len)
        # Bottom-right bracket
        painter.drawLine(w - 2, h - 2, w - 2 - bracket_len, h - 2)
        painter.drawLine(w - 2, h - 2, w - 2, h - 2 - bracket_len)

        painter.end()


class CircularGauge(QWidget):
    """A circular gauge widget showing a percentage value."""

    def __init__(self, label="CPU", color=QColor(0, 220, 255), parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label = label
        self._color = color
        self._value = 0.0
        self._target = 0.0
        self.setMinimumSize(80, 80)
        self.setMaximumSize(100, 100)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_target(self, value):
        self._target = max(0.0, min(1.0, value))

    def _tick(self):
        self._value += (self._target - self._value) * 0.1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        r = min(w, h) / 2 - 8

        # Background circle
        bg_pen = QPen(QColor(0, 100, 150, 80))
        bg_pen.setWidthF(3.0)
        painter.setPen(bg_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 0, 360 * 16)

        # Value arc
        start_angle = 90 * 16
        span_angle = int(-self._value * 360 * 16)
        val_pen = QPen(self._color)
        val_pen.setWidthF(3.0)
        painter.setPen(val_pen)
        painter.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start_angle, span_angle)

        # Percentage text
        font = QFont("Consolas", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self._color.red(), self._color.green(), self._color.blue(), 230))
        text = f"{int(self._value * 100)}%"
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)

        # Label below
        font2 = QFont("Consolas", 6)
        painter.setFont(font2)
        painter.setPen(QColor(0, 180, 220, 180))
        painter.drawText(QRectF(0, h - 14, w, 14), Qt.AlignmentFlag.AlignCenter, self._label)

        painter.end()


class StatusLabel(QLabel):
    """A styled status label with glowing text."""

    def __init__(self, text="", color=QColor(0, 220, 255), parent=None):
        super().__init__(text, parent)
        self._color = color
        font = QFont("Consolas", 9)
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet(f"color: rgb({color.red()},{color.green()},{color.blue()});")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_color(self, color):
        self._color = color
        self.setStyleSheet(f"color: rgb({color.red()},{color.green()},{color.blue()});")


class TopBarWidget(QWidget):
    """Top bar with JARVIS nameplate and status indicators."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(50)

        self._blink = False
        self._state = "idle"

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    def set_state(self, state):
        self._state = state

    def _tick(self):
        self._blink = not self._blink
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Background line at bottom
        pen = QPen(QColor(0, 180, 255, 80))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(0, h - 1, w, h - 1)

        # JARVIS nameplate
        font = QFont("Consolas", 16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(0, 230, 255, 240))
        painter.drawText(QRectF(20, 0, 200, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "J.A.R.V.I.S")

        # Subtitle
        font2 = QFont("Consolas", 7)
        painter.setFont(font2)
        painter.setPen(QColor(0, 180, 220, 160))
        painter.drawText(QRectF(20, h - 18, 200, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        "JUST A RATHER VERY INTELLIGENT SYSTEM")

        # Status indicators on the right
        indicators = [
            ("ONLINE", QColor(0, 255, 100), True),
            (self._state.upper(), QColor(0, 220, 255), self._blink),
        ]

        x = w - 20
        for label, color, blink in reversed(indicators):
            font3 = QFont("Consolas", 8)
            font3.setBold(True)
            painter.setFont(font3)
            text_w = 100
            alpha = 240 if not blink else (240 if self._blink else 100)
            painter.setPen(QColor(color.red(), color.green(), color.blue(), alpha))
            painter.drawText(QRectF(x - text_w, 0, text_w, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

            # Status dot
            dot_r = 4
            dot_x = x - text_w - 8
            dot_y = h / 2
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), alpha)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(dot_x, dot_y), dot_r, dot_r)

            x = dot_x - 15

        painter.end()


class HUDOverlay(QWidget):
    """Decorative grid and tech-line overlays drawn across the full window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._phase = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._phase += 0.02
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Faint grid
        grid_color = QColor(0, 100, 150, 15)
        pen = QPen(grid_color)
        pen.setWidthF(0.5)
        painter.setPen(pen)

        spacing = 40
        for x in range(0, w, spacing):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, spacing):
            painter.drawLine(0, y, w, y)

        # Corner tech brackets (large, at screen edges)
        bracket_color = QColor(0, 200, 255, 60)
        pen = QPen(bracket_color)
        pen.setWidthF(2.0)
        painter.setPen(pen)

        bl = 30  # bracket length
        margin = 10
        # Top-left
        painter.drawLine(margin, margin, margin + bl, margin)
        painter.drawLine(margin, margin, margin, margin + bl)
        # Top-right
        painter.drawLine(w - margin, margin, w - margin - bl, margin)
        painter.drawLine(w - margin, margin, w - margin, margin + bl)
        # Bottom-left
        painter.drawLine(margin, h - margin, margin + bl, h - margin)
        painter.drawLine(margin, h - margin, margin, h - margin - bl)
        # Bottom-right
        painter.drawLine(w - margin, h - margin, w - margin - bl, h - margin)
        painter.drawLine(w - margin, h - margin, w - margin, h - margin - bl)

        # Animated scan line
        scan_y = (math.sin(self._phase) * 0.5 + 0.5) * h
        scan_grad = QLinearGradient(0, scan_y - 2, 0, scan_y + 2)
        scan_grad.setColorAt(0, QColor(0, 200, 255, 0))
        scan_grad.setColorAt(0.5, QColor(0, 200, 255, 30))
        scan_grad.setColorAt(1, QColor(0, 200, 255, 0))
        painter.setBrush(QBrush(scan_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(0, scan_y - 2, w, 4))

        painter.end()


class InfoPanel(HUDPanel):
    """HUD panel showing time, date, and system information."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setFixedHeight(160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self._time_label = StatusLabel("", QColor(0, 230, 255))
        self._time_label.setFont(QFont("Consolas", 18))
        self._time_label.setStyleSheet("color: rgb(0,230,255); font-size: 18px; font-weight: bold;")
        layout.addWidget(self._time_label)

        self._date_label = StatusLabel("", QColor(0, 180, 220))
        self._date_label.setStyleSheet("color: rgb(0,180,220); font-size: 9px;")
        layout.addWidget(self._date_label)

        layout.addSpacing(4)

        self._status_lines = [
            StatusLabel("SYSTEM: OPERATIONAL", QColor(0, 255, 100)),
            StatusLabel("NETWORK: CONNECTED", QColor(0, 220, 150)),
            StatusLabel("SECURITY: ACTIVE", QColor(0, 200, 255)),
        ]
        for sl in self._status_lines:
            sl.setStyleSheet(f"font-size: 8px;")
            layout.addWidget(sl)

        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_info)
        self._timer.start(1000)
        self._update_info()

    def _update_info(self):
        now = datetime.now()
        self._time_label.setText(now.strftime("%H:%M:%S"))
        self._date_label.setText(now.strftime("%a, %b %d, %Y"))


class GaugePanel(HUDPanel):
    """HUD panel containing circular gauges."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setFixedHeight(120)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._cpu_gauge = CircularGauge("CPU", QColor(0, 220, 255))
        self._mem_gauge = CircularGauge("MEM", QColor(0, 255, 150))
        self._net_gauge = CircularGauge("NET", QColor(255, 200, 50))

        layout.addWidget(self._cpu_gauge)
        layout.addWidget(self._mem_gauge)
        layout.addWidget(self._net_gauge)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_gauges)
        self._timer.start(2000)
        self._update_gauges()

    def _update_gauges(self):
        self._cpu_gauge.set_target(0.3 + 0.4 * random.random())
        self._mem_gauge.set_target(0.4 + 0.3 * random.random())
        self._net_gauge.set_target(0.2 + 0.5 * random.random())

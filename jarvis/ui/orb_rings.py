"""
Central orb and holographic data rings widget for the JARVIS interface.

Renders the iconic glowing core orb with three concentric rotating rings,
each spinning at a different speed with tick marks and segmented arcs.
The orb pulses softly when idle and brightens/shifts color when processing.
"""

import math
import random
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient, QLinearGradient,
    QPainterPath, QFont
)


class OrbRingsWidget(QWidget):
    """Widget that renders the central orb and rotating data rings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # State: "idle", "listening", "processing", "responding"
        self.state = "idle"

        # Animation phases
        self._pulse_phase = 0.0
        self._ring1_angle = 0.0   # inner ring (slow)
        self._ring2_angle = 0.0   # middle ring (medium)
        self._ring3_angle = 0.0   # outer ring (fast)
        self._filament_phase = 0.0
        self._energy_ripples = []  # list of (radius, opacity) tuples

        # Timer driving the animation
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60fps

    def set_state(self, state: str):
        self.state = state
        if state == "processing":
            self._spawn_ripple()

    def _spawn_ripple(self):
        self._energy_ripples.append((0, 1.0))

    def _tick(self):
        dt = 0.016

        # Pulse speed depends on state
        if self.state == "idle":
            pulse_speed = 1.5
        elif self.state == "listening":
            pulse_speed = 3.0
        elif self.state == "processing":
            pulse_speed = 4.0
        else:
            pulse_speed = 2.5

        self._pulse_phase += dt * pulse_speed
        self._filament_phase += dt * 2.0

        # Ring rotation speeds
        self._ring1_angle = (self._ring1_angle + 0.4) % 360
        self._ring2_angle = (self._ring2_angle - 0.7) % 360
        self._ring3_angle = (self._ring3_angle + 1.1) % 360

        # Energy ripples expand and fade
        new_ripples = []
        for r, op in self._energy_ripples:
            r += 2.5
            op -= 0.015
            if op > 0:
                new_ripples.append((r, op))
        self._energy_ripples = new_ripples

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2

        # Scale based on widget size
        base = min(w, h)
        orb_r = base * 0.18

        # --- Determine colors based on state ---
        if self.state == "idle":
            core_color = QColor(0, 200, 255)
            glow_color = QColor(0, 150, 220, 80)
            ring_color = QColor(0, 180, 255, 200)
        elif self.state == "listening":
            core_color = QColor(50, 220, 255)
            glow_color = QColor(50, 200, 255, 120)
            ring_color = QColor(50, 210, 255, 220)
        elif self.state == "processing":
            # Cycle between cyan and violet
            t = (math.sin(self._pulse_phase * 2) + 1) / 2
            r = int(0 + t * 140)
            g = int(200 - t * 100)
            b = int(255 - t * 55)
            core_color = QColor(r, g, b)
            glow_color = QColor(r, g, b, 100)
            ring_color = QColor(r, g, b, 220)
        else:  # responding
            core_color = QColor(100, 230, 255)
            glow_color = QColor(100, 200, 255, 100)
            ring_color = QColor(100, 210, 255, 210)

        # --- Pulse factor ---
        if self.state == "processing":
            pulse = 1.0 + 0.06 * math.sin(self._pulse_phase * 3)
        else:
            pulse = 1.0 + 0.03 * math.sin(self._pulse_phase)

        orb_r_dyn = orb_r * pulse

        # --- Draw outer glow ---
        glow_r = orb_r_dyn * 2.5
        glow_grad = QRadialGradient(cx, cy, glow_r)
        glow_grad.setColorAt(0, glow_color)
        glow_grad.setColorAt(0.5, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 30))
        glow_grad.setColorAt(1, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # --- Draw energy ripples ---
        for r, op in self._energy_ripples:
            ripple_r = orb_r + r * 3
            pen = QPen(QColor(core_color.red(), core_color.green(), core_color.blue(), int(150 * op)))
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), ripple_r, ripple_r)

        # --- Draw rotating rings ---
        self._draw_ring(painter, cx, cy, orb_r * 1.8, self._ring1_angle, ring_color,
                        segments=4, tick_count=24, tick_len=0.04, line_width=2.0)
        self._draw_ring(painter, cx, cy, orb_r * 2.5, self._ring2_angle, ring_color,
                        segments=6, tick_count=32, tick_len=0.03, line_width=1.5)
        self._draw_ring(painter, cx, cy, orb_r * 3.2, self._ring3_angle, ring_color,
                        segments=3, tick_count=48, tick_len=0.025, line_width=1.0)

        # --- Draw orb body ---
        # Outer orb ring
        pen = QPen(QColor(core_color.red(), core_color.green(), core_color.blue(), 180))
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), orb_r_dyn, orb_r_dyn)

        # Inner orb gradient
        orb_grad = QRadialGradient(cx, cy, orb_r_dyn)
        orb_grad.setColorAt(0, QColor(core_color.red(), core_color.green(), core_color.blue(), 200))
        orb_grad.setColorAt(0.4, QColor(core_color.red(), core_color.green(), core_color.blue(), 60))
        orb_grad.setColorAt(0.85, QColor(core_color.red(), core_color.green(), core_color.blue(), 20))
        orb_grad.setColorAt(1, QColor(core_color.red(), core_color.green(), core_color.blue(), 0))
        painter.setBrush(QBrush(orb_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), orb_r_dyn, orb_r_dyn)

        # --- Draw filament lines on orb edge ---
        num_filaments = 8
        for i in range(num_filaments):
            angle = self._filament_phase + (i * 2 * math.pi / num_filaments)
            x1 = cx + math.cos(angle) * orb_r_dyn * 0.85
            y1 = cy + math.sin(angle) * orb_r_dyn * 0.85
            x2 = cx + math.cos(angle) * orb_r_dyn * 1.0
            y2 = cy + math.sin(angle) * orb_r_dyn * 1.0
            alpha = int(120 + 80 * math.sin(self._filament_phase * 2 + i))
            alpha = max(0, min(255, alpha))
            pen = QPen(QColor(core_color.red(), core_color.green(), core_color.blue(), alpha))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # --- Draw inner core dot ---
        core_dot_r = orb_r_dyn * 0.15
        core_grad = QRadialGradient(cx, cy, core_dot_r)
        core_grad.setColorAt(0, QColor(255, 255, 255, 220))
        core_grad.setColorAt(0.5, QColor(core_color.red(), core_color.green(), core_color.blue(), 180))
        core_grad.setColorAt(1, QColor(core_color.red(), core_color.green(), core_color.blue(), 0))
        painter.setBrush(QBrush(core_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), core_dot_r, core_dot_r)

        painter.end()

    def _draw_ring(self, painter, cx, cy, radius, angle, color,
                  segments=4, tick_count=24, tick_len=0.04, line_width=1.5):
        """Draw a rotating ring with segmented arcs and tick marks."""
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(angle)

        pen = QPen(color)
        pen.setWidthF(line_width)

        # Draw segmented arcs
        seg_arc = 360.0 / segments
        gap = seg_arc * 0.15
        for i in range(segments):
            start = i * seg_arc + gap / 2
            span = seg_arc - gap
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(-radius, -radius, radius * 2, radius * 2)
            painter.drawArc(rect, int(start * 16), int(span * 16))

        # Draw tick marks
        tick_pen = QPen(QColor(color.red(), color.green(), color.blue(), 150))
        tick_pen.setWidthF(1.0)
        painter.setPen(tick_pen)
        for i in range(tick_count):
            a = 2 * math.pi * i / tick_count
            r1 = radius - radius * tick_len
            r2 = radius + radius * tick_len
            x1 = math.cos(a) * r1
            y1 = math.sin(a) * r1
            x2 = math.cos(a) * r2
            y2 = math.sin(a) * r2
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Draw small dots at segment boundaries
        dot_pen = QPen(QColor(color.red(), color.green(), color.blue(), 200))
        painter.setPen(dot_pen)
        for i in range(segments):
            a = 2 * math.pi * i / segments
            dx = math.cos(a) * radius
            dy = math.sin(a) * radius
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 220)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(dx, dy), 3.0, 3.0)

        painter.restore()

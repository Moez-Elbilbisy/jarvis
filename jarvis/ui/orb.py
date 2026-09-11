"""
Jarvis Arc Reactor Orb
Concentric ring animation inspired by the Stark Industries arc reactor.
Each state changes the ring colors and animation behavior.
"""

import math

from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import QPainter, QColor, QRadialGradient, QBrush, QPen
from PyQt6.QtWidgets import QWidget, QSizePolicy


class OrbState:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    WORKING = "working"
    ERROR = "error"


# Arc reactor color palettes per state
STATE_COLORS = {
    OrbState.IDLE: {
        "core": QColor(0, 160, 200, 180),
        "ring1": QColor(0, 120, 160, 100),
        "ring2": QColor(0, 80, 120, 60),
        "ring3": QColor(0, 60, 100, 30),
        "glow": QColor(0, 180, 220, 30),
    },
    OrbState.LISTENING: {
        "core": QColor(0, 212, 255, 240),
        "ring1": QColor(0, 212, 255, 160),
        "ring2": QColor(0, 180, 220, 100),
        "ring3": QColor(0, 140, 180, 50),
        "glow": QColor(0, 212, 255, 60),
    },
    OrbState.THINKING: {
        "core": QColor(0, 212, 255, 220),
        "ring1": QColor(0, 180, 240, 140),
        "ring2": QColor(0, 140, 200, 90),
        "ring3": QColor(0, 100, 160, 50),
        "glow": QColor(0, 212, 255, 50),
    },
    OrbState.SPEAKING: {
        "core": QColor(0, 212, 255, 230),
        "ring1": QColor(0, 200, 240, 150),
        "ring2": QColor(0, 160, 210, 100),
        "ring3": QColor(0, 120, 170, 50),
        "glow": QColor(0, 212, 255, 55),
    },
    OrbState.WORKING: {
        "core": QColor(0, 200, 230, 200),
        "ring1": QColor(0, 160, 200, 120),
        "ring2": QColor(0, 120, 170, 80),
        "ring3": QColor(0, 80, 130, 40),
        "glow": QColor(0, 180, 220, 45),
    },
    OrbState.ERROR: {
        "core": QColor(255, 80, 60, 200),
        "ring1": QColor(255, 60, 40, 120),
        "ring2": QColor(200, 40, 30, 70),
        "ring3": QColor(160, 30, 20, 35),
        "glow": QColor(255, 60, 40, 40),
    },
}


class JarvisOrb(QWidget):
    """
    Animated arc reactor orb.
    Concentric rings pulse and rotate based on Jarvis state.
    """

    def __init__(self, parent=None, size: int = 200):
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.setFixedSize(size, size)

        self._phase = 0.0
        self._rotation = 0.0
        self._state = OrbState.IDLE
        self._target_state = OrbState.IDLE
        self._transition_progress = 1.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60fps

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, new_state: str):
        if new_state == self._state:
            return
        self._target_state = new_state
        self._transition_progress = 0.0

    def _tick(self):
        # Phase advance — speed varies by state
        speed = {
            OrbState.IDLE: 0.012,
            OrbState.LISTENING: 0.035,
            OrbState.THINKING: 0.06,
            OrbState.SPEAKING: 0.045,
            OrbState.WORKING: 0.03,
            OrbState.ERROR: 0.025,
        }.get(self._state, 0.015)

        self._phase += speed
        self._rotation += speed * 0.8

        # Smooth state transition
        if self._transition_progress < 1.0:
            self._transition_progress = min(1.0, self._transition_progress + 0.04)
            if self._transition_progress >= 1.0:
                self._state = self._target_state

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        base_r = min(self.width(), self.height()) / 3.2

        colors = STATE_COLORS.get(self._state, STATE_COLORS[OrbState.IDLE])

        # Background glow — soft halo
        glow_r = base_r * 1.6
        glow_grad = QRadialGradient(cx, cy, glow_r)
        glow_color = colors["glow"]
        glow_grad.setColorAt(0, glow_color)
        glow_fade = QColor(glow_color)
        glow_fade.setAlpha(0)
        glow_grad.setColorAt(1, glow_fade)
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # Ring 3 — outermost, faint
        r3 = base_r * (1.15 + math.sin(self._phase * 0.7) * 0.04)
        self._draw_ring(painter, cx, cy, r3, colors["ring3"], 2.5)

        # Ring 2 — middle
        r2 = base_r * (0.85 + math.sin(self._phase + 0.5) * 0.03)
        self._draw_ring(painter, cx, cy, r2, colors["ring2"], 3.0)

        # Ring 1 — inner, bright
        r1 = base_r * (0.55 + math.sin(self._phase * 1.2) * 0.02)
        self._draw_ring(painter, cx, cy, r1, colors["ring1"], 3.5)

        # Core — bright center point
        core_r = base_r * 0.18
        core_grad = QRadialGradient(cx, cy, core_r)
        core_grad.setColorAt(0, colors["core"])
        core_fade = QColor(colors["core"])
        core_fade.setAlpha(int(colors["core"].alpha() * 0.3))
        core_grad.setColorAt(0.7, core_fade)
        core_fade2 = QColor(colors["core"])
        core_fade2.setAlpha(0)
        core_grad.setColorAt(1, core_fade2)
        painter.setBrush(QBrush(core_grad))
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # State-specific overlays
        if self._state == OrbState.THINKING:
            # Rotating segments on ring 1
            self._draw_segments(painter, cx, cy, r1, colors["ring1"])

        elif self._state == OrbState.LISTENING:
            # Pulse ring — expanding outward
            pulse_r = base_r * (1.0 + (self._phase * 2 % 1.0) * 0.5)
            pulse_alpha = int(80 * (1.0 - (self._phase * 2 % 1.0)))
            pulse_color = QColor(0, 212, 255, pulse_alpha)
            self._draw_ring(painter, cx, cy, pulse_r, pulse_color, 1.5)

        elif self._state == OrbState.SPEAKING:
            # Sound wave rings
            for i in range(2):
                wave_phase = self._phase * 3 + i * 3.14
                wave_r = base_r * (0.6 + 0.6 * (math.sin(wave_phase) * 0.5 + 0.5))
                wave_alpha = int(60 * (1 - i * 0.4))
                wave_color = QColor(0, 212, 255, wave_alpha)
                self._draw_ring(painter, cx, cy, wave_r, wave_color, 1.5)

        elif self._state == OrbState.ERROR:
            # Flicker
            flicker = 0.6 + abs(math.sin(self._phase * 5)) * 0.4
            painter.setOpacity(flicker)

        painter.setOpacity(1.0)

    def _draw_ring(self, painter, cx, cy, radius, color, width):
        """Draw a single concentric ring."""
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

    def _draw_segments(self, painter, cx, cy, radius, color):
        """Draw rotating arc segments on a ring (thinking state)."""
        pen = QPen(color, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        import math as m
        for i in range(4):
            angle = self._rotation * 2 + i * (m.pi / 2)
            start_deg = angle * 180 / m.pi
            span_deg = 25
            painter.drawArc(
                int(cx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
                int(start_deg * 16), int(span_deg * 16)
            )

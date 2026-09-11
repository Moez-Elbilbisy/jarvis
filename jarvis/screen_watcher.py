"""
Jarvis Screen Watcher
Proactive screen awareness: periodically captures the screen, detects
*meaningful* visual changes with a cheap perceptual hash (8x8 average
hash -- no extra dependencies), and asks Gemini for a one-sentence
observation. Heavy throttling keeps it useful instead of annoying:

  - only comments when the screen truly changed (Hamming distance gate)
  - SCREEN_MIN_COMMENT_GAP between comments
  - SCREEN_COMMENT_COOLDOWN quiet watch after each comment
  - SCREEN_MAX_CHANGES_PER_HOUR hard quota
  - auto-pauses while the user is talking to Jarvis

Flow: capture -> aHash -> changed? -> budget ok? -> brain.analyze_screen(png)
      -> "NO_COMMENT" filtered out -> on_comment callback (UI queue).
"""

import logging
import threading
import time
from typing import Callable, Optional

from jarvis import config
from jarvis.tools.pc_control import capture_screen_png

logger = logging.getLogger("jarvis.screen_watcher")

# 8x8 aHash bits that must flip for the change to count as "meaningful"
MEANINGFUL_CHANGE_BITS = 12


def ahash64(png_bytes: bytes) -> Optional[int]:
    """8x8 average-hash of a PNG. Returns a 64-bit int, or None on failure."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("L").resize(
            (8, 8), Image.LANCZOS
        )
        pixels = list(img.getdata())
        avg = sum(pixels) / 64.0
        bits = 0
        for i, p in enumerate(pixels):
            if p > avg:
                bits |= 1 << i
        return bits
    except Exception:
        return None


def hamming64(a: int, b: int) -> int:
    """Hamming distance between two 64-bit hashes."""
    return bin(a ^ b).count("1")


class ScreenWatcher:
    """
    Background screen-awareness loop.

    on_comment(text) is invoked from the watcher thread whenever Jarvis
    produces a proactive observation; the UI is expected to marshal it
    onto the main thread itself.
    """

    def __init__(self, brain, on_comment: Callable[[str], None]):
        self._brain = brain
        self._on_comment = on_comment

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._last_hash: Optional[int] = None
        self._last_comment_ts = 0.0
        self._cooldown_until = 0.0
        self._hour_start = time.time()
        self._changes_this_hour = 0
        self._paused_until = 0.0

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the watcher loop (idempotent)."""
        if self.is_running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="jarvis-eyes"
        )
        self._thread.start()
        return True

    def stop(self):
        """Stop the watcher loop (idempotent)."""
        self._stop.set()

    def toggle(self) -> bool:
        if self.is_running:
            self.stop()
        else:
            self.start()
        return self.is_running

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def pause_for_request(self, seconds: float = 180.0):
        """
        Suppress proactive comments for a while (used while the user is
        conversing). Self-expires so no resume call is strictly needed.
        """
        self._paused_until = max(self._paused_until, time.time() + seconds)

    def resume(self):
        self._paused_until = 0.0

    # ── Loop ─────────────────────────────────────────────────────

    def _run(self):
        interval = max(10, int(config.SCREEN_CAPTURE_INTERVAL))
        logger.info(
            "Screen watcher started (capture every %ss, max %s comments/hour)",
            interval, config.SCREEN_MAX_CHANGES_PER_HOUR,
        )
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:
                logger.debug("Watcher cycle error: %s", e)
            self._stop.wait(interval)
        logger.info("Screen watcher stopped")

    def _cycle(self):
        png = capture_screen_png(max_dim=config.SCREEN_MAX_IMAGE_DIM)
        if png is None:
            return
        h = ahash64(png)
        if h is None:
            return

        prev = self._last_hash
        self._last_hash = h
        if prev is None:
            return  # first frame: baseline only

        if hamming64(prev, h) < MEANINGFUL_CHANGE_BITS:
            return  # screen essentially unchanged

        now = time.time()
        if now < self._paused_until:
            return
        if now < self._cooldown_until:
            return
        if now - self._last_comment_ts < config.SCREEN_MIN_COMMENT_GAP:
            return
        if now - self._hour_start >= 3600:
            self._hour_start = now
            self._changes_this_hour = 0
        if self._changes_this_hour >= config.SCREEN_MAX_CHANGES_PER_HOUR:
            return

        # Budget a comment: ask the brain whether this is worth saying
        self._changes_this_hour += 1
        self._last_comment_ts = now
        self._cooldown_until = now + config.SCREEN_COMMENT_COOLDOWN

        logger.info("Meaningful screen change -> requesting observation")
        try:
            comment = self._brain.analyze_screen(png)
        except Exception as e:
            logger.warning("analyze_screen failed: %s", e)
            return
        if comment and "NO_COMMENT" not in comment:
            try:
                self._on_comment(comment)
            except Exception as e:
                logger.warning("on_comment callback failed: %s", e)

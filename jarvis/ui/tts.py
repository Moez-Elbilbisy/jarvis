"""
Jarvis TTS -- human-sounding speech output.

Primary: Microsoft Edge neural voices (edge-tts) -- genuinely human, free,
no API key, prebuilt wheels. Needs internet (Microsoft's free TTS service).
Fallback: pyttsx3 (offline SAPI voice) through a dedicated worker thread,
which also fixes the classic "speaks only once" bug caused by calling
runAndWait() on a shared engine from many short-lived threads.

Usage:
    speaker = get_speaker()
    speaker.speak("All systems operational, sir.")
"""

import asyncio
import logging
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from jarvis import config

logger = logging.getLogger("jarvis.tts")

# Sensible neural defaults: en-US male/female; "hi" = high quality tier
_EDGE_VOICE = "en-US-GuyNeural"          # deep male, very natural
_EDGE_VOICE_FEMALE = "en-US-AriaNeural"
_EDGE_VOICE_AR = "ar-EG-ShakirNeural"     # Egyptian Arabic male neural
_ARABIC_RE = None  # compiled lazily


def _has_arabic(text: str) -> bool:
    """True if the text contains Arabic-script characters."""
    global _ARABIC_RE
    if _ARABIC_RE is None:
        import re
        _ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
    return bool(_ARABIC_RE.search(text or ""))
_PLAYBACK_SAMPLE_RATE = 24000            # edge-tts mp3 native rate


class Speaker:
    """
    Human-quality TTS with a serialized playback queue.

    - speak(text, interrupt=False) enqueues text; nothing blocks the caller.
    - interrupt=True drops everything queued and plays immediately.
    - speak() from any thread is safe; playback happens on one worker.
    - `_playing` flag lets the UI mute wake-word detection while talking.
    """

    def __init__(self):
        self._q: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._playing = threading.Event()
        self._muted = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        self._edge_ok: Optional[bool] = None  # unknown until first try
        self._fallback = None                 # pyttsx3 engine (lazy)
        self._tmpdir = tempfile.mkdtemp(prefix="jarvis_tts_")

    # ── Public API ───────────────────────────────────────────────

    def speak(self, text: str, interrupt: bool = False) -> None:
        """Queue text for speech. Safe from any thread."""
        text = (text or "").strip()
        if not text or self._muted.is_set():
            return
        if interrupt:
            self._drain()
        with self._lock:
            started = self._started
        if not started:
            self.start()
        self._q.put({"text": text})

    def mute_for(self, seconds: float) -> None:
        """Drop incoming speech for a while (used during mic capture)."""
        if seconds <= 0:
            return
        self._muted.set()
        threading.Timer(seconds, self._muted.clear).start()

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()

    def stop(self) -> None:
        """Discard everything queued and stop the current utterance."""
        self._drain()

    def shutdown(self) -> None:
        self._drain()
        self._q.put(None)

    # ── Internals ────────────────────────────────────────────────

    def _drain(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._worker, daemon=True,
                         name="jarvis-tts").start()

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            text = item["text"]
            self._playing.set()
            try:
                if not self._speak_edge(text):
                    self._speak_pyttsx3(text)
            except Exception as e:
                logger.warning("TTS failed: %s", e)
            finally:
                self._playing.clear()

    # ── Edge neural voice ────────────────────────────────────────

    def _speak_edge(self, text: str) -> bool:
        """Synthesize with edge-tts and play via soundfile. True on success."""
        try:
            import edge_tts
            import soundfile as sf
            import numpy as np
            import sounddevice as sd
        except ImportError:
            self._edge_ok = False
            return False

        mp3_path = Path(self._tmpdir) / f"utt_{int(time.time() * 1000)}.mp3"
        try:
            async def _synth():
                # Voice selection: Egyptian Arabic voice for Arabic text
                # (Shakir pronounces Egyptian dialect naturally), otherwise
                # the English voice. Fallback voices tried in order.
                if _has_arabic(text):
                    voices = [_EDGE_VOICE_AR, _EDGE_VOICE]
                else:
                    voices = [_EDGE_VOICE, _EDGE_VOICE_FEMALE]
                for v in voices:
                    try:
                        await edge_tts.Communicate(
                            text, v, rate=config.EDGE_TTS_RATE
                        ).save(str(mp3_path))
                        return
                    except Exception:
                        continue

            asyncio.run(_synth())
            if not mp3_path.exists() or mp3_path.stat().st_size < 200:
                self._edge_ok = False
                return False

            data, sr_ = sf.read(str(mp3_path), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            data = np.clip(data * config.TTS_VOLUME, -1.0, 1.0)
            sd.play(data, sr_)
            sd.wait()
            return True
        except Exception as e:
            logger.debug("edge-tts failed (%s); falling back", e)
            self._edge_ok = False
            return False
        finally:
            try:
                mp3_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ── pyttsx3 fallback (worker-thread engine) ─────────────────

    def _speak_pyttsx3(self, text: str) -> None:
        """
        Fallback: SAPI voice on a DEDICATED engine created and used only on
        this worker thread. The old code created runAndWait() calls from many
        threads on one engine -- after the first utterance the engine's COM
        event loop wedged and all further speech was silently dropped.
        """
        try:
            import pyttsx3
            if self._fallback is None:
                self._fallback = pyttsx3.init()
                self._fallback.setProperty("rate", config.TTS_RATE)
                self._fallback.setProperty("volume", config.TTS_VOLUME)
                voices = self._fallback.getProperty("voices")
                idx = min(config.TTS_VOICE_INDEX, len(voices) - 1)
                if voices and idx >= 0:
                    self._fallback.setProperty("voice", voices[idx].id)
            self._fallback.say(text)
            self._fallback.runAndWait()
        except Exception as e:
            logger.warning("pyttsx3 fallback failed: %s", e)


_speaker: Optional[Speaker] = None
_speaker_lock = threading.Lock()


def get_speaker() -> Speaker:
    """Process-wide speaker singleton."""
    global _speaker
    with _speaker_lock:
        if _speaker is None:
            _speaker = Speaker()
        return _speaker

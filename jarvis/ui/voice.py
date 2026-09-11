"""
Jarvis Voice Module
Handles speech recognition (input) and text-to-speech (output).
"""

import logging
import threading
from typing import Callable

from jarvis.ui.tts import get_speaker

logger = logging.getLogger("jarvis.voice")


class VoiceEngine:
    """
    Manages voice input (speech recognition) and output (text-to-speech).
    Thread-safe -- TTS runs in background threads.
    """

    def __init__(self):
        self._tts_engine = None  # legacy; speech goes through tts.Speaker
        self._is_speaking = False
        self._voice_enabled = True
        self._recognition_available = False
        self._speaker = get_speaker()
        self._check_recognition()

    def _initialize_tts(self):
        """Legacy hook kept for compatibility; Speaker handles speech now."""
        logger.info(" TTS ready (neural voice, pyttsx3 fallback)")

    def _check_recognition(self):
        """
        Check if speech recognition + microphone are available.

        NOTE: mic capture goes through jarvis.voice_loop (sounddevice),
        NOT PyAudio. PyAudio is optional legacy -- do not require it.
        """
        try:
            import speech_recognition  # noqa: F401
        except ImportError:
            self._recognition_available = False
            logger.info("  Speech recognition not installed (pip install SpeechRecognition)")
            return

        try:
            import sounddevice as sd  # noqa: F401
            self._recognition_available = True
            logger.info(" Speech recognition available (sounddevice mic)")
        except ImportError:
            # Legacy fallback: PyAudio also works if someone has it
            try:
                import pyaudio  # noqa: F401
                self._recognition_available = True
                logger.info(" Speech recognition available (PyAudio)")
            except ImportError:
                self._recognition_available = False
                logger.info("  No sounddevice/PyAudio - use the MIC button path (pip install sounddevice)")

    #  Text-to-Speech 

    def speak(self, text: str, block: bool = False):
        """
        Convert text to speech via the shared Speaker (neural voice with
        serialized playback; fixes the speaks-only-once issue).

        Args:
            text: Text to speak
            block: Accepted for compatibility; playback is always queued.
        """
        if not self._voice_enabled:
            return
        self._is_speaking = True
        try:
            self._speaker.speak(text)
        finally:
            # The Speaker owns actual playback; flag clears via polling.
            threading.Timer(0.5, self._clear_speaking).start()

    def _clear_speaking(self):
        self._is_speaking = self._speaker.is_playing

    def stop_speaking(self):
        """Stop current speech output."""
        self._speaker.stop()
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    #  Speech Recognition 

    def listen(self, timeout: int = 5, phrase_limit: int = 10) -> str | None:
        """
        Listen for speech input and return transcribed text.
        Blocks until speech is detected or timeout.

        Args:
            timeout: Max seconds to wait for speech to start
            phrase_limit: Max seconds of speech to capture

        Returns:
            Transcribed text or None if no speech detected
        """
        if not self._recognition_available:
            logger.warning("Speech recognition not available")
            return None

        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()

            with sr.Microphone() as source:
                logger.debug(" Listening...")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_limit,
                )

            # Try Google's free speech recognition
            text = recognizer.recognize_google(audio)
            logger.debug(f" Heard: {text}")
            return text

        except sr.WaitTimeoutError:
            logger.debug(" Listening timed out")
            return None
        except sr.UnknownValueError:
            logger.debug(" Could not understand audio")
            return None
        except sr.RequestError as e:
            logger.warning(f" Speech recognition service error: {e}")
            return None
        except OSError as e:
            if "PyAudio" in str(e) or "portaudio" in str(e).lower():
                self._recognition_available = False
                logger.debug(" PyAudio not available - voice input disabled")
            else:
                logger.warning(f" Listen error: {e}")
            return None
        except Exception as e:
            logger.debug(f" Listen error: {e}")
            return None

    def listen_continuous(self, callback: Callable[[str], None], stop_event: threading.Event | None = None):
        """
        Continuously listen and call callback with transcribed text.
        Runs until stop_event is set.
        """
        if not self._recognition_available:
            logger.warning("Speech recognition not available for continuous listening")
            return

        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()

            with sr.Microphone() as source:
                logger.info(" Continuous listening started...")
                recognizer.adjust_for_ambient_noise(source, duration=1)

                while not (stop_event and stop_event.is_set()):
                    try:
                        audio = recognizer.listen(source, timeout=2, phrase_time_limit=10)
                        text = recognizer.recognize_google(audio)
                        if text:
                            callback(text)
                    except sr.WaitTimeoutError:
                        continue
                    except sr.UnknownValueError:
                        continue
                    except Exception as e:
                        logger.warning(f"Continuous listen error: {e}")
                        break

        except Exception as e:
            logger.error(f"Continuous listening setup failed: {e}")

    #  Settings 

    def set_voice_enabled(self, enabled: bool):
        """Enable or disable voice output."""
        self._voice_enabled = enabled
        if not enabled:
            self.stop_speaking()

    @property
    def is_voice_enabled(self) -> bool:
        return self._voice_enabled

    @property
    def has_recognition(self) -> bool:
        return self._recognition_available

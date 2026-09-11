"""
Jarvis Voice Loop
Real-time microphone capture and transcription WITHOUT PyAudio.

Uses sounddevice (prebuilt wheels -- no C++ compiler needed) to capture mic
audio with voice-activity detection, then transcribes via Google's free
speech recognition. Degrades gracefully: if no microphone or no sounddevice,
the loop simply reports unavailable and the UI hides the mic controls.

Flow: mic -> RMS VAD -> AudioData -> recognize_google -> text -> brain -> TTS
"""

import difflib
import logging
import queue
import re
import threading
import time
from typing import Callable, List, Optional

from jarvis import config

logger = logging.getLogger("jarvis.voice_loop")

SAMPLE_RATE = 16000
FRAME_MS = 30                     # frames per callback
SILENCE_FRAMES_TO_STOP = 45       # ~1.4s of silence ends an utterance
SPEECH_START_FRAMES = 6           # ~180ms above threshold starts an utterance
MAX_UTTERANCE_SECONDS = 20        # safety cap
RMS_SPEECH_THRESHOLD = 0.006      # tweakable; typical laptop mic speech > 0.01


# ── Direct-command detection ─────────────────────────────

def _is_direct_command(text: str) -> bool:
    """True if the utterance starts with a known action verb (Arabic or
    English). Used when Google drops the wake word from Arabic speech:
    'افتح الدسكورد' should command, not be silently discarded."""
    words = _norm_words(text)
    if not words:
        return False
    ar = {w for w in getattr(config, "COMMAND_VERBS_AR", [])}
    en = {v.lower() for v in getattr(config, "COMMAND_VERBS_EN", [])}
    first = words[0]
    # Arabic: the verb is often the first word (sometimes after 'يا')
    if first in ar or (len(words) > 1 and words[0] in {"يا", "ي"} and words[1] in ar):
        return True
    # English: first word is an action verb (skip 'jarvis' if leading)
    probe = words[1] if (words[0] in {"jarvis", "javis", "jervis"} and len(words) > 1) else first
    return probe in en


def _passes_conversation_filter(text: str) -> bool:
    """Heuristic: is this utterance plausibly addressed to Jarvis?

    In always-listen mode everything heard reaches here. Drop the obvious
    not-to-me cases: single words, pure filler/interjections ("yeah",
    "ايوه", "yalla"), and utterances that are only a wake-word fragment
    misheard. Questions and commands naturally pass (multi-word, not filler).
    """
    words = [w for w in re.findall(r"[\w\u0600-\u06FF']+", text.lower())
             if w]
    if len(words) < 2:
        return False
    fillers = {f.lower() for f in getattr(config, "CHATTER_FILLERS", [])}
    # All words being fillers/fragments -> not addressed to us
    meaningful = [w for w in words if w not in fillers]
    if not meaningful:
        return False
    # Pure greeting-only chatter ("hello hello") also stays ignored
    if all(w in {"hello", "hi", "hey", "salam", "سلام", "هلا"}
           for w in meaningful):
        return False
    return True


# ── Wake-word matching ────────────────────────────────────

_NON_WORD_RE = re.compile(r"[^\w ]+", re.UNICODE)  # \w keeps Arabic letters


def _norm_words(text: str) -> List[str]:
    """Lowercase, strip punctuation, split into words.

    Unicode-aware: Arabic words (\u062c\u0627\u0631\u0641\u064a\u0633) survive
    normalization, so wake matching works on ar-EG transcripts too.
    """
    return _NON_WORD_RE.sub(" ", (text or "").lower()).split()


def wake_phrases() -> List[str]:
    """All accepted wake phrases (deduplicated, English + Arabic)."""
    return list(dict.fromkeys(
        [config.WAKE_WORD]
        + list(config.WAKE_WORD_VARIANTS)
        + list(getattr(config, "WAKE_WORD_VARIANTS_AR", []))
    ))


def contains_wake_phrase(text: str) -> bool:
    """Fuzzy check: does the transcript contain any wake phrase anywhere?

    Compares at character level over sliding word windows so ASR mangling
    ("okay jarvis", "a jarvis", "hay jarvis") still matches.
    """
    words = _norm_words(text)
    if not words:
        return False
    n = len(words)
    for phrase in wake_phrases():
        pw = _norm_words(phrase)
        m = len(pw)
        if m == 0 or m > n:
            continue
        phrase_str = " ".join(pw)
        for i in range(n - m + 1):
            window_str = " ".join(words[i:i + m])
            ratio = difflib.SequenceMatcher(None, window_str, phrase_str).ratio()
            if ratio >= config.WAKE_MATCH_THRESHOLD:
                return True
    return False


def strip_wake_word(text: str) -> str:
    """Remove the best-matching wake-phrase window from the transcript."""
    words = _norm_words(text)
    if not words:
        return ""
    best_ratio, best_start, best_len = 0.0, 0, 0
    for phrase in wake_phrases():
        pw = _norm_words(phrase)
        m = len(pw)
        if m == 0 or m > len(words):
            continue
        phrase_str = " ".join(pw)
        for i in range(len(words) - m + 1):
            window_str = " ".join(words[i:i + m])
            ratio = difflib.SequenceMatcher(None, window_str, phrase_str).ratio()
            if ratio > best_ratio:
                best_ratio, best_start, best_len = ratio, i, m
    if best_ratio >= config.WAKE_MATCH_THRESHOLD:
        words = words[best_start + best_len:]
    return " ".join(words).strip()


# ── Local STT: faster-whisper (offline, no rate limits) ──

_whisper_model = None      # lazy singleton
_whisper_failed = False    # import/init failed -> stop trying this process


def _get_whisper():
    """Lazy faster-whisper singleton (base model, int8, CPU).

    Returns None when unavailable (not installed or LOCAL_STT disabled) so
    callers fall back to the remote Google recognizer transparently.
    """
    global _whisper_model, _whisper_failed
    if _whisper_failed:
        return None
    if _whisper_model is not None:
        return _whisper_model
    if not getattr(config, "LOCAL_STT", True):
        _whisper_failed = True
        return None
    try:
        from faster_whisper import WhisperModel
        logger.info("Loading local Whisper (base/int8 on CPU) -- one-time cost")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("Local Whisper ready -- offline transcription active")
        return _whisper_model
    except Exception as e:
        logger.warning("faster-whisper unavailable (%s) -- using Google STT", e)
        _whisper_failed = True
        return None


def transcribe_frames(audio) -> Optional[str]:
    """Transcribe concatenated float32 frames -> text.

    Primary: local faster-whisper (offline, no network drops or latency).
    Fallback: SpeechRecognition's Google endpoint, trying every language in
    config.SPEECH_LANGUAGES (ar-EG Egyptian Arabic + en-US English) and
    returning BOTH readings so the brain can use whichever it understands.
    """
    import numpy as np

    # 1) Local whisper first: auto-detects Arabic/English, no network.
    model = _get_whisper()
    if model is not None:
        try:
            mono = np.clip(audio, -1.0, 1.0).astype(np.float32)
            # NB: faster-whisper takes raw 16kHz float32 (no sample_rate kwarg)
            # -- our capture path already produces exactly that.
            segments, info = model.transcribe(
                mono,
                language=None,          # auto-detect ar/en
                beam_size=1, vad_filter=True,
            )
            texts = [seg.text.strip() for seg in segments if seg.text.strip()]
            if texts:
                text = " ".join(texts)
                lang = getattr(info, "language", "?") or "?"
                logger.info(" Heard (local whisper, %s): %s", lang, text)
                return text
            logger.info(" Local whisper heard nothing -- trying Google STT")
        except Exception as e:
            logger.warning(" Local whisper failed (%s) -- trying Google STT", e)

    # 2) Remote Google recognizer (dual-language) as fallback.
    try:
        import speech_recognition as sr
    except ImportError:
        logger.warning(" SpeechRecognition not installed -- transcription unavailable")
        return None

    recognizer = sr.Recognizer()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    # AudioData(frame_data, sample_rate, sample_width) -- sample_width is
    # BYTES PER SAMPLE. int16 audio requires 2 here; passing 1 (channels)
    # silently corrupts the audio and every transcription fails.
    audio_data = sr.AudioData(pcm, SAMPLE_RATE, 2)

    langs = list(getattr(config, "SPEECH_LANGUAGES", None) or ["ar-EG", "en-US"])
    readings = []  # list of (lang, text)
    for lang in langs:
        text = _recognize_in_language(recognizer, audio_data, lang)
        if text:
            readings.append((lang, text))
    if not readings:
        logger.info(" Could not understand audio (speech captured but not recognized)")
        return None
    if len(readings) == 1:
        lang, text = readings[0]
        logger.info(" Heard (%s): %s", lang, text)
        return text
    # Both languages produced text: return both so the brain gets the
    # fullest picture (Egyptian Arabic + English cross-check).
    combined = " / ".join(f"[{lang}] {t}" for lang, t in readings)
    logger.info(" Heard (dual): %s", combined)
    return combined


def _recognize_in_language(recognizer, audio_data, lang: str) -> Optional[str]:
    """One Google recognition attempt in one language; 1 retry on
    transient network errors (IncompleteRead, timeouts)."""
    import speech_recognition as sr
    for attempt in range(2):
        try:
            return recognizer.recognize_google(audio_data, language=lang)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            if attempt == 0:
                # Transient network hiccup -- retry once
                time.sleep(0.5)
                continue
            logger.warning(" Transcription failed (%s) after retry: %s", lang, e)
            return None
    return None


class VoiceLoop:
    """
    Push-to-talk style voice capture loop.

    start_listening(on_text) records one utterance and calls on_text(text)
    (or on_text(None) on silence/error). Runs entirely in a background
    thread; safe to call from the Qt main thread.
    """

    def __init__(self):
        self._available: Optional[bool] = None  # unknown until first check
        self._busy = False
        self._stop_event = threading.Event()
        self.last_peak_rms = 0.0  # diagnostic: loudest frame of last capture

    # ── Availability ─────────────────────────────────────────────

    def check_available(self) -> bool:
        """Check mic + sounddevice availability. Caches the result."""
        if self._available is not None:
            return self._available
        try:
            import sounddevice as sd
            default_input = sd.default.device[0]
            if default_input is None or default_input < 0:
                self._available = False
                logger.info("  No default microphone found - voice loop disabled")
                return False
            # Probe the device briefly
            dev = sd.query_devices(default_input)
            if dev.get("max_input_channels", 0) < 1:
                self._available = False
                logger.info("  Default input has no capture channels - voice loop disabled")
                return False
            self._available = True
            logger.info(" Voice loop ready (mic: %s)", dev.get("name", "?"))
        except Exception as e:
            self._available = False
            logger.info("  Voice loop unavailable: %s (pip install sounddevice)", e)
        return self._available

    @property
    def busy(self) -> bool:
        return self._busy

    # ── Capture ──────────────────────────────────────────────────

    def start_listening(self, on_text: Callable[[Optional[str]], None]) -> bool:
        """
        Listen for one utterance in a background thread.
        Returns False immediately if unavailable or already busy.
        """
        if not self.check_available():
            on_text(None)
            return False
        if self._busy:
            logger.debug("Voice loop already busy")
            return False

        self._busy = True
        self._stop_event.clear()

        def _run():
            text: Optional[str] = None
            try:
                text = self._capture_utterance()
            except Exception as e:
                logger.warning("Voice capture failed: %s", e)
            finally:
                self._busy = False
                try:
                    on_text(text)
                except Exception as e:
                    logger.error("Voice callback error: %s", e)

        threading.Thread(target=_run, daemon=True, name="jarvis-voice").start()
        return True

    def cancel(self):
        """Abort an in-progress capture."""
        self._stop_event.set()

    def _capture_utterance(
        self,
        vad_threshold: float = RMS_SPEECH_THRESHOLD,
        start_timeout: Optional[float] = None,
    ) -> Optional[str]:
        """
        Record one utterance with VAD and return transcribed text (or None).

        vad_threshold: minimum RMS considered speech (raise for passive wake
            listening so background noise does not trigger transcription).
        start_timeout: if set, give up after this many seconds when no
            speech has started (used for the attention window).
        """
        import numpy as np
        import sounddevice as sd

        audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
        stop = self._stop_event
        in_ch = _get_input_channels()

        def _callback(indata, frames, time_info, status):
            if status:
                pass  # overflow warnings are non-fatal
            # Downmix ALL physical mics to mono: on a multi-mic array,
            # grabbing only channel 0 discards the other elements -> quiet,
            # muffled capture. Mean over channels keeps every mic in play.
            if indata.ndim > 1 and indata.shape[1] > 1:
                mono = indata.mean(axis=1)
            else:
                mono = indata[:, 0]
            audio_q.put(mono.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=in_ch,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * FRAME_MS / 1000),
            callback=_callback,
        ):
            frames: list[np.ndarray] = []
            speech_started = False
            speech_frames = 0
            silence_frames = 0
            peak = 0.0
            max_frames = SAMPLE_RATE * MAX_UTTERANCE_SECONDS // int(SAMPLE_RATE * FRAME_MS / 1000)

            t0 = time.time()
            while not stop.is_set():
                if (
                    start_timeout is not None
                    and not speech_started
                    and time.time() - t0 > start_timeout
                ):
                    break  # nobody started speaking within the window
                try:
                    frame = audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                peak = max(peak, rms)

                if not speech_started:
                    if rms > vad_threshold:
                        speech_frames += 1
                        frames.append(frame)
                        if speech_frames >= SPEECH_START_FRAMES:
                            speech_started = True
                    else:
                        # Keep a pre-roll so word onsets are not clipped
                        # (20 frames ~= 600ms: protects first syllables of
                        # words like "open" or "play" from being cut off).
                        frames.append(frame)
                        if len(frames) > 20:
                            frames.pop(0)
                else:
                    frames.append(frame)
                    if rms < vad_threshold:
                        silence_frames += 1
                        if silence_frames >= SILENCE_FRAMES_TO_STOP:
                            break
                    else:
                        silence_frames = 0
                    if len(frames) >= max_frames:
                        break

        self.last_peak_rms = peak
        if stop.is_set() or len(frames) < SPEECH_START_FRAMES:
            return None

        # Trim trailing silence
        audio = np.concatenate(frames)
        return transcribe_frames(audio)


# ── Hands-free wake-word listener ─────────────────────────


def mic_available() -> bool:
    """One-shot mic/sounddevice availability probe."""
    return VoiceLoop().check_available()


def _get_input_channels() -> int:
    """Input channel count of the default recording device.

    Laptop mic arrays (e.g. 4-channel Realtek) expose several physical
    microphones; hardcoding 1 captures a single element and drops the rest,
    making speech quiet and muffled. Returns 1 on any failure so
    single-mic setups are unaffected.
    """
    try:
        import sounddevice as sd
        dev = sd.query_devices(sd.default.device[0])
        return max(1, int(dev.get("max_input_channels", 1)))
    except Exception:
        return 1


def measure_ambient(seconds: float = 1.5) -> Optional[float]:
    """
    Measure average ambient noise (RMS) from the default mic.
    Used to auto-calibrate wake-word sensitivity per room/mic.
    Returns None if measurement fails.
    """
    try:
        import numpy as np
        import sounddevice as sd
        rec = sd.rec(
            int(SAMPLE_RATE * seconds),
            samplerate=SAMPLE_RATE, channels=_get_input_channels(), dtype="float32",
        )
        sd.wait()
        # Downmix all physical mics to mono so the RMS reflects what the
        # capture path hears (must match _capture_utterance's downmix).
        audio = rec.mean(axis=1) if (rec.ndim > 1 and rec.shape[1] > 1) else rec[:, 0]
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    except Exception as e:
        logger.warning("Ambient measurement failed: %s", e)
        return None


class WakeListener:
    """
    Hands-free mode: one persistent mic stream, passively gated by a higher
    VAD threshold, transcribing only utterances that survive VAD and contain
    the wake phrase (fuzzy-matched, e.g. "hey Jarvis", "a jarvis").

    Two states:
      passive   -- listen for the wake word
      attention -- wake word heard: send any remainder immediately ("hey
                   Jarvis, what's the weather"), otherwise capture the next
                   utterance within ACTIVATION_LISTEN_SECONDS

    Callbacks (all invoked from the listener thread -- the UI must marshal
    them onto the Qt main thread itself):
      on_wake()             -- wake phrase detected
      on_command(text)      -- transcribed command to process
      on_state(state)       -- 'passive' | 'attention' | 'timeout'
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        on_wake: Optional[Callable[[], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
        while_speaking: Optional[Callable[[], bool]] = None,
    ):
        self._on_command = on_command
        self._on_wake = on_wake or (lambda: None)
        self._on_state = on_state or (lambda s: None)
        # Live probe: True while Jarvis's TTS is actually playing, so the
        # listener can ignore its own voice for the exact playback duration.
        self._while_speaking = while_speaking or (lambda: False)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop = VoiceLoop()
        self._mute_until = 0.0  # suppress wake detection (e.g. during TTS)

    def mute(self, seconds: float):
        """
        Ignore wake detection for a while. Used while Jarvis speaks so its
        own voice (which may contain the wake phrase) cannot self-trigger.
        """
        self._mute_until = max(self._mute_until, time.time() + seconds)

    @property
    def is_running(self) -> bool:
        return bool(
            self._thread and self._thread.is_alive() and not self._stop.is_set()
        )

    def start(self) -> bool:
        """Start the listener. Returns False if no mic is available."""
        if not self._loop.check_available():
            return False
        if self.is_running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="jarvis-wake"
        )
        self._thread.start()
        return True

    def stop(self):
        """Stop the listener (idempotent)."""
        self._stop.set()

    def _run(self):
        # ── Auto-calibrate to this room's ambient noise ──
        ambient = measure_ambient(1.5)
        if ambient is not None:
            self._passive_thr = max(
                RMS_SPEECH_THRESHOLD, ambient * config.WAKE_AMBIENT_MULTIPLIER
            )
            # Ceiling: a quiet room would otherwise calibrate a passive gate
            # fine-grained but a loud room sets one no normal voice crosses.
            # Never let the passive gate exceed MAX_PASSIVE_GATE.
            self._passive_thr = min(
                getattr(config, "MAX_PASSIVE_GATE", 0.030), self._passive_thr
            )
            self._active_thr = max(
                RMS_SPEECH_THRESHOLD, ambient * config.ACTIVE_AMBIENT_MULTIPLIER
            )
            logger.info(
                "Wake listener calibrated: ambient RMS %.4f -> gates %.4f (passive) / %.4f (active)",
                ambient, self._passive_thr, self._active_thr,
            )
        else:
            self._passive_thr = RMS_SPEECH_THRESHOLD
            self._active_thr = RMS_SPEECH_THRESHOLD
            logger.warning("Using default VAD thresholds (no ambient measurement)")

        logger.info("Wake listener started (say \"%s\")", config.WAKE_WORD)
        self._on_state("passive")

        while not self._stop.is_set():
            # ── Passive: wait for the wake word ──
            text = None
            try:
                text = self._loop._capture_utterance(vad_threshold=self._passive_thr)
            except Exception as e:
                logger.warning("Wake capture error: %s", e)
            if self._stop.is_set():
                break
            if time.time() < self._mute_until or self._while_speaking():
                continue  # Jarvis is speaking -- ignore everything heard
            if not text:
                # Diagnostics: distinguish "too quiet" from "heard but not
                # recognized" so tuning hints are actually correct.
                peak = getattr(self._loop, "last_peak_rms", 0.0)
                if peak and peak >= self._passive_thr:
                    logger.info(
                        "Wake miss: audio captured (peak %.4f, gate %.4f) but "
                        "transcription returned nothing -- mic may be distorted "
                        "or Google Speech unreachable",
                        peak, self._passive_thr,
                    )
                elif peak and peak > self._passive_thr * 0.55:
                    logger.info(
                        "Wake miss: voice peak %.4f vs gate %.4f "
                        "(speak louder, or lower WAKE_AMBIENT_MULTIPLIER)",
                        peak, self._passive_thr,
                    )
                continue
            # ── Command extraction (wake word, direct command, or conversation) ──
            # Dual-language readings like "[ar] افتح... / [en] ..." carry
            # the command in the Arabic leg; check each part.
            parts: List[tuple] = []
            if text.startswith("["):
                for chunk in text.split(" / "):
                    if chunk.startswith("[") and "]" in chunk:
                        lang, _, body = chunk.partition("]")
                        parts.append((lang.strip("["), body.strip()))
                    else:
                        parts.append(("", chunk))
            else:
                parts.append(("", text))

            direct_ok = bool(getattr(config, "DIRECT_COMMAND_MODE", False))
            always_ok = bool(getattr(config, "ALWAYS_LISTEN", False))
            command, via = None, ""
            matched = False
            for _lang, body in parts:
                if contains_wake_phrase(body):
                    # Bare "hey Jarvis" -> empty remainder -> attention window
                    command = strip_wake_word(body) or None
                    via, matched = "wake word", True
                    break
                if direct_ok and _is_direct_command(body):
                    command, via, matched = body, "direct command", True
                    break
                if always_ok and len(body.split()) >= 2 and \
                        _passes_conversation_filter(body):
                    command, via, matched = body, "always-listen", True
                    break
            if not matched:
                logger.debug("Speech (no wake word): %s", text[:60])
                continue

            # ── Dedup: same command twice in a row executes once ──
            now = time.time()
            window = float(getattr(config, "COMMAND_DEDUP_SECONDS", 5.0))
            if command is not None:
                last_cmd = getattr(self, "_last_cmd", "") or ""
                if command.strip().lower() == last_cmd.strip().lower() \
                        and (now - getattr(self, "_last_cmd_at", 0.0)) < window:
                    logger.info("Duplicate command suppressed: %s", command[:50])
                    self._on_state("passive")
                    continue
                self._last_cmd = command
                self._last_cmd_at = now

            logger.info("Command accepted (%s): %s", via, command[:80] if command else "")
            self._on_wake()

            # ── Attention window (only when nothing to send yet) ──
            self._on_state("attention")
            if command is None or not command.strip():
                command = None
                try:
                    command = self._loop._capture_utterance(
                        vad_threshold=self._active_thr,
                        start_timeout=float(config.ACTIVATION_LISTEN_SECONDS),
                    )
                except Exception as e:
                    logger.warning("Attention capture error: %s", e)
            if self._stop.is_set():
                break
            if command:
                self._on_command(command)
            else:
                self._on_state("timeout")
            self._on_state("passive")

        logger.info("Wake listener stopped")

"""
Jarvis Configuration
Manages API keys, paths, and preferences from .env and local defaults.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
_jarvis_dir = Path(__file__).resolve().parent

# Frozen (PyInstaller) support: code lives in a TEMP extraction dir that is
# DELETED on exit -- never store user data there. Real home = exe folder.
_FROZEN = getattr(sys, "frozen", False)
_APP_HOME = Path(sys.executable).resolve().parent if _FROZEN else _project_root

# .env: packaged copy inside the bundle first, then beside the exe (user-
# editable without rebuilding), then the source-tree locations.
if _FROZEN:
    load_dotenv(Path(getattr(sys, "_MEIPASS", "")) / "jarvis" / ".env")
load_dotenv(_APP_HOME / ".env")
load_dotenv(_project_root / ".env")
load_dotenv(_jarvis_dir / ".env")

#  API Keys 
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
COMPOSIO_API_KEY: str = os.getenv("COMPOSIO_API_KEY", "")

#  AI Model Settings 
# Verified live 2026-09-09: these have working free-tier quota.
# (gemini-3.6-flash = 20 req/day then hard-stops; gemini-3.1-pro = 0 quota,
# dead weight -- never list models without probing them first.)
GEMINI_MODELS: list[str] = [
    "gemini-flash-latest",        # auto-tracks the newest working flash
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]
GROQ_MODELS: list[str] = [
    "qwen/qwen3.8-27b",      # fast small model (tested ~170ms)
    "openai/gpt-oss-20b",    # bigger second try (~330ms)
]
DEFAULT_GEMINI_MODEL: str = "gemini-flash-latest"
DEFAULT_GROQ_MODEL: str = "qwen/qwen3.8-27b"

# ── Free OpenAI-compatible fallback providers ────
# All use the OpenAI chat-completions schema. Add a key to .env and it
# automatically joins the fallback chain (see FALLBACK_PROVIDERS).
OPENAI_COMPAT_PROVIDERS: dict[str, dict] = {
    "groq":     {"base_url": "https://api.groq.com/openai/v1"},
    "nvidia":   {"base_url": "https://integrate.api.nvidia.com/v1"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1"},
    "mistral":  {"base_url": "https://api.mistral.ai/v1"},
    "github":   {"base_url": "https://models.github.ai/inference"},
    "sambanova": {"base_url": "https://api.sambanova.ai/v1"},
}

# Fallback order (only providers with keys in .env are used)
# "dynamic": True -> also discover the provider's full live model catalog
# at fallback time (cached), so newly added models work without code changes.
FALLBACK_PROVIDERS: list[dict] = [
    {"provider": "groq", "models": ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]},
    {"provider": "nvidia", "models": [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
    ], "no_think": True, "dynamic": True,
       # Text fallback can't see -- when the request had a screenshot,
       # these vision-capable NIM models answer instead (tested working).
       "vision_models": ["meta/llama-3.2-11b-vision-instruct"]},
    {"provider": "openrouter", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
    {"provider": "cerebras", "models": ["llama-3.3-70b"]},
    {"provider": "mistral", "models": ["mistral-small-latest"]},
    {"provider": "github", "models": ["openai/gpt-4o-mini"]},
    {"provider": "sambanova", "models": ["Meta-Llama-3.3-70B-Instruct"]},
]

# Model-ID keywords marking NON-chat endpoints (embeddings, rerankers,
# classifiers, vision utilities) -- excluded from dynamic fallback expansion.
NON_CHAT_MODEL_KEYWORDS: tuple[str, ...] = (
    "embed", "rerank", "retriever", "clip", "deplot", "parse",
    "detector", "reward", "guard", "safety", "translate", "muse",
    "ising-calibration", "fuyu", "ocr", "vlm", "vision",
)
# Max dynamically-discovered models tried per provider per fallback call
# (keeps worst-case latency bounded if a provider is having a bad day)
DYNAMIC_MODEL_CAP: int = 12

# Which model on each provider understands the PC/memory tool schema
# (left empty: tool calling stays Gemini-only; fallbacks are text-only
# with the honest "tools unavailable" notice).
NVIDIA_MODELS: list[str] = [
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
]

#  Paths 
# Frozen builds: keep user data in %LOCALAPPDATA%\JarvisAssistant (the
# bundle's temp dir is wiped at exit, and Program Files is not writable).
# Distinct name avoids clashing with other apps called "Jarvis".
if _FROZEN:
    DATA_DIR: Path = Path(os.getenv("LOCALAPPDATA", str(_APP_HOME))) / "JarvisAssistant"
else:
    DATA_DIR: Path = _jarvis_dir / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH: Path = DATA_DIR / "jarvis.db"

#  Composio Google Toolkits 
COMPOSIO_GOOGLE_TOOLKITS: list[str] = [
    "googlecalendar",
    "gmail",
    "googledrive",
    "googlephotos",
]

#  Voice Settings 
WAKE_WORD: str = "hey jarvis"
WAKE_WORD_VARIANTS: list[str] = ["hey jarvis", "ok jarvis", "hi jarvis"]
# Arabic wake variants (Egyptian) -- matched against ar-EG transcripts
WAKE_WORD_VARIANTS_AR: list[str] = ["جارفيس", "يا جارفيس", "جارفس", "جارفيز"]
WAKE_MATCH_THRESHOLD: float = 0.65   # fuzzy match ratio for wake phrases
ACTIVATION_LISTEN_SECONDS: int = 15  # attention window after the wake word
WAKE_AMBIENT_MULTIPLIER: float = 1.8   # passive gate = ambient noise x this
ACTIVE_AMBIENT_MULTIPLIER: float = 1.3 # attention gate = ambient noise x this
# Ceiling for the passive gate: in very quiet rooms the calibrated gate stays
# so low-relative that any hum trips it, while in normal rooms speech sits
# under a too-high gate -> "wake miss" spam. Cap it so the passive gate can
# never exceed a level a normal speaking voice always crosses.
MAX_PASSIVE_GATE: float = 0.030

# Speech recognition languages, tried in order (Google free endpoint).
# Primary ar-EG = Egyptian Arabic; results also cross-checked with en-US
# so English commands keep working and the brain gets both readings.
SPEECH_LANGUAGES: list[str] = ["ar-EG", "en-US"]

# Direct-command mode: Google's ar-EG often DROPS the wake word from
# Arabic transcripts, silently losing the command. When enabled, an
# utterance STARTING with an action verb is treated as a command even
# without a wake word. Set False to require the wake word strictly.
DIRECT_COMMAND_MODE: bool = True
COMMAND_VERBS_AR: list[str] = [
    "افتح", "افتحلي", "فتح", "شغل", "شغلي", "اقفل", "قفل", "امسح",
    "اكتب", "ابعت", "ابعتلي", "دور", "دور", "هات", "جيب", "حط",
    "وقف", "نزل", "نزّل", "حمّل", "غير", "كبّر", "صغّر", "العب", "شيك",
]
COMMAND_VERBS_EN: list[str] = [
    "open", "play", "close", "run", "launch", "start", "type", "send",
    "search", "kill", "stop", "pause", "resume", "skip", "shutdown",
    "restart", "lock", "focus", "switch", "minimize", "maximize",
    "mute", "unmute", "undo", "download", "check",
]
# Same command heard twice within this window executes once (dual-language
# + speaker echo can produce near-identical back-to-back captures).
COMMAND_DEDUP_SECONDS: float = 5.0

#  Always-listen mode 
# Treat any clear utterance as addressed to Jarvis -- no wake word needed.
# A chatter filter drops obviously-not-addressed speech (fillers, single
# interjections, ASR artifacts). Set JARVIS_ALWAYS_LISTEN=0 to require the
# wake word / direct commands only.
ALWAYS_LISTEN: bool = os.getenv("JARVIS_ALWAYS_LISTEN", "1").lower() \
    not in ("0", "false", "no")
# Single words / pure-filler utterances that never count as addressing
# Jarvis (screened out by _passes_conversation_filter in voice_loop).
CHATTER_FILLERS: list[str] = [
    "yeah", "yes", "yep", "no", "okay", "ok", "so", "like", "um", "uh",
    "hmm", "huh", "wow", "lol", "thanks", "wait", "well",
    "aywa", "tayeb", "tayyeb", "yalla", "keda", "khlas",
    "bye", "goodbye", "cya", "later", "goodnight",
    "ايوه", "أيوه", "ايه", "إيه", "تمام", "ماشي", "طب", "خلاص", "يعني",
    "اه", "آه", "لا", "هو", "هي", "سلام", "باي", "يلا", "يلا بينا",
    "بينا", "لازم", "كده", "ازيك", "إزيك",
]

TTS_RATE: int = 175          # words per minute (pyttsx3 fallback)
TTS_VOLUME: float = 0.9
TTS_VOICE_INDEX: int = 0     # system default voice (pyttsx3 fallback)
EDGE_TTS_RATE: str = "+0%"   # neural voice speed (e.g. "+10%", "-5%")
EDGE_TTS_VOICE_EN: str = "en-US-GuyNeural"        # deep male, very natural
EDGE_TTS_VOICE_AR: str = "ar-EG-ShakirNeural"     # Egyptian Arabic male

#  UI Settings 
WINDOW_TITLE: str = "Jarvis -- Personal AI Assistant"
WINDOW_WIDTH: int = 900
WINDOW_HEIGHT: int = 700
ORB_SIZE: int = 200

#  Proactive Alert Intervals (seconds) 
CALENDAR_CHECK_INTERVAL: int = 300   # 5 minutes
EMAIL_CHECK_INTERVAL: int = 600      # 10 minutes
MEETING_REMINDER_MINUTES: int = 15   # alert 15 min before

#  Screen Awareness (proactive vision) 
SCREEN_EYES_ENABLED: bool = False    # user toggles the EYES button in the HUD
SCREEN_CAPTURE_INTERVAL: int = 25    # seconds between screen grabs
SCREEN_MIN_COMMENT_GAP: int = 240    # seconds between proactive comments (>= 4 min)
SCREEN_COMMENT_COOLDOWN: int = 120   # after a comment, wait before watching actively
SCREEN_MAX_CHANGES_PER_HOUR: int = 8 # hard quota of proactive comments per hour
SCREEN_MAX_IMAGE_DIM: int = 1280     # downscale grabs to keep payload small

#  Hotkeys 
ACTIVATE_HOTKEY: str = "Ctrl+Space"
VOICE_TOGGLE_HOTKEY: str = "Ctrl+J"

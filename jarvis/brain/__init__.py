"""
Jarvis Brain -- Core AI Orchestrator
Connects Gemini AI with Composio tools for Google service integration.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import traceback
import webbrowser
from typing import Any, Callable, Dict, List, Optional

from jarvis.config import (
    COMPOSIO_API_KEY,
    COMPOSIO_GOOGLE_TOOLKITS,
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODELS,
)
from jarvis.database import Database
from jarvis.brain.commands import CommandRegistry
from jarvis.brain.prompts import (
    get_jarvis_system_prompt,
    get_morning_briefing_prompt,
    get_email_summary_prompt,
    get_compound_task_prompt,
)
from jarvis.brain.screen_prompt import SCREEN_COMMENT_PROMPT
from jarvis.tools.pc_control import (
    get_pc_tool_declarations,
    execute_pc_tool,
    is_pc_tool,
    capture_screen_png,
)
from jarvis.memory import (
    MemoryStore,
    get_memory_tool_declarations,
    execute_memory_tool,
    is_memory_tool,
)

logger = logging.getLogger("jarvis.brain")

# ── Deterministic fast-path commands ──────────────────────────────────────
# Basic PC commands ("open spotify", "switch to discord", "close chrome")
# must NEVER depend on an LLM choosing to call a tool. Fallback models
# sometimes answer with role-play or printed action blocks; this regex
# layer executes the real action in code, before any AI is consulted.

_FAST_OPEN_VERBS = (
    "open", "start", "launch",
    "افتح", "افتحلي", "ابدأ",
    "afte7", "aftah", "afta7", "ftah",
)
# Play verbs are NOT open verbs: "play X by Y" / "شغل X لـ Y" targets a
# song/video, not an executable. Handled by the AI (Spotify URI etc).
_FAST_PLAY_VERBS = ("play", "شغل", "شغلي", "شغّل", "عند", "شغيل",
                    "shaghghel", "shaghel", "shghel")
# "It didn't open" / "spotify didn't open" -> retry opening the app.
_FAST_RETRY_PATTERNS = (
    "ما فتحش", "مفتحش", "مافتحش", "مش شغال", "ما شالش", "ما اشتغلش",
    "didn't open", "didnt open", "not open", "doesn't work", "didnt work",
)
_FAST_SWITCH_VERBS = ("switch to", "focus", "go to", "روح", "روح على", "ركز على")
_FAST_CLOSE_VERBS = ("close", "quit", "exit", "kill",
                     "اقفل", "قفل", "امسح", "سكر")
_FAST_APP_FIXUPS = {
    "the discord": "discord", "el discord": "discord", "discord": "discord",
    "the spotify": "spotify", "el spotify": "spotify", "spotify": "spotify",
    "steam": "steam", "chrome": "chrome", "notepad": "notepad",
    "calculator": "calculator", "calc": "calculator",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "vs code": "code", "vscode": "code", "code": "code",
    "task manager": "task manager",
}
# Trailing complaint phrases: cut from song queries -- "play X you didnt
# play it" means the SONG is X and the rest is feedback, not the title.
_PLAY_COMPLAINT_MARKERS = (
    "you didnt", "you didn't", "it didnt", "it didn't", "didn't play",
    "didnt play", "not playing", "didnt work", "doesn't work", "didnt open",
    "you never", "why didnt", "why didn't",
    "مش شغالة", "مش شغاله", "ما شغلتش", "ما اشتغلتش", "شغلتهاش",
    "مش شغال", "ما اشتغلش", "ما فتحش",
)
# Bare-pronoun targets: "play it" / "play that again" -> replay the last
# song query instead of searching Spotify for the literal word "it".
_PLAY_PRONOUNS = {"it", "that", "this", "one", "song",
                  "هو", "ده", "دي", "دا", "هاذا", "هذا"}
# Last song query the fast path searched (for "play it" replay). A list
# so it can be reassigned without a `global` statement.
_LAST_FAST_SONG = [None]

# Generic / mood-based song requests: bypassing the fast path on these lets
# the AI pick a REAL track ("شغل اي اغنيه" must not search Spotify for the
# literal word "اي"). Specific titles still fast-path untouched.
def _norm_ar(text: str) -> str:
    """Unify Arabic alef/ta-marbuta/alef-maksura variants + lowercase."""
    out = (text or "").lower().strip()
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"),
                     ("ة", "ه"), ("ى", "ي")):
        out = out.replace(src, dst)
    return out


_GENERIC_OR_MOOD_PHRASES = {
    "اي", "اي حاجه", "اي اغنيه", "حاجه", "اغاني", "مزيكا", "ميوزك",
    "رايقه", "هاديه", "حزينه", "فرفوشه", "حماسيه",
    "any", "anything", "something", "some music", "random", "music",
    "song", "songs", "tunes", "playlist",
    "chill", "relaxing", "upbeat", "sad", "vibe",
    # articles: so "play a song" / "شغل الاغاني" fully normalize to generic
    "a", "an", "the", "يا",
    # taste-related: let the AI choose
    "علي ذوقك", "على ذوقك", "ذوقك", "something good", "any good",
    "اي حاجه حلوه", "حاجه حلوه",
}
_GENERIC_OR_MOOD_PHRASES = {_norm_ar(p) for p in _GENERIC_OR_MOOD_PHRASES}


def _is_generic_song_request(song: str) -> bool:
    """True if the whole song phrase is generic/mood-based ("اي حاجه",
    "something chill") rather than a concrete title/artist."""
    norm = _norm_ar(song)
    if not norm:
        return False
    if norm in _GENERIC_OR_MOOD_PHRASES:
        return True
    words = norm.split()
    return bool(words) and all(w in _GENERIC_OR_MOOD_PHRASES for w in words)


def _fast_path_command(user_input: str):
    """Match a basic PC command and return (tool, args) or None.

    Handles open/switch/close + app-name extraction, in English, Egyptian
    Arabic and Franco. Deliberately conservative: only fires on a leading
    action verb so ordinary questions go to the AI untouched.
    """
    if not user_input:
        return None
    text = user_input.strip().strip(".,!?؟.،")
    low = text.lower()

    # Strip a leading wake phrase so "jarvis open spotify" still fast-paths
    for wake in ("hey jarvis", "hi jarvis", "yo jarvis", "jarvis",
                 "يا جارفيس", "جارفيس", "يا جرجس", "جرجس"):
        if low.startswith(wake):
            text = text[len(wake):].strip(" ,.!?،")
            low = text.lower()
            break

    # 0) Complaint handling: "spotify didn't open" -> retry pc_open_app.
    #    Also handles a bare app name + complaint ("سبوتيفاي ما فتحش").
    for pat in _FAST_RETRY_PATTERNS:
        if pat in low:
            # Look for a known app mention anywhere in the utterance
            for alias, exe in (
                ("سبوتيفاي", "spotify"), ("spotify", "spotify"),
                ("ديسكورد", "discord"), ("دسكورد", "discord"),
                ("discord", "discord"),
                ("كروم", "chrome"), ("chrome", "chrome"),
                ("ستيم", "steam"), ("steam", "steam"),
            ):
                if alias in low:
                    return "pc_open_app", {"app_name": exe}

    # 0.7) Simple scrape commands: "scrape text from example.com"
    fast_scrape = _fast_path_scrape(user_input)
    if fast_scrape is not None:
        return fast_scrape

    # 1) switch/focus -- app may already be running
    for verb in _FAST_SWITCH_VERBS:
        if low.startswith(verb):
            app = text[len(verb):].strip(" .,!؟.،")
            if app:
                return "pc_focus_window", {"window_name": app}
    for verb in ("ركز على",):
        if low.startswith(verb):
            app = text[len(verb):].strip(" .,!؟.،")
            if app:
                return "pc_focus_window", {"window_name": app}

    # 2) close/quit
    for verb in _FAST_CLOSE_VERBS:
        if low.startswith(verb):
            app = text[len(verb):].strip(" .,!؟.،")
            if app:
                app = _FAST_APP_FIXUPS.get(app.lower(), app)
                return "pc_kill_process", {"name": app}

    # 3) open/launch -- the big one
    for verb in _FAST_OPEN_VERBS:
        if low.startswith(verb + " ") or low == verb:
            rest = text[len(verb):].strip(" .,!؟.،")
            if not rest:
                continue
            low_rest = rest.lower()
            # Drop filler words: "open the discord", "open el spotify"
            if low_rest.startswith("the "):
                low_rest = low_rest[4:]
            elif low_rest.startswith("el "):
                low_rest = low_rest[3:]
            words = low_rest.split()
            if not words:
                continue
            # Full phrase ("task manager", "vs code") beats single word
            app = (_FAST_APP_FIXUPS.get(low_rest)
                   or _FAST_APP_FIXUPS.get(words[0])
                   or words[0])
            # Compound command ("open spotify and play x"): the FIRST app
            # word is what we launch; the rest is handled by the AI after.
            if app and all(ch.isalnum() or ch in " -_" for ch in app):
                return "pc_open_app", {"app_name": app}

    # 4) play verbs: only fast-path when a KNOWN APP word follows directly
    #    ("شغل سبوتيفاي" = open spotify). A song name after play ("شغل فيك
    #    حاجه باي دنيا وائل") must go to the AI, which resolves it to a
    #    Spotify search -- launching an app named "فيك" is nonsense.
    for verb in _FAST_PLAY_VERBS:
        if low.startswith(verb + " ") or low == verb:
            rest = text[len(verb):].strip(" .,!؟.،")
            low_rest = rest.lower()
            # Drop Arabic definite article: "الدسكورد" -> "ديسكورد"
            if low_rest.startswith("ال") and len(low_rest) > 4:
                stripped = low_rest[2:]
            else:
                stripped = low_rest
            for alias, exe in (
                ("سبوتيفاي", "spotify"), ("السبوتيفاي", "spotify"),
                ("سبوتفاي", "spotify"),
                ("spotify", "spotify"),
                ("ديسكورد", "discord"), ("الديسكورد", "discord"),
                ("دسكورد", "discord"), ("الدسكورد", "discord"),
                ("discord", "discord"),
                ("ستيم", "steam"), ("الستيم", "steam"),
                ("steam", "steam"),
                ("كروم", "chrome"), ("الكروم", "chrome"),
                ("chrome", "chrome"),
            ):
                if low_rest == alias or stripped == alias:
                    return "pc_open_app", {"app_name": exe}
            # Unknown target after a play verb -> it's a SONG/artist request.
            # Search Spotify directly via the URI handler -- no AI needed.
            # Keep the full phrase INCLUDING "by X" / "لـ X": the artist
            # helps Spotify pick the right track, and stripping it would
            # break titles like "Stand By Me". Only drop ASR noise words.
            song = rest.strip()
            # Cut trailing complaints ("you didnt play it", "مش شغالة"):
            # they belong to the conversation, not the song title.
            low_song = song.lower()
            for marker in _PLAY_COMPLAINT_MARKERS:
                idx = low_song.find(marker)
                if idx > 0:
                    song = song[:idx].strip(" .,!؟.،")
                    break
            noise = {"يا", "الاغنيه", "الاغنية", "اغنيه", "اغنية"}
            words = [w for w in song.split() if w not in noise]
            song = " ".join(words).strip(" .,!؟.،")
            if not song:
                return None  # bare "play" -> AI asks what to play
            # Generic/mood request ("شغل اي اغنيه", "play something chill")
            # -> hand to the AI, which picks a CONCRETE fitting track instead
            # of searching Spotify for the literal mood word.
            if _is_generic_song_request(song):
                return None
            # "play it" / "play that again" -> replay the last search.
            core = [w for w in song.lower().split()
                    if w not in ("again", "تاني")]
            if core and all(w in _PLAY_PRONOUNS for w in core):
                if _LAST_FAST_SONG[0]:
                    return "pc_spotify_play", {"query": _LAST_FAST_SONG[0]}
                return None  # nothing to replay -> AI handles with context
            _LAST_FAST_SONG[0] = song
            return "pc_spotify_play", {"query": song}
    return None


# ── Fast path: unambiguous scrape commands ───────────────────────
# "scrape the text from example.com" / "scrape links from https://x.com"
# execute directly -- no LLM routing needed when the request is simple.

_FAST_SCRAPABLE = ("text", "article", "read", "body", "content",
                   "links", "tables", "table", "data")


def _fast_path_scrape(user_input: str):
    """Match a simple 'scrape X from <url>' command -> (tool, args) | None.

    Conservative by design: exactly one URL, exactly one known extraction
    target, no comparative/conditional phrasing ("find the cheapest price")
    -- anything ambiguous goes to the AI.
    """
    if not user_input:
        return None
    low = user_input.strip().lower()
    if "scrape" not in low and "scrap" not in low:
        return None
    # Reject questions/conditionals -- these need the AI
    if ("?" in low or low.startswith(("what", "which", "how", "why",
                                      "can you", "could you"))
            or " and " in low or " then " in low
            or "compare" in low or "find" in low):
        return None
    urls = re.findall(r"https?://\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}\S*",
                      low)
    if len(urls) != 1:
        return None
    url = urls[0].rstrip(".,!?\"'")
    words = low.replace(url, " ").split()
    if "scrape" not in words:
        return None
    # The extraction target = first scrapable word that isn't the verb
    target = next((w for w in words
                   if w not in ("scrape", "scrap") and w in _FAST_SCRAPABLE),
                  None)
    if not target:
        return None
    mode = {"text": "article", "article": "article", "read": "article",
            "body": "article", "content": "article",
            "links": "links", "table": "table", "tables": "table",
            "data": "table"}[target]
    return "web_scrape_page", {"url": url, "mode": mode}


# Max tool-calling rounds per request (Gemini -> execute -> Gemini -> execute ...)
MAX_TOOL_ROUNDS = 10

# Hard wall-clock cap for ONE Gemini SDK call. Google endpoints stall
# indefinitely on some networks; without this the brain blocks and the
# fallback providers (Groq/NVIDIA/...) never get tried.
GEMINI_CALL_CAP_SECONDS = 9.0


def _run_bounded(fn, timeout: float):
    """Run sync callable fn() under a hard wall-clock cap.

    Returns fn()'s result, or None if it did not finish within `timeout`.
    On timeout the worker thread is abandoned (daemon -> dies with the
    process). We deliberately do NOT use
    asyncio.wait_for(asyncio.to_thread(...)) here: it cannot cancel a
    running thread, so it silently waits for a stalled SDK call to
    finish -- which is exactly the multi-minute hang this helper kills.

    Exceptions raised by fn() propagate to the caller so existing error
    handling (rate-limit detection etc.) keeps working unchanged.
    """
    box: dict = {}

    def _runner():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- handed to the joiner
            box["error"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return None
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── Think-before-act gate ─────────────────────────────────────────────
# Every fast-path action (open/close/play/switch...) is shown to a small
# AI check BEFORE it touches the laptop. The gate can correct a mangled
# ASR argument ("fake hacker danielle" -> the real app/track), pass it
# unchanged, or block it. It fails OPEN: if no AI answers inside the
# budget, the action proceeds as before, so a dead network can never
# make the assistant unable to open an app.
ACTION_GATE_TIMEOUT = float(os.getenv("JARVIS_ACTION_GATE_TIMEOUT", "2.5"))
ACTION_GATE_ENABLED = os.getenv("JARVIS_ACTION_GATE", "1") not in {"0", "false", "False"}

_ACTION_GATE_PROMPT = (
    "You are the safety & intent gate of a desktop assistant. The user said:\n"
    "  USER: {user_input}\n"
    "The assistant is about to run the tool {tool} with arguments {args}.\n\n"
    "Decide if this action matches what the user wants. Rules:\n"
    "1. If the app/query/window argument is obviously mangled speech and "
    "you can infer the REAL target (a well-known app, song, artist, website "
    "or file), return the corrected arguments.\n"
    "2. If the action plausibly matches the request even with imperfect "
    "speech recognition, pass it through unchanged.\n"
    "3. Block only when the action would clearly do something the user did "
    "NOT ask for, or is destructive (close/delete/format/kill on the wrong "
    "target). Ordinary launches are not dangerous.\n\n"
    "Reply with ONLY a JSON object, no markdown:\n"
    '{{"decision": "allow" | "correct" | "block", '
    '"args": <the arguments to run>, '
    '"reason": "<max 8 words>"}}'
)


def _parse_gate_json(text: str):
    """Parse the gate model's reply; tolerate markdown fences."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start:end + 1])
    except Exception:
        return None
    decision = str(obj.get("decision", "")).lower()
    if decision not in {"allow", "correct", "block"}:
        return None
    if decision == "correct" and not isinstance(obj.get("args"), dict):
        return None
    return obj

# Schema keys that Gemini rejects (OpenAI-specific extensions)
_STRIP_SCHEMA_KEYS = frozenset({
    "examples", "title", "additionalProperties", "additional_properties",
    "minimumLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "pattern", "default", "deprecated", "readOnly", "writeOnly",
})


def _clean_schema(obj):
    """Recursively strip keys that Gemini's FunctionDeclaration rejects."""
    if isinstance(obj, dict):
        return {k: _clean_schema(v) for k, v in obj.items() if k not in _STRIP_SCHEMA_KEYS}
    elif isinstance(obj, list):
        return [_clean_schema(i) for i in obj]
    return obj


class JarvisBrain:
    """
    The AI brain of Jarvis.
    Orchestrates Gemini AI with Composio tools for Google service integration.
    """

    def remaining_time(self, deadline: Optional[float]) -> float:
        """Seconds left before a request deadline expires.

        A None deadline means "no ceiling" -- return a large sentinel so
        callers can still do arithmetic like remaining - 0.5 safely.
        """
        if deadline is None:
            return 999.0
        return max(0.0, deadline - time.monotonic())

    def __init__(self, db: Database):
        self.db = db
        self.commands = CommandRegistry()
        self.composio = None
        self.composio_session = None
        self.gemini_client = None
        self.memory = MemoryStore(db)
        self._tools_description = ""
        self._gemini_tool = None  # types.Tool wrapping FunctionDeclarations
        self._mcp_url = None
        self._mcp_headers = {}
        self._composio_tool_names = set()   # for text-action validation
        self._last_text_action = None       # one-shot guard state
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize Composio, Gemini, and PC control tools."""
        try:
            # Initialize Gemini
            if GEMINI_API_KEY:
                from google import genai
                self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
                logger.info(" Gemini AI initialized")
            else:
                logger.warning("  No GEMINI_API_KEY -- AI responses will be limited")

            # Initialize Composio (non-fatal if it fails)
            if COMPOSIO_API_KEY:
                try:
                    from composio import Composio
                    self.composio = Composio(api_key=COMPOSIO_API_KEY)
                    logger.info(" Composio initialized")

                    # Create a session for the local user (with MCP endpoint)
                    self.composio_session = self.composio.create(
                        user_id="jarvis_user", mcp=True
                    )
                    tools = self.composio_session.tools()
                    self._tools_description = self._describe_tools(tools)
                    # Expose MCP endpoint
                    if hasattr(self.composio_session, 'mcp') and self.composio_session.mcp:
                        self._mcp_url = self.composio_session.mcp.url
                        self._mcp_headers = self.composio_session.mcp.headers
                        logger.info(f" MCP endpoint: {self._mcp_url}")
                    logger.info(
                        f" Composio session {self.composio_session.session_id} "
                        f"created with {len(tools)} meta-tools"
                    )
                except Exception as comp_err:
                    logger.warning(f"  Composio unavailable: {comp_err}")
                    self.composio = None
                    self.composio_session = None
            else:
                logger.info("  No COMPOSIO_API_KEY -- Google integrations unavailable")

            # Build combined Gemini tool (PC tools + Composio tools)
            _comp_tools = self.composio_session.tools() if self.composio_session else []
            self._composio_tool_names = {
                t.get("function", {}).get("name", "")
                for t in _comp_tools if isinstance(t, dict)
            } - {""}
            self._gemini_tool = self._build_gemini_tool(_comp_tools)
            pc_count = len(get_pc_tool_declarations())
            mem_count = len(get_memory_tool_declarations())
            composio_count = len(self.composio_session.tools()) if self.composio_session else 0
            logger.info(
                f" Gemini tools ready: {pc_count} PC + {mem_count} memory + {composio_count} Composio"
            )

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f" Brain initialization failed: {e}")
            logger.debug(traceback.format_exc())
            return False

    # ── Deadline-capped tool execution ────────────────────────────────────

    async def execute_quick(self, user_input: str) -> str:
        """Answer `user_input` with a 5-second hard ceiling.

        Uses the deterministic fast path first, then a single low-latency
        AI round if needed (context is deliberately lean; tool-calling 
        rounds are skipped; screenshot is dropped to save latency).
        """
        deadline = time.monotonic() + 5.0

        # Fast path -- runs in-process, returns inside well under 5s.
        fast = _fast_path_command(user_input)
        if fast is not None:
            fp_tool, fp_args = fast
            # Think-before-act: a small AI check reviews the action before
            # anything touches the laptop. May correct mangled ASR args or
            # block a mismatched/dangerous action; fails open.
            fp_args, blocked = self._action_gate(
                user_input, fp_tool, fp_args, deadline=deadline)
            if blocked is not None:
                reply = f"I held off on that: {blocked}"
                self.db.add_message("assistant", reply)
                self.db.log_command(user_input, intent="fast_path_blocked",
                                    success=False)
                return reply
            logger.info("  [FAST PATH] %s(%s)", fp_tool,
                        json.dumps(fp_args, ensure_ascii=False))
            fp_result_str = self._execute_tool(fp_tool, fp_args)
            try:
                fp_result = json.loads(fp_result_str)
            except Exception:
                fp_result = {}
            fp_ok = bool(fp_result.get("success", False))
            fp_spoken = str(fp_result.get("data") or fp_result.get("error") or "Done.")
            if fp_ok:
                reply = f"Done. {fp_spoken}"
            else:
                reply = "I couldn't do that: " + fp_spoken
            self.db.add_message("user", user_input)
            self.db.add_message("assistant", reply)
            self.db.log_command(user_input, intent="fast_path",
                                success=fp_ok)
            return reply

        # Lean chat fallback: no tools, no screenshot, no multi-round dance.
        remaining = self.remaining_time(deadline)
        if remaining < 1.5:
            return "I'm a bit busy right now -- try again in a moment."

        context = self.db.get_conversation_context(limit=4)  # trim for speed
        try:
            reply = await self._groq_fallback(
                user_input, context, screen_image=None,
                model_budget=remaining - 0.5, deadline=deadline,
            )
        except Exception as e:
            logger.warning("execute_quick fallback failed: %s", e)
            reply = "I couldn't respond quickly enough. Please try again."

        self.db.add_message("assistant", reply)
        self.db.log_command(user_input, intent="execute_quick", success=True)
        return reply

    # ── Tool Conversion ──────────────────────────────────────────

    def _describe_tools(self, tools) -> str:
        """Generate a description of available Composio tools for the system prompt."""
        if not tools:
            return ""
        descriptions = []
        for tool in tools:
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                name = func.get("name", "unknown")
                desc = func.get("description", "No description")
            else:
                name = getattr(tool, "name", "unknown")
                desc = getattr(tool, "description", "No description")
            descriptions.append(f"- {name}: {desc[:120]}")
        return "\n".join(descriptions)

    def _build_gemini_tool(self, composio_tools):
        """Build a single Gemini types.Tool with PC tools + Composio tools."""
        from google.genai import types

        decls = []

        # 1. Always include PC control tools + persistent memory tools
        decls.extend(get_pc_tool_declarations())
        decls.extend(get_memory_tool_declarations())

        # 2. Add Composio meta-tools if available
        for tool in composio_tools:
            if not isinstance(tool, dict) or "function" not in tool:
                continue
            func = tool["function"]
            name = func.get("name", "")
            description = func.get("description", "")
            params = _clean_schema(func.get("parameters", {}))

            decl = types.FunctionDeclaration(
                name=name,
                description=description,
                parameters=params if params else None,
            )
            decls.append(decl)

        if not decls:
            return None
        return types.Tool(function_declarations=decls)

    # ── Unified Tool Execution (shared by Gemini + fallback chain) ──

    # ── Think-before-act gate ─────────────────────────────────────────

    def _gate_client(self):
        """Lean OpenAI-compatible client for the gate (cached, no retries)."""
        client = getattr(self, "_gate_client_cache", None)
        if client is None:
            import os as _os
            from openai import OpenAI
            from jarvis.config import OPENAI_COMPAT_PROVIDERS
            key = _os.getenv("GROQ_API_KEY", "")
            base = OPENAI_COMPAT_PROVIDERS.get("groq", {}).get("base_url")
            if not key or not base:
                return None
            client = self._gate_client_cache = OpenAI(
                base_url=base, api_key=key, max_retries=0)
        return client

    def _gate_model(self) -> str:
        """Cheapest fast chat model available for the gate."""
        from jarvis.config import FALLBACK_PROVIDERS
        for entry in FALLBACK_PROVIDERS:
            if entry["provider"] == "groq":
                models = entry.get("models") or []
                if models:
                    return models[0]
        return "llama-3.1-8b-instant"

    def _action_gate(self, user_input: str, tool: str, args: dict,
                     deadline: Optional[float] = None) -> tuple:
        """Ask a small model to sanity-check a fast-path action first.

        Returns (args, blocked_reason). args may be corrected by the
        gate. Fail-open policy: any timeout/error/absent AI returns the
        original args unblocked, so commands still work offline.
        Hard-capped so the 5s ceiling is never eaten by the check.
        """
        if not ACTION_GATE_ENABLED:
            return args, None
        budget = ACTION_GATE_TIMEOUT
        if deadline is not None:
            budget = min(budget, max(0.8, self.remaining_time(deadline) - 2.0))
        if budget < 0.8:
            return args, None  # no room to think -- act (fail-open)

        prompt = _ACTION_GATE_PROMPT.format(
            user_input=user_input[:300], tool=tool,
            args=json.dumps(args, ensure_ascii=False, default=str))

        try:
            text = None
            if self.gemini_client:
                from google.genai import types as gtypes
                try:
                    resp = _run_bounded(
                        lambda: self.gemini_client.models.generate_content(
                            model=DEFAULT_GEMINI_MODEL, contents=prompt,
                            config=gtypes.GenerateContentConfig(
                                temperature=0.0, max_output_tokens=120)),
                        budget)
                    if resp is not None:
                        text = getattr(resp, "text", None)
                except Exception as gem_err:
                    # Gemini down/rate-limited: fall through to the Groq
                    # tier instead of failing the whole gate open.
                    logger.debug("  [GATE] Gemini tier unavailable (%s)", gem_err)
                    text = None
            if text is not None and _parse_gate_json(text) is None:
                text = None  # unparsable -> let the Groq tier try
            if text is None:
                client = self._gate_client()
                if client is not None:
                    model = self._gate_model()
                    r = _run_bounded(
                        lambda: client.chat.completions.create(
                            model=model,
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=120, temperature=0.0,
                            timeout=budget),
                        budget)
                    if r is not None and r.choices:
                        text = r.choices[0].message.content
            verdict = _parse_gate_json(text or "")
            if verdict is None:
                return args, None  # unparsable/no AI -> fail-open
            decision = verdict["decision"]
            reason = str(verdict.get("reason", ""))[:80]
            if decision == "correct":
                fixed = dict(verdict.get("args") or {})
                if fixed:
                    logger.info("  [GATE] corrected %s args: %s -> %s (%s)",
                                tool, args, fixed, reason)
                    return fixed, None
                return args, None
            if decision == "block":
                logger.info("  [GATE] blocked %s(%s): %s", tool, args, reason)
                return None, reason or "that doesn't look right"
            return args, None
        except Exception as e:
            logger.warning("  [GATE] failed open (%s)", e)
            return args, None

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Execute any tool by name; returns the result as a JSON string."""
        if tool_name == "pc_screen_vision":
            logger.info("  [VISION] Capturing screen for analysis...")
            png = capture_screen_png()
            if png is not None:
                self._pending_screen_png = png
                return json.dumps({
                    "success": True,
                    "data": ("Screenshot captured. The image is delivered to "
                             "you -- analyze it and answer the user's question."),
                })
            return json.dumps({
                "success": False,
                "error": "Screen capture failed (Pillow missing or no active display).",
            })
        if is_pc_tool(tool_name):
            logger.info(f"  [PC TOOL] Executing: {tool_name}({json.dumps(tool_args, default=str)[:200]})")
            result = execute_pc_tool(tool_name, tool_args)
            logger.info(f"  [PC TOOL] Result: {result[:300]}")
            return result
        if is_memory_tool(tool_name):
            logger.info(f"  [MEMORY] {tool_name}({json.dumps(tool_args, default=str)[:200]})")
            result = execute_memory_tool(tool_name, tool_args, self.memory)
            logger.info(f"  [MEMORY] Result: {result[:200]}")
            return result
        return self._execute_composio_tool(tool_name, tool_args)

    def _fallback_tools(self) -> list[dict]:
        """Build the OpenAI function-calling schema for the fallback chain.

        Mirrors the Gemini tool set: PC control + memory + Composio, so
        fallback models can actually DO things instead of pretending.
        """
        from google.genai import types as gtypes

        def _lowercase_schema(node):
            """Recursively normalize Gemini schema output to OpenAI JSON schema.

            Gemini's Type enum dumps as 'OBJECT'/'STRING' (uppercase); the
            OpenAI-compatible endpoints require lowercase JSON types.
            """
            if isinstance(node, dict):
                out = {}
                for k, v in node.items():
                    if k == "type" and isinstance(v, str):
                        out[k] = v.lower()
                    else:
                        out[k] = _lowercase_schema(v)
                return out
            if isinstance(node, list):
                return [_lowercase_schema(x) for x in node]
            return node

        def decl_to_openai(d) -> dict:
            # mode='json' is critical: Gemini Schema enums must serialize to
            # plain JSON, then types are lowercased for OpenAI compatibility.
            try:
                params = d.parameters.model_dump(mode="json", exclude_none=True) if d.parameters else None
            except Exception:
                params = None
            if isinstance(params, dict):
                params = _lowercase_schema(params)
            if not params or not params.get("properties"):
                params = {"type": "object", "properties": {}}
            return {"type": "function", "function": {
                k: v for k, v in {
                    "name": d.name,
                    "description": d.description or "",
                    "parameters": params,
                }.items() if v is not None
            }}

        # Groq free tier: 8k TPM hard ceiling, and our full payload once
        # crossed it (413 -> chain died -> "offline mode" replies). These
        # caps keep the tools portion well under the ~6k-token target.
        MAX_DESC_LEN = 80        # parameter/function description chars
        MAX_TOOL_JSON_CHARS = 15_000  # all tools serialized, ~3.7k tokens

        def _trim_descriptions(node):
            """Cap every 'description' string at MAX_DESC_LEN chars."""
            if isinstance(node, dict):
                out = {}
                for k, v in node.items():
                    if k == "description" and isinstance(v, str):
                        out[k] = v if len(v) <= MAX_DESC_LEN else v[:MAX_DESC_LEN - 1].rstrip() + "…"
                    else:
                        out[k] = _trim_descriptions(v)
                return out
            if isinstance(node, list):
                return [_trim_descriptions(x) for x in node]
            return node

        def _strip_heavy(node):
            """Drop bulk-only schema keys from any tool."""
            if isinstance(node, dict):
                out = {k: _strip_heavy(v) for k, v in node.items()
                       if k not in {"examples", "example", "title",
                                    "execution_guidance",
                                    "recommended_plan_steps",
                                    "known_pitfalls"}}
                return out
            if isinstance(node, list):
                return [_strip_heavy(x) for x in node]
            return node

        def _strip_nones(node):
            """Remove every None-valued key recursively, at any depth.

            Groq rejects e.g. 'strict': null with 400 "Value is not
            nullable". Composio is the known offender, but nothing guarantees
            other layers stay clean -- apply to ALL tools.
            """
            if isinstance(node, dict):
                return {k: _strip_nones(v) for k, v in node.items()
                        if v is not None}
            if isinstance(node, list):
                return [_strip_nones(x) for x in node]
            return node

        def _finalize(t):
            """Sanitize one converted tool for OpenAI-compatible endpoints."""
            try:
                f = dict(t["function"])
                f["description"] = (f.get("description") or "")[:MAX_DESC_LEN]
                f["parameters"] = _trim_descriptions(
                    _strip_heavy(_strip_nones(f.get("parameters") or {})))
                # Belt-and-braces: strict must be absent or a bool.
                if "strict" in f and not isinstance(f["strict"], bool):
                    del f["strict"]
                return {"type": "function", "function": f}
            except Exception:
                return t

        tools: list[dict] = []
        try:
            tools.extend(_finalize(decl_to_openai(d)) for d in get_pc_tool_declarations())
            tools.extend(_finalize(decl_to_openai(d)) for d in get_memory_tool_declarations())
        except Exception as e:
            logger.warning("PC/memory tool schema conversion failed: %s", e)
        if self.composio_session:
            # Composio returns OpenAI-ish dicts but with None-valued fields
            # (e.g. strict: null) that Groq rejects with "Value is not
            # nullable" -- _finalize strips every None field recursively.
            try:
                tools.extend(_finalize(_strip_nones(t))
                             for t in self.composio_session.tools())
            except Exception as e:
                logger.warning("Composio tool schema cleanup failed: %s", e)

        # Last-resort size guard: if the serialized set is still oversized,
        # drop the heaviest tools (rarely needed meta-tools first) until it
        # fits. Groq 413 used to kill the entire chain; a missing optional
        # tool degrades gracefully instead.
        def _tool_size(t):
            try:
                return len(json.dumps(t))
            except Exception:
                return 0

        try:
            while len(tools) > 4 and sum(_tool_size(t) for t in tools) > MAX_TOOL_JSON_CHARS:
                heaviest = max(range(len(tools)), key=lambda i: _tool_size(tools[i]))
                dropped = tools.pop(heaviest)
                logger.info("  [FALLBACK TOOLS] payload over %d chars -> dropped %s",
                            MAX_TOOL_JSON_CHARS,
                            dropped.get("function", {}).get("name", "?"))
        except Exception:
            pass
        return tools

    # ── Text-action parsing (ReAct-style fallback) ──

    # Known tool names: PC + memory tools are static; Composio names come
    # from the live session when available.
    def _known_tool_names(self) -> set:
        names = set()
        try:
            names.update(t["function"]["name"] for t in self._fallback_tools())
        except Exception:
            pass
        if self._composio_tool_names:
            names.update(self._composio_tool_names)
        return names

    def _parse_text_action(self, text: str):
        """Extract a tool action the model printed as plain text.

        Some fallback models (notably nemotron, which runs without a tool
        schema) answer with ReAct-style blocks instead of native tool_calls:

            {"action": "pc_open_app", "action_input": "spotify"}
            Action: pc_open_app
            Action Input: spotify

        Without this parser the JSON gets spoken to the user verbatim and
        nothing executes. Returns (tool_name, args) or None.
        """
        if not text:
            return None
        candidate = text.strip()

        # Shape 1: a bare (or fenced) JSON object with an action field
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
        if fenced:
            candidate = fenced.group(1).strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    name = obj.get("action") or obj.get("tool") or obj.get("name")
                    if isinstance(name, str) and name.strip():
                        raw_args = (obj.get("action_input")
                                    or obj.get("tool_input")
                                    or obj.get("arguments")
                                    or obj.get("args")
                                    or {})
                        if isinstance(raw_args, str):
                            raw_args = raw_args.strip()
                            if not raw_args:
                                raw_args = {}
                            else:
                                try:
                                    raw_args = json.loads(raw_args)
                                except Exception:
                                    raw_args = {"input": raw_args}
                        if not isinstance(raw_args, dict):
                            raw_args = {"input": raw_args}
                        return name.strip(), raw_args
            except Exception:
                pass

        # Shape 2: ReAct "Action: X / Action Input: Y" (optionally fenced)
        m = re.search(
            r"Action\s*:\s*([A-Za-z0-9_]+)\s*(?:\n|\r\n?)\s*Action Input\s*:\s*(.+)",
            text, re.S)
        if m:
            name = m.group(1).strip()
            raw = m.group(2).strip().strip("`")
            try:
                args = json.loads(raw)
            except Exception:
                args = {"input": raw.strip("\"'")}
            return name, (args if isinstance(args, dict) else {"input": args})

        # Shape 3: nemotron bracket-call syntax -- [tool_name] / [calling
        # tool_name] / [calling tool_name(args-json)] with the query either
        # as JSON in the bracket, a quoted string after, or the next line.
        # Example from a real failure:
        #   "Let me check YouTube for new videos...\n\n[web_search]\n\n"
        m = re.search(
            r"\[\s*(?:calling\s+)?(?:tool\s+)?([A-Za-z0-9_]+)\s*"
            r"(?:\(\s*(\{.*?\})\s*\)|\(([^)]*)\))?\s*\]",
            text, re.S)
        if m:
            name = m.group(1).strip()
            raw = (m.group(2) or m.group(3) or "").strip()
            if raw:
                try:
                    args = json.loads(raw)
                except Exception:
                    # quoted single arg, or bare text
                    q = re.match(r"[\"'](.+)[\"']$", raw)
                    args = {"input": (q.group(1) if q else raw).strip()}
            else:
                # No args in the bracket: take a quoted phrase or the next
                # non-empty line as the intent ("search YouTube..." etc.).
                after = text[m.end():].strip()
                q = re.match(r"[\"'](.+?)[\"']", after)
                if q:
                    args = {"input": q.group(1)}
                else:
                    nl = after.split("\n", 1)[0].strip()
                    args = {"input": nl[:200]} if nl else {}
            if isinstance(args, dict):
                return name, args

        return None

    def _run_text_action(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Execute a parsed text action once, safely.

        Unknown tools are rejected (no blind dispatch). One-shot guard
        prevents an echo loop if the model repeats the same action in its
        follow-up reply.
        """
        key = (tool_name, json.dumps(tool_args, sort_keys=True, default=str))
        if key == getattr(self, "_last_text_action", None):
            return json.dumps({"error": (
                "You already performed exactly this action. Do NOT repeat "
                "the action block -- just tell the user the result in words."
            )})
        if tool_name not in self._known_tool_names():
            # Common alias attempts by text-only models (nemotron calls the
            # web-search tool "web_search"; the real name is pc_web_search).
            aliases = {
                "web_search": "pc_web_search",
                "search_web": "pc_web_search",
                "open_app": "pc_open_app",
                "focus_window": "pc_focus_window",
                "run_command": "pc_run_command",
                "type_text": "pc_type_text",
                "screenshot": "pc_take_screenshot",
                "screen_vision": "pc_screen_vision",
                "composio_connect": None,  # does not exist -- never dispatch
            }
            mapped = aliases.get(tool_name, "__missing__")
            if mapped is None:
                return json.dumps({
                    "error": f"Tool '{tool_name}' does not exist and must not "
                             "be simulated. Tell the user honestly that you "
                             "cannot perform it.",
                })
            if mapped == "__missing__":
                return json.dumps({
                    "error": f"Unknown tool '{tool_name}'. "
                             "Use one of the provided tool names exactly.",
                })
            tool_name = mapped
        # Bare-string action_input ("spotify") -> the tool's real arg name
        if set(tool_args) == {"input"} and isinstance(tool_args["input"], str):
            common = {
                "pc_open_app": "app_name",
                "pc_focus_window": "window_name",
                "pc_run_command": "command",
                "pc_type_text": "text",
                "pc_hotkey": "keys",
                "pc_search_web": "query",
                "web_scrape_page": "url",
                "web_browser_interact": "url",
                "pc_mouse_action": "action",
                "pc_web_search": "query",
            }
            if tool_name in common:
                tool_args = {common[tool_name]: tool_args["input"]}
        self._last_text_action = key
        result = self._execute_tool(tool_name, tool_args)
        self._last_text_action_result = result
        return result

    def _execute_composio_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute a Composio meta-tool and return the result as a string."""
        logger.info(f"  [COMPOSIO] Executing: {tool_name}")
        logger.info(f"  [COMPOSIO] Args: {json.dumps(args, default=str)[:500]}")
        try:
            # Pass session_id automatically
            if self.composio_session:
                args["session_id"] = self.composio_session.session_id

            logger.info(f"  [COMPOSIO] Calling session.execute()...")
            result = self.composio_session.execute(tool_name, arguments=args)
            logger.info(f"  [COMPOSIO] Result type: {type(result).__name__}")

            if hasattr(result, "data"):
                data = result.data
                if isinstance(data, dict):
                    out = json.dumps(data, indent=2, default=str)
                    logger.info(f"  [COMPOSIO] Data (dict, {len(data)} keys): {list(data.keys())[:10]}")
                    logger.info(f"  [COMPOSIO] Data preview: {out[:500]}")
                    return out
                out = str(data)
                logger.info(f"  [COMPOSIO] Data preview: {out[:500]}")
                return out

            if hasattr(result, "error") and result.error:
                logger.warning(f"  [COMPOSIO] Error in response: {result.error}")

            out = str(result)
            logger.info(f"  [COMPOSIO] Full result preview: {out[:500]}")
            return out
        except Exception as e:
            logger.error(f"  [COMPOSIO] FAILED: {tool_name}: {e}")
            import traceback
            logger.error(f"  [COMPOSIO] Traceback: {traceback.format_exc()}")
            return json.dumps({"error": str(e)})

    # ── Main Processing Pipeline ─────────────────────────────────

    async def process(self, user_input: str, screenshot: bool = False) -> str:
        """
        Process user input through the full pipeline:
        1. Store user message
        2. Build context + system prompt (with memories injected)
        3. Call Gemini with tools (PC + memory + Composio)
        4. Execute any tool calls in a loop
        5. Generate final response
        6. Store and return

        Args:
            screenshot: If True, capture the current screen and attach it
                so Jarvis can SEE what the user sees (screen vision).
        """
        # Store user message
        self.db.add_message("user", user_input)
        deadline = time.monotonic() + 5.0  # hard ceiling: answer within 5s        # ── Deterministic fast-path: basic PC commands skip the AI ──
        # Fallback models sometimes role-play instead of calling tools;
        # "open spotify" must work even when every LLM misbehaves.
        fast_note = None  # optional AI hand-off note set by the fast path
        fast = _fast_path_command(user_input)
        if fast is not None:
            fp_tool, fp_args = fast
            # Think-before-act: a small AI check reviews the action before
            # anything touches the laptop. May correct mangled ASR args or
            # block a mismatched/dangerous action; fails open.
            fp_args, blocked = self._action_gate(
                user_input, fp_tool, fp_args, deadline=deadline)
            if blocked is not None:
                reply = f"I held off on that: {blocked}"
                self.db.add_message("assistant", reply)
                self.db.log_command(user_input, intent="fast_path_blocked",
                                    success=False)
                return reply
            logger.info("  [FAST PATH] %s(%s)", fp_tool,
                        json.dumps(fp_args, ensure_ascii=False))
            fp_result_str = self._execute_tool(fp_tool, fp_args)
            try:
                fp_result = json.loads(fp_result_str)
            except Exception:
                fp_result = {}
            fp_ok = bool(fp_result.get("success", False))
            fp_spoken = str(fp_result.get("data") or fp_result.get("error") or "Done.")
            if fp_ok:
                reply = f"Done. {fp_spoken}"
                self.db.add_message("assistant", reply)
                self.db.log_command(user_input, intent="fast_path",
                                    success=True)
                return reply
            else:
                reply = "I couldn't do that: " + fp_spoken
                self.db.add_message("assistant", reply)
                self.db.log_command(user_input, intent="fast_path",
                                    success=False)
                return reply

        # Build context
        context = self.db.get_conversation_context(limit=10)
        if fast_note:
            context = context + "\n\n" + fast_note

        # Screen vision: capture the display if requested
        screen_image = None
        if screenshot:
            screen_image = self._capture_screen_bytes()

        # Always use tool pipeline (PC tools are always available)
        if self._gemini_tool:
            try:
                response = await self._process_with_tools(
                    user_input, context, screen_image, deadline=deadline)
            except Exception as e:
                logger.error(f"Tool pipeline failed: {e}")
                logger.debug(traceback.format_exc())
                # Keep the screenshot so fallback VISION can still answer
                # "what's on my screen" while Gemini is down.
                # Fresh budget for the chain -- see the note in
                # _process_with_tools; scraps-of-deadline = skipped providers.
                response = await self._groq_fallback(
                    user_input, context, screen_image,
                    model_budget=14.0,
                    deadline=time.monotonic() + 15.0,
                )
        elif screen_image:
            # No Gemini tool pipeline: vision-capable fallback handles it.
            response = await self._groq_fallback(user_input, context, screen_image)
        else:
            response = await self._process_chat_only(user_input, context)

        # Store assistant response
        self.db.add_message("assistant", response)
        self.db.log_command(user_input, intent="processed", success=True)

        return response

    def _capture_screen_bytes(self) -> Optional[bytes]:
        """Capture the current screen as PNG bytes for Gemini vision."""
        try:
            from jarvis.tools.pc_control import capture_screen_png
            return capture_screen_png(max_dim=1600)
        except Exception as e:
            logger.warning(f" Screen capture failed: {e}")
            return None

    async def _process_with_tools(
        self,
        user_input: str,
        context: str,
        screen_image: Optional[bytes] = None,
        *,
        deadline: Optional[float] = None,
    ) -> str:
        """Process with tool-calling via Gemini.

        Implements the tool-calling loop:
        1. Send user message + tools to Gemini
        2. If Gemini calls a tool -> execute it locally/via Composio -> send result back
        3. Repeat until Gemini produces a text response (or max rounds)
        """
        from google.genai import types

        # Inject persistent memories into the system prompt
        memory_block = self.memory.prompt_block()
        system_prompt = get_jarvis_system_prompt(self._tools_description)
        if memory_block:
            system_prompt += (
                "\n\nWHAT YOU REMEMBER ABOUT THE USER (long-term memory):\n"
                f"{memory_block}\n"                "Use this naturally when relevant. Never claim you cannot remember."
            )
        # Speed optimization: if we're already tight on time, skip the
        # slower tool pipeline and go straight to a lean fallback.
        # NOTE: outside `if memory_block:` -- this used to live inside it,
        # leaving `config` unbound for memory-less users (UnboundLocalError
        # on every tool-pipeline request).
        remaining = self.remaining_time(deadline)
        if remaining < 3.0:
            logger.info("  Tool pipeline skipped: tight deadline (%ss left)", remaining)
            # Fresh budget: the fallback chain must get a real chance to
            # answer, not the scraps of an exhausted deadline (which made
            # every provider get skipped -> "offline mode" replies).
            return await self._groq_fallback(
                user_input, context, screen_image,
                model_budget=14.0, deadline=time.monotonic() + 15.0,
            )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[self._gemini_tool],
        )

        # Build conversation messages
        messages = []
        context_msgs = self.db.get_recent_messages(limit=8)
        for msg in context_msgs:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                messages.append(types.Content(role="user", parts=[types.Part(text=content)]))
            else:
                messages.append(types.Content(role="model", parts=[types.Part(text=content)]))

        # Add current input (with optional screen image for vision)
        if screen_image:
            current_parts = [
                types.Part(text=(
                    "[The user has shared a screenshot of their current screen. "
                    "It is attached below. Consider it when answering.]\n\n"
                    f"User message: {user_input}"
                )),
                types.Part(inline_data=types.Blob(mime_type="image/png", data=screen_image)),
            ]
            messages.append(types.Content(role="user", parts=current_parts))
        else:
            messages.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

        # Tool-calling loop
        for round_num in range(MAX_TOOL_ROUNDS):
            response = None
            for model_name in GEMINI_MODELS:
                try:
                    remaining = (self.remaining_time(deadline)
                                 if deadline is not None else 999.0)
                    # If too tight for a Gemini round, skip and fall straight
                    # to the fallback chain (it gets its own fresh budget).
                    if remaining < 2.0:
                        break
                    # Hard wall-clock cap on the sync SDK call. NOTE:
                    # asyncio.wait_for(asyncio.to_thread(...)) is NOT enough
                    # -- it cannot cancel a running thread, so it silently
                    # waits for a stalled endpoint to finish. _run_bounded
                    # abandons the stuck call at the budget instead, so the
                    # fallback provider chain actually gets tried.
                    call_budget = min(remaining - 0.25, GEMINI_CALL_CAP_SECONDS)
                    response = _run_bounded(
                        lambda mn=model_name: (
                            self.gemini_client.models.generate_content(
                                model=mn, contents=messages, config=config)),
                        call_budget,
                    )
                    if response is None:
                        logger.warning(
                            "Gemini %s stalled >%.1fs -> fallback chain",
                            model_name, call_budget)
                    break  # success OR stall: no more Gemini rounds
                except Exception as e:
                    err_str = str(e)
                    # Rate limited -- swap to the fallback provider chain
                    # instantly instead of stalling on a sleep-and-retry.
                    if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str or 'Too Many Requests' in err_str:
                        logger.warning(f"Gemini {model_name} rate-limited -> instant fallback")
                        break
                    logger.warning(f"Gemini {model_name} failed: {e}")
                    continue
            if response is None:
                # Every Gemini model failed (rate limit, outage, or hang):
                # answer via the OpenAI-compatible provider chain. FRESH
                # budget -- the old code passed the exhausted deadline, so
                # every fallback model got skipped and the user heard
                # "offline mode" even with working providers configured.
                logger.info("Gemini unavailable -> trying fallback provider chain")
                fallback_deadline = time.monotonic() + 15.0
                return await self._groq_fallback(user_input, context, screen_image,
                                               model_budget=14.0,
                                               deadline=fallback_deadline)

            # Check for tool calls
            if not response.candidates or not response.candidates[0].content:
                break

            parts = response.candidates[0].content.parts
            has_tool_call = False
            function_responses = []
            screen_png = None  # set when Gemini asks to look at the screen

            for part in parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}
                    logger.info(f"  Tool call: {tool_name}({json.dumps(tool_args, default=str)[:200]})")

                    # Route to the right executor (shared with fallback chain)
                    result_str = self._execute_tool(tool_name, tool_args)
                    if tool_name == "pc_screen_vision":
                        screen_png = getattr(self, "_pending_screen_png", None)

                    function_responses.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=tool_name,
                                response={"result": result_str},
                            )
                        )
                    )
                    has_tool_call = True

            if not has_tool_call:
                # No tool calls — extract text response
                text_parts = [
                    p.text for p in parts
                    if hasattr(p, "text") and p.text
                ]
                if text_parts:
                    return "\n".join(text_parts)
                break

            # Add model response (with tool calls) to conversation
            messages.append(response.candidates[0].content)

            # Add function responses
            messages.append(types.Content(role="user", parts=function_responses))

            # Attach the screen image right after the tool result so the
            # model sees the pixels it asked for (Gemini vision).
            if screen_png is not None:
                messages.append(types.Content(
                    role="user",
                    parts=[types.Part(inline_data=types.Blob(
                        mime_type="image/png", data=screen_png))],
                ))

            logger.info(f"  Round {round_num + 1}: tool executed, continuing...")

        # If we exhausted rounds, try to get a final text response
        text_parts = []
        if response and response.candidates and response.candidates[0].content:
            text_parts = [
                p.text for p in response.candidates[0].content.parts
                if hasattr(p, "text") and p.text
            ]
        if text_parts:
            return "\n".join(text_parts)

        return "I've processed your request but couldn't generate a summary. Please try again."

    async def _process_chat_only(self, user_input: str, context: str) -> str:
        """Process without Composio tools -- pure chat mode."""
        system_prompt = get_jarvis_system_prompt()

        if self.gemini_client:
            return await self._gemini_chat(user_input, system_prompt, context)
        elif self.composio is None:
            return self._offline_response(user_input)
        else:
            return await self._groq_fallback(user_input, context)

    async def _gemini_chat(
        self, user_input: str, system_prompt: str, context: str
    ) -> str:
        """Send a chat message to Gemini."""
        from google.genai import types

        full_prompt = f"{system_prompt}\n\n{context}\n\nUser: {user_input}"

        for model_name in GEMINI_MODELS:
            try:
                response = _run_bounded(
                    lambda mn=model_name: (
                        self.gemini_client.models.generate_content(
                            model=mn, contents=full_prompt)),
                    GEMINI_CALL_CAP_SECONDS,
                )
                if response is None:
                    # Endpoint stalled past the cap -- every model shares
                    # it, so go straight to the fallback provider chain.
                    logger.warning(
                        "Gemini %s stalled >%.1fs -> fallback chain",
                        model_name, GEMINI_CALL_CAP_SECONDS)
                    break
                if response.text:
                    return response.text
            except Exception as e:
                err_str = str(e)
                if ('429' in err_str or 'RESOURCE_EXHAUSTED' in err_str
                        or 'Too Many Requests' in err_str):
                    logger.warning(
                        f"Gemini {model_name} rate-limited -> instant fallback")
                    break
                logger.warning(f"Gemini {model_name} failed: {e}")
                continue

        # Fresh budget for the chain: this path is reached precisely when
        # Gemini wasted its window, so the fallbacks must not inherit the
        # scraps (scraps = every provider skipped = "offline mode").
        return await self._groq_fallback(
            user_input, context, model_budget=14.0,
            deadline=time.monotonic() + 15.0)

    def _discover_chat_models(self, client, prov: str) -> list[str]:
        """Fetch a provider's live chat-capable model catalog (cached).

        Filters out embeddings/rerankers/etc. and caps the list so a
        degraded provider can't stall the fallback chain. Result is cached
        per provider for the process lifetime.
        """
        from jarvis.config import DYNAMIC_MODEL_CAP, NON_CHAT_MODEL_KEYWORDS
        try:
            listed = client.models.list()
        except Exception as e:
            logger.warning("Model discovery failed for %s: %s", prov, e)
            return []
        found = []
        for m in listed:
            mid = getattr(m, "id", None) or str(m)
            low = mid.lower()
            if any(k in low for k in NON_CHAT_MODEL_KEYWORDS):
                continue
            found.append(mid)
        found.sort()
        logger.info(
            "Discovered %d chat models on %s (using up to %d per fallback call)",
            len(found), prov, DYNAMIC_MODEL_CAP,
        )
        return found[:DYNAMIC_MODEL_CAP]

    async def _groq_fallback(
        self,
        user_input: str,
        context: str,
        screen_image: Optional[bytes] = None,
        *,
        model_budget: float = 60.0,
        deadline: Optional[float] = None,
    ) -> str:
        """
        Multi-provider OpenAI-compatible fallback chain. Used when Gemini
        is unavailable. Each provider with an API key in .env is tried in
        FALLBACK_PROVIDERS order; the first working model answers.

        If a screenshot is attached, vision-capable models answer instead,
        so "what's on my screen" works even while Gemini is rate-limited.

        Note: fallbacks cannot use tools (PC/Composio), so the user is
        told honestly rather than hallucinating results.
        """
        import base64
        from openai import OpenAI
        from jarvis.config import (
            OPENAI_COMPAT_PROVIDERS, FALLBACK_PROVIDERS,
        )
        import os

        system_prompt = get_jarvis_system_prompt()
        if screen_image:
            tool_notice = ("\n\n[Note: I'm on a backup VISION model right now: "
                           "you CAN see the attached screenshot and describe it, "
                           "but tool actions (opening apps, checking emails, file "
                           "operations) are temporarily unavailable.]")
        else:
            tool_notice = ("\n\n[Note: I'm on a backup model right now, but you "
                           "DO have tool calls (pc_*, memory_*, Composio tools). "
                           "Use them for any action the user requests. NEVER claim "
                           "you opened/focused/checked anything unless a tool call "
                           "returned success. No role-play like 'Opening Discord...' "
                           "-- call the tool, then report the real result. "
                           "CRITICAL -- never fabricate tool results: if you cannot "
                           "actually execute a tool right now, you MUST say what you "
                           "cannot do and stop. NEVER invent search results, view "
                           "counts, video titles, emails, or any data you did not "
                           "receive from a real tool result. Saying '[web_search] ... "
                           "I found several recent results with 2.1M views' when no "
                           "tool actually ran is a serious failure.]")
            # Anti-hallucination for connection flows: fallback models were
            # inventing fake tools ("composio_connect") and generic URLs
            # (connect.composio.dev/mcp) when asked about Google services.
            # Give them the REAL connection status + the ONLY legit link.
            try:
                status_lines = []
                from composio import Composio as _C
                _c = _C(api_key=os.getenv("COMPOSIO_API_KEY", ""))
                _page = _c.connected_accounts.list()
                _items = []
                for _el in _page:
                    if isinstance(_el, tuple) and _el[0] == "items":
                        _items = _el[1]
                        break
                from collections import defaultdict as _dd
                _by_toolkit = _dd(list)
                for _it in _items:
                    _d = _it.model_dump() if hasattr(_it, "model_dump") else {}
                    _slug = (_d.get("toolkit") or {}).get("slug", "?") \
                        if isinstance(_d.get("toolkit"), dict) else "?"
                    _by_toolkit[_slug].append(str(_d.get("status", "?")))
                if _by_toolkit:
                    for _slug, _states in sorted(_by_toolkit.items()):
                        ok = any(s.upper() == "ACTIVE" for s in _states)
                        status_lines.append(
                            f"  - {_slug}: {'CONNECTED' if ok else 'NOT connected (auth expired or never linked)'}")
                else:
                    status_lines.append("  - (no Composio connections exist yet)")
                tool_notice += (
                    "\n\n[REAL Composio connection status right now:\n"
                    + "\n".join(status_lines))
                # Disconnected Google toolkits: hand the model the REAL
                # one-time auth link (cached per process -- the first
                # fallback call pays one router call, the rest reuse it).
                # Text-only models like nemotron cannot execute tools, so
                # without this they echo fake URLs from chat history.
                _disconnected = sorted(
                    _slug for _slug, _states in _by_toolkit.items()
                    if not any(s.upper() == "ACTIVE" for s in _states))
                if _disconnected and self.composio_session:
                    _link = getattr(self, "_cached_auth_link", None)
                    if not _link:
                        _res = _run_bounded(
                            lambda: self.composio_session.execute(
                                "COMPOSIO_MANAGE_CONNECTIONS",
                                arguments={"toolkits": _disconnected[:3]}),
                            5.0)
                        try:
                            _data = getattr(_res, "data", None) or {}
                            for _r in (_data.get("results") or {}).values():
                                _u = _r.get("redirect_url")
                                if _u:
                                    _link = _u
                                    break
                        except Exception:
                            _link = None
                        if _link:
                            self._cached_auth_link = _link
                    if _link:
                        tool_notice += (
                            f"\n ONE-TIME AUTH LINK for {', '.join(_disconnected[:3])}: {_link}")
                tool_notice += (
                    "\nIf a toolkit is NOT connected, tell the user to open the "
                    "auth link above -- do NOT invent connection tools, flows or "
                    "URLs. There is no tool named composio_connect. Use ONLY the "
                    "auth link stated in this notice, never a link from chat "
                    "history or memory.]")
            except Exception:
                pass  # status fetch failed -- proceed without it

        user_content: Any = f"{context}\n\nUser: {user_input}"
        if screen_image:
            b64 = base64.b64encode(screen_image).decode()
            user_content = [
                {"type": "text",
                 "text": f"{context}\n\nUser: {user_input}\n\n"
                         "(A screenshot of the user's current screen is attached. "
                         "Base your answer on what is actually visible.)"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]

        messages = [
            {"role": "system", "content": system_prompt + tool_notice},
            {"role": "user", "content": user_content},
        ]

        # Token-budget guard: Groq free tier rejects whole requests over
        # 8k TPM (413), which killed the chain even after schema
        # compaction. Tool schemas (~6.2k) + system prompt (~1k) + this
        # request must fit under the limit, so the carried conversation
        # context is capped hard. Recent turns matter; ancient ones don't.
        _MAX_CONTEXT_CHARS = 1600
        if len(user_content) > 2400:  # text-mode only (str content)
            parts = user_content.split("\n\nUser: ", 1)
            if len(parts) == 2:
                ctx, ask = parts
                if len(ctx) > _MAX_CONTEXT_CHARS:
                    ctx = "..." + ctx[-_MAX_CONTEXT_CHARS:]
                user_content = f"{ctx}\n\nUser: {ask}"
                messages[1]["content"] = user_content

        for entry in FALLBACK_PROVIDERS:
            prov = entry["provider"]
            meta = OPENAI_COMPAT_PROVIDERS.get(prov, {})
            key_env = f"{prov.upper()}_API_KEY"
            api_key = os.getenv(key_env, "")
            # Keyless providers (local Ollama) need no API key -- they are
            # the offline safety net when every cloud provider is down.
            if not api_key and not meta.get("keyless"):
                continue  # no key configured for this provider
            if not meta.get("base_url"):
                continue
            if not api_key:
                api_key = "ollama"  # OpenAI SDK requires a non-empty string

            # max_retries=0: the chain itself is the retry mechanism --
            # any model that fails moves on to the next one instantly.
            # The SDK's default retries (2, with backoff) would add 10-20s
            # before failover, which defeats the fast-response ethos.
            client = OpenAI(base_url=meta["base_url"], api_key=api_key,
                            max_retries=0)

            # Tools: fallback models get the full PC/memory/Composio set in
            # OpenAI format, so they execute commands instead of hallucinating.
            try:
                oai_tools = self._fallback_tools()
            except Exception as e:
                # Tool serialization must not kill the WHOLE chain: try this
                # provider text-only and let later providers try full tools.
                logger.warning("Tool serialization failed for %s (%s) "
                               "-- continuing text-only", prov, e)
                oai_tools = []
            # Vision models can't take tool schemas reliably; tools ride
            # along only for text-mode calls.
            tools_kw = {"tools": oai_tools} if (oai_tools and not screen_image) else {}

            # Model selection: with a screenshot, only vision-capable
            # providers/models are tried -- text-only providers would 400
            # on the image payload and waste the chain.
            if screen_image:
                if not entry.get("vision_models"):
                    continue  # this provider can't see -- skip it
                models = list(entry["vision_models"])
            else:
                models = list(entry.get("models", []))
            if entry.get("dynamic") and not screen_image:
                cache = getattr(self, "_dynamic_models", None)
                if cache is None:
                    cache = self._dynamic_models = {}
                if prov not in cache:
                    cache[prov] = self._discover_chat_models(client, prov)
                for mid in cache[prov]:
                    if mid not in models:
                        models.append(mid)

            for model in models:
                try:
                    extra = {}
                    # Thinking toggle only for models that support it, so
                    # third-party NIM models don't 400 on the extra body.
                    if entry.get("no_think") and "nemotron" in model.lower():
                        extra["chat_template_kwargs"] = {"enable_thinking": False}
                    # Per-model tool set: nemotron models emit unreliable tool
                    # calls, so they run text-only -- but the strict notice
                    # keeps them honest instead of role-playing actions.
                    model_tools = tools_kw if "nemotron" not in model.lower() else {}

                    # ── Tool-calling loop (same schema as OpenAI) ──
                    convo = list(messages)
                    final_text: Optional[str] = None
                    for _round in range(MAX_TOOL_ROUNDS):
                        remaining = (self.remaining_time(deadline)
                                 if deadline is not None else 999.0)
                        max_timeout = min(60.0, remaining - 0.5)
                        call_timeout = max(1.0, min(60.0, max_timeout))
                        # If the deadline is too tight for a large model, skip to
                        # a smaller/faster one (the loop continues down the list).
                        if call_timeout < 2.0:
                            logger.debug(
                                "Skipping %s/%s: call_timeout=%.2fs "
                                "(remaining=%.2fs)", prov, model,
                                call_timeout, remaining)
                            break  # skip this model -- no time to wait for it
                        response = client.chat.completions.create(
                                model=model,
                                messages=convo,
                                max_tokens=1024,
                                timeout=call_timeout,
                                extra_body=extra or None,
                                **model_tools,
                        )
                        msg = response.choices[0].message if response.choices else None
                        if msg is None:
                            break
                        tool_calls = list(getattr(msg, "tool_calls", None) or [])
                        if not tool_calls:
                            content = msg.content or ""
                            # Some fallback models print ReAct-style action
                            # blocks as plain text instead of using native
                            # tool_calls. Parse and execute them; then give
                            # the model one follow-up round to convert the
                            # real tool result into a spoken reply.
                            action = self._parse_text_action(content)
                            if action is not None:
                                t_name, t_args = action
                                logger.info("  [TEXT ACTION] %s(%s)",
                                            t_name,
                                            json.dumps(t_args, default=str)[:200])
                                t_result = self._run_text_action(t_name, t_args)
                                logger.info("  [TEXT ACTION] Result: %s",
                                            t_result[:300])
                                convo.append(msg)
                                convo.append({"role": "user", "content": (
                                    
                                    f"Tool {t_name} executed and returned:\n"
                                    f"{t_result[:2000]}\n\nNow reply to the "
                                    "user in words only. Do NOT output another "
                                    "action block or JSON -- the action is "
                                    "already done."
                                )})
                                continue
                            final_text = content
                            break
                        convo.append(msg)  # includes the assistant tool_calls
                        for tc in tool_calls:
                            try:
                                t_name = tc.function.name
                                t_args = json.loads(tc.function.arguments or "{}")
                            except Exception:
                                t_name, t_args = "unknown", {}
                            logger.info("  [FALLBACK TOOL] %s(%s)", t_name,
                                        json.dumps(t_args, default=str)[:200])
                            t_result = self._execute_tool(t_name, t_args)
                            convo.append({"role": "tool", "tool_call_id": tc.id,
                                          "content": t_result[:4000]})
                    if final_text:
                        logger.info("Fallback answered via %s / %s", prov, model)
                        return final_text
                except Exception as e:
                    logger.warning("Fallback %s / %s failed: %s", prov, model, e)
                    continue

        # No fallback provider answered
        return self._offline_response(user_input)

    def _offline_response(self, user_input: str) -> str:
        """Provide a helpful response when no AI services are available."""
        lower = user_input.lower()

        if any(greeting in lower for greeting in ["hello", "hi", "hey", "good morning"]):
            return (
                "Hello! I'm Jarvis, your personal AI assistant. "
                "I notice my AI services aren't fully connected yet. "
                "Please make sure your API keys are set in the .env file:\n"
                "- GEMINI_API_KEY (for AI responses)\n"
                "- COMPOSIO_API_KEY (for Google integrations)\n\n"
                "Get your Composio key at https://dashboard.composio.dev"
            )

        if any(word in lower for word in ["help", "what can you do"]):
            return (
                "I can help you with:\n"
                " **Calendar** -- view events, find free slots, create meetings\n"
                " **Gmail** -- read, search, send emails\n"
                " **Drive** -- search and manage files\n"
                " **Photos** -- browse albums and recent photos\n"
                " **Reminders** -- set via calendar\n"
                " **Chat** -- general questions and conversation\n\n"
                "To enable Google integrations, connect your accounts via Composio."
            )

        return (
            "I'm running in offline mode. To unlock my full capabilities:\n"
            "1. Set GEMINI_API_KEY in .env for AI responses\n"
            "2. Set COMPOSIO_API_KEY in .env for Google integrations\n"
            "3. Restart me and connect your Google accounts\n\n"
            "You can still use me for basic conversation!"
        )

    # ── Screen Awareness ───────────────────────────────

    def analyze_screen(self, png_bytes: bytes) -> str:
        """
        One-shot proactive vision call: look at the screen frame and either
        produce ONE brief observation or NO_COMMENT. Never raises; returns
        "NO_COMMENT" on any failure. Called from the watcher thread.
        """
        if not self.gemini_client:
            return "NO_COMMENT"
        from google.genai import types
        try:
            response = _run_bounded(
                lambda: self.gemini_client.models.generate_content(
                    model=DEFAULT_GEMINI_MODEL,
                    contents=[types.Content(role="user", parts=[
                        types.Part(text=SCREEN_COMMENT_PROMPT),
                        types.Part(inline_data=types.Blob(
                            mime_type="image/png", data=png_bytes)),
                    ])],
                    # No tools: this is a pure observation call, one round only.
                    # NOTE: no thinking_config here -- thinking_budget=0 triggers
                    # a 400 INVALID_ARGUMENT on current Gemini flash models.
                    config=types.GenerateContentConfig(
                        temperature=0.4,
                        max_output_tokens=80,
                    ),
                ),
                GEMINI_CALL_CAP_SECONDS,
            )
            if response is None:
                logger.warning(" analyze_screen stalled >%.1fs -> NO_COMMENT",
                               GEMINI_CALL_CAP_SECONDS)
                return "NO_COMMENT"
            text = (response.text or "").strip()
            return text if text else "NO_COMMENT"
        except Exception as e:
            logger.warning(f" analyze_screen failed: {e}")
            return "NO_COMMENT"

    async def morning_briefing(self) -> str:
        """Generate a morning briefing with all available data."""
        calendar_data = ""
        email_data = ""
        drive_data = ""

        if self.composio_session:
            try:
                today = __import__("datetime").date.today().isoformat()
                calendar_result = await self.commands._handle_calendar(
                    self.composio_session, "get_today", {}
                )
                calendar_data = str(calendar_result.get("data", "No events"))

                email_result = await self.commands._handle_email(
                    self.composio_session, "get_unread", {"count": 5}
                )
                email_data = str(email_result.get("data", "No unread emails"))

                drive_result = await self.commands._handle_drive(
                    self.composio_session, "list_recent", {"count": 3}
                )
                drive_data = str(drive_result.get("data", "No recent files"))
            except Exception as e:
                logger.warning(f"Briefing data fetch failed: {e}")

        if self.gemini_client:
            from google.genai import types
            prompt = get_morning_briefing_prompt(calendar_data, email_data, drive_data)
            try:
                response = _run_bounded(
                    lambda: self.gemini_client.models.generate_content(
                        model=DEFAULT_GEMINI_MODEL,
                        contents=prompt,
                    ),
                    GEMINI_CALL_CAP_SECONDS,
                )
                if response is not None and response.text:
                    return response.text
            except Exception as e:
                logger.warning(f"Briefing generation failed: {e}")

        # Offline fallback
        return (
            " Good morning!\n\n"
            "I'm still getting set up. For a full morning briefing, "
            "please connect your Google accounts via Composio."
        )

    async def get_tools_info(self) -> dict:
        """Return information about available tools and connections."""
        info = {
            "gemini": bool(self.gemini_client),
            "composio": bool(self.composio_session),
            "tools_count": 0,
            "available_intents": self.commands.available_intents,
        }
        if self.composio_session:
            tools = self.composio_session.tools()
            info["tools_count"] = len(tools) if tools else 0
        return info

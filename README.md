# 🤖 Jarvis — Personal AI Assistant

A local, always-on AI assistant for Windows: talk (or type) naturally in English,
Egyptian Arabic, or Franco, and Jarvis controls your PC, plays your music, manages
Google services, and answers anything — with a morphing-orb PyQt6 HUD.

```bash
python -m jarvis
```

---

## How Jarvis thinks

### Dual-engine brain with a hard 5-second ceiling
- **Primary:** Gemini Flash (multi-model rotation: `gemini-flash-latest`,
  `gemini-3-flash-preview`, `gemini-3.1-flash-lite`) with native function calling.
- **Instant fallback chain:** Groq → NVIDIA NIM → OpenRouter → Cerebras → Mistral
  → GitHub Models → SambaNova. When Gemini stalls or rate-limits (429/503), the
  chain takes over — no hidden SDK retries, fresh time budgets per hand-off, so
  "offline mode" replies don't happen even when every primary model is down.
- Every LLM call is **bounded**: stalled calls are abandoned at their budget (not
  waited on), so a hung Google endpoint can never freeze the assistant.
- Fallback models get the **real tool schemas** (PC + memory + Composio), size-capped
  to fit Groq's free-tier 8k-token limit, with all `None`-valued schema fields
  stripped (Groq rejects `strict: null` with a 400).

### Think-before-act gate
Before any fast-path action touches your laptop (open / close / play / switch —
in English, Arabic, or Franco), a small AI check reviews the planned call:
- **Allow** — the action matches what you said → runs immediately
- **Correct** — mangled speech recognition gets fixed *before* executing
  ("open v s code" → VS Code, garbled song names → the real track)
- **Block** — the action clearly isn't what you asked → Jarvis says so instead of doing it

Fail-open by design: if no AI answers within the gate window (~2.5s cap), the
action proceeds — a dead network can never stop you opening apps.
Configure with `JARVIS_ACTION_GATE=0` (off) and `JARVIS_ACTION_GATE_TIMEOUT`.

### Deterministic fast path
Direct commands (`i.e open spotify`) skip the AI entirely:
regex-matched, gated, executed — the 5-second ceiling is a worst case, not a norm.

### Spotify that actually plays
The desktop Spotify client ignores synthetic keystrokes for playback, so Jarvis:
1. Opens `spotify:search:` for your query (Arabic or English)
2. Waits for the results list, finds the real **Play button** via Windows UI
   Automation (Spotify is relaunched with `--force-renderer-accessibility` if needed)
3. Clicks it and **verifies playback** by polling the window title — reporting
   honestly when a Spotify Free ad is in the way, instead of faking success

---

## How Jarvis listens

### 4-channel microphone array capture
Laptop mic arrays (e.g. 4-element Realtek) are captured **in full**: the input
channel count is detected from the default device, all channels are downmixed to
mono (mean), in both the live capture stream and the ambient-noise calibration —
no more quiet, muffled single-channel audio.

### Self-calibrating voice activity detection
- Ambient RMS is measured at startup; passive and attention gates are derived
  from it (multipliers in `jarvis/config.py`)
- The passive gate is **capped** (`MAX_PASSIVE_GATE`), so a noisy room can never
  calibrate a gate higher than a normal speaking voice
- 600 ms pre-roll buffer keeps the first syllable of "open" / "play" intact;
  1.4 s of silence ends an utterance (a quick breath doesn't cut you off)

### Always-listen mode
No wake word required. Every multi-word utterance reaches the brain — commands,
questions, small talk — with a chatter filter dropping filler ("yeah", "ايوه",
"yalla bye"), single words, and greetings-only. Self-hearing protection mutes the
mic while Jarvis speaks. Prefer the classic flow? Set `JARVIS_ALWAYS_LISTEN=0`
and use the wake word — **"hey Jarvis"** and Arabic variants (يا جارفيس…), fuzzy-matched
at a 0.65 ratio so mispronunciations still trigger, with direct commands always
bypassing the wake word.

### Dual-language speech recognition
Arabic (ar-EG) and English (en-US) are both tried on the same audio; the brain
receives both readings and uses whichever it understands.

---

## What Jarvis can do

**24 PC tools** — open/close/focus apps, run commands, hotkeys, window management,
screenshots, screen vision ("what's on my screen"), web search, system info, media
keys, volume, and more — plus dedicated automation modules:
- `tools/gui_automation.py` — PyAutoGUI desktop automation (FAILSAFE enabled)
- `tools/web_scraper.py` — BeautifulSoup4 static scraping
- `tools/browser_automation.py` — Selenium dynamic browser control (lazy init, clean teardown)

**Google services via Composio** — Gmail, Google Calendar, Discord and more
through the Composio tool router. The live connection status is injected into
every fallback prompt, so models report your *real* auth links instead of
hallucinating connection flows.

**Long-term memory** — 4 memory tools (store/recall/forget/context) persisted in SQLite.

**Proactive layer** — scheduler with morning briefings, screen watcher that
notices meaningful screen changes, reminders and alerts.

**HUD** — PyQt6 morphing orb, chat panel, live waveform, hands-free toggle,
provider/fallback visibility, wake-listen state rings.

---

## Setup

### 1. Install
```bash
pip install -r jarvis/requirements.txt
```

### 2. API keys
Copy the template and fill in your keys:
```bash
cp jarvis/.env.example jarvis/.env
```
| Key | Required | For |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Primary brain ([Google AI Studio](https://aistudio.google.com/apikey)) |
| `GROQ_API_KEY` | recommended | Fastest fallback ([console.groq.com](https://console.groq.com)) |
| `NVIDIA_API_KEY` | optional | Vision-capable fallback models |
| `COMPOSIO_API_KEY` | for Google tools | Gmail/Calendar/Discord ([dashboard.composio.dev](https://dashboard.composio.dev)) |

### 3. Run
```bash
python -m jarvis          # GUI + voice (default)
python -m jarvis --cli    # text-only
python -m jarvis --debug  # verbose logging
```

First Google-tools use generates a one-time Composio auth link — open it once;
connections persist (Google tokens in testing mode may need re-auth after ~7 days).

### 4. Build the exe (optional)
```bash
pip install pyinstaller
pyinstaller Jarvis.spec
```
`Jarvis.spec` whitelist-packages the project (never sweeping stale artifacts),
bundles the automation modules via `hiddenimports`, and excludes heavy unused deps.

---

## Configuration highlights (`jarvis/config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `WAKE_MATCH_THRESHOLD` | 0.65 | fuzzy wake-phrase ratio (lower = more forgiving) |
| `WAKE_AMBIENT_MULTIPLIER` | 1.8 | passive gate = ambient × this |
| `ACTIVE_AMBIENT_MULTIPLIER` | 1.3 | attention gate = ambient × this |
| `MAX_PASSIVE_GATE` | 0.030 | passive gate ceiling |
| `SILENCE_FRAMES_TO_STOP` | 45 | ~1.4 s silence ends an utterance |
| `JARVIS_ALWAYS_LISTEN` | 1 | no wake word needed |
| `JARVIS_ACTION_GATE` | 1 | AI pre-check before PC actions |

## Project structure

```
├── Jarvis.spec              # PyInstaller packaging (root)
├── tests/                   # wake-word + automation suites
└── jarvis/
    ├── __main__.py          # module runner
    ├── main.py              # entry point
    ├── config.py            # all knobs
    ├── voice_loop.py        # capture, VAD, wake word, always-listen
    ├── database.py          # SQLite (conversations, commands, memory)
    ├── memory.py            # long-term memory
    ├── scheduler.py         # briefings, reminders
    ├── screen_watcher.py    # screen-change observer
    ├── autostart.py         # run at Windows startup
    ├── brain/
    │   ├── __init__.py      # orchestrator: gate, fast path, tool loop, fallback chain
    │   ├── prompts.py       # system prompts
    │   ├── commands.py      # fast-path command grammar (EN/AR)
    │   └── screen_prompt.py # screen-vision prompting
    ├── tools/
    │   ├── pc_control.py    # 24 PC tools + Spotify UIA playback
    │   ├── gui_automation.py    # PyAutoGUI
    │   ├── web_scraper.py       # BeautifulSoup4
    │   └── browser_automation.py # Selenium
    ├── ui/                  # orb, main window, chat panel, TTS, waveform…
    └── data/                # runtime db + logs (gitignored)
```

## License

MIT

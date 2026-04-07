<p align="center">
  <img src="assets/avatar.png" alt="PMC Overwatch" width="160" />
</p>

<h1 align="center">PMC Overwatch</h1>

<p align="center">
  <strong>AI-powered animated stream companion for Escape from Tarkov</strong><br/>
  3D Mascot with Personality • Dual LLM + Gemini Vision • Neural Voice Pipeline • Twitch Chat<br/>
  <sub>v0.30.0 &mdash; In Development</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey?logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/LLM-Groq%20%2B%20Ollama-FF6B35" alt="LLM" />
  <img src="https://img.shields.io/badge/Vision-Gemini%202.0%20Flash-4285F4" alt="Vision" />
  <img src="https://img.shields.io/badge/TTS-edge--tts%20%2B%20Kokoro-22c55e" alt="TTS" />
  <img src="https://img.shields.io/badge/STT-Whisper%20%2B%20Groq-ef4444" alt="STT" />
  <img src="https://img.shields.io/badge/anti--cheat-safe-brightgreen" alt="Anti-Cheat Safe" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

---

## What Is This?

PMC Overwatch is an **animated AI companion** that lives on your screen while you stream Escape from Tarkov on Twitch. It sees your gameplay, reacts to what's happening, talks back in real-time, and interacts with your chat — all while being completely **anti-cheat safe**.

Think of it as a virtual co-host with its own personality, brain, and body.

**What it does:**
- **Sees your screen** via Gemini Vision and reacts to combat, looting, deaths, and extracts
- **Talks back** with Microsoft Neural Voices in real-time (sentence-by-sentence streaming)
- **Moves around** your screen autonomously — walks, ducks during combat, celebrates kills
- **Has personality modes** — switch between Hype Man, Tactical Advisor, and Comedy mode
- **Roasts your deaths** — escalating reactions from encouraging to full roast mode
- **Responds to Twitch chat** — viewers can make it dance, move, and ask it questions
- **Knows Tarkov** — quests, ammo tables, maps, bosses, flea market prices

**Anti-cheat safe:** Screen capture uses DXGI (the same API OBS uses). No game memory reading, no packet inspection, no overlays injected into the game process. Fully BattlEye compliant.

---

## Architecture

```
                          ┌──────────────────────────┐
                          │   OBS Browser Source      │
                          │   mascot_3d.html          │
                          │   Three.js + FBX + GLB    │
                          │   Idle Animations + Danger│
                          └────────────▲─────────────┘
                                       │ WebSocket (emotion, animation,
                                       │  danger, macro, personality)
┌──────────────────────────────────────┼────────────────────────────────┐
│                              main.py │ (Headless Engine)              │
│                                      │                                │
│  ┌─────────────┐   ┌────────────┐   ┌▼───────────────┐              │
│  │ voice_input  │──▶│   brain    │──▶│ mascot_server  │              │
│  │              │   │            │   │ FastAPI+WS:8420│              │
│  │ Silero VAD   │   │ Groq ─────┤   └────────────────┘              │
│  │ Whisper STT  │   │ Ollama ───┘                                    │
│  │ Noise Reduce │   │               ┌────────────────┐              │
│  └─────────────┘   │ Personality   │ voice_output    │              │
│                     │ Death Roasts  │ edge-tts (pri)  │              │
│                     │ Danger Aware  │ Kokoro  (bkup)  │              │
│                     │ Stream Recap  │ Lip-sync amp    │              │
│                     └────────────┘  └────────────────┘              │
│                                                                      │
│  ┌──────────────┐   ┌─────────────┐  ┌──────────────┐              │
│  │ video_capture │   │ expression  │  │ sound_effects│              │
│  │ DXGI Capture │   │ _engine     │  │ Kill chime   │              │
│  │ Gemini Vision│   │ 12 emotions │  │ Death buzzer │              │
│  │ (cache/20s)  │   │ DangerLevel │  │ Loot sparkle │              │
│  └──────────────┘   └─────────────┘  │ Extract fanf.│              │
│                                       └──────────────┘              │
│  ┌──────────────┐   ┌─────────────┐                                  │
│  │ twitch_bot   │   │ tarkov_data │                                  │
│  │ 16 commands  │   │ + updater   │                                  │
│  │ Cooldowns    │   │ GraphQL API │                                  │
│  └──────────────┘   └─────────────┘                                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Features

### Voice & Audio Pipeline
| Feature | Technology | Description |
|---------|-----------|-------------|
| **Speech Detection** | Silero VAD (neural) | >99% accuracy, rejects keyboard/mouse noise. RMS fallback if unavailable |
| **Speech-to-Text** | Groq cloud + faster-whisper | Groq `whisper-large-v3-turbo` primary, local `faster-whisper` fallback |
| **Text-to-Speech** | edge-tts + Kokoro ONNX | Microsoft Ava Neural (primary), Kokoro 82M offline backup |
| **Barge-In** | Interrupt detection | Speak over the AI — it stops, captures your audio, and responds |
| **Noise Reduction** | noisereduce (spectral) | Strips background noise before transcription |
| **Language Detection** | Auto + per-sentence | English, Russian, Romanian — locks language per response |

### AI Brain (Dual-Engine LLM + Vision)
| Engine | Role | Model | Notes |
|--------|------|-------|-------|
| **Groq Cloud** | Text (primary) | llama-3.3-70b-versatile | 250+ tok/s, auto-fallback to 8b-instant on rate limit |
| **Ollama Local** | Text (fallback) | qwen2.5:3b | Offline emergency backup, auto-starts with app |
| **Gemini** | Vision only | gemini-2.0-flash | Screen analysis every 20s, cached context injected into all prompts |

Rate limits are tracked with cooldown timers. When Groq hits its limit, the system fails over to Ollama and auto-restores when the cooldown expires. Gemini is reserved exclusively for screen vision analysis (1,500 req/day free tier = 8 hours of streaming).

### Personality System
Three switchable personality modes that change how the AI speaks and reacts:

| Mode | Style |
|------|-------|
| **Hype** | High-energy play-by-play. Screams about kills, gasps at close calls, maximum chat engagement |
| **Tactical** | Serious military advisor. Callouts, warnings, strategic analysis |
| **Comedy** | Sarcastic observer. Roasts gameplay, makes jokes, self-deprecating humor |

Switch via Twitch chat (`!personality hype/tactical/comedy`) or the web dashboard.

### Death Roast System
The AI tracks deaths per stream and escalates its reactions:

| Deaths | Tier | Reaction Style |
|--------|------|---------------|
| 1 | Supportive | "Shake it off, you got this" |
| 3 | Concerned | "Okay maybe try a different approach?" |
| 5 | Blunt | "That's becoming a pattern" |
| 7 | Roasting | "At this point the scavs feel bad" |
| 10 | Brutal | Full roast, no filter |
| 15+ | Legendary | "Historic performance, truly unprecedented" |

### Danger Awareness
Vision analysis assigns a danger level (NONE / LOW / MEDIUM / HIGH) to each screen capture:

- **LOW** — inventory management, quiet areas. Mascot is calm, occasional tips
- **MEDIUM** — nearby movement, distant gunfire. Mascot gets alert, warns about threats
- **HIGH** — active combat, grenades, bosses. Mascot ducks, urgent callouts, combat SFX

The mascot's glow color changes with danger level, and combat particle effects appear during HIGH danger.

### Sound Effects
Contextual SFX triggered by game events detected through vision analysis:

| Event | Sound | Cooldown |
|-------|-------|----------|
| **Kill confirmed** | Ascending triple chime (E5-G5-C6) | 2s |
| **Death** | Descending three-note buzzer (G4-D4-G3) | 3s |
| **Loot found** | Shimmering two-note sparkle (D6-F6) | 2s |
| **Extract success** | Triumphant four-note fanfare (C5-E5-G5-C6) | 5s |

### 3D Mascot (OBS Overlay)
| Feature | Description |
|---------|-------------|
| **Character** | Custom FBX model with Altyn helmet + gold RPK weapon |
| **Animations** | 12 Mixamo animations: idle, walk, crouch, wave, clap, think, shrug, dance, salute, die, win |
| **AI-Driven Gestures** | LLM uses `[gesture:NAME]` tags to trigger animations contextually |
| **Idle Micro-Animations** | Breathing bob, weight shifting, random fidgets (weapon adjust, foot tap) |
| **Danger Reactions** | Glow color shifts (green→yellow→orange→red), combat particles at HIGH danger |
| **Animation Macros** | Chain sequences: celebrate = dance→clap→wave. Custom via `!macro` |
| **Autonomous Movement** | Walks every 12-20s + screen-driven movement (moves toward action) |
| **Voice-Reactive** | Green glow aura, amplitude-driven effects |
| **Debug Panel** | Press **D** for live RPK position/rotation/scale sliders |

### Twitch Integration
| Command | Description |
|---------|-------------|
| `!hello` | Greeting |
| `!move <dir>` | Move mascot (left/right/center/random) |
| `!dance` | Trigger dance animation |
| `!wave` | Trigger wave animation |
| `!clap` | Trigger clap animation |
| `!think` | Trigger thinking pose |
| `!shrug` | Trigger shrug animation |
| `!salute` | Trigger salute animation |
| `!crouch` | Trigger crouch animation |
| `!die` | Dramatic death animation |
| `!win` | Victory pose |
| `!ask <text>` | Ask the AI a question directly |
| `!status` | Show AI engine status + uptime |
| `!personality <mode>` | Switch personality (hype/tactical/comedy) |
| `!deaths` | Show death/kill count and K/D ratio |
| `!celebrate` | Trigger celebration macro (dance→clap→wave) |
| `!macro <list>` | Custom animation sequence (e.g., `!macro dance,clap,wave`) |

All commands have a 10-second per-user cooldown. Macros are capped at 5 animations.

### Web Dashboard
Control panel at `http://localhost:8420` with:
- Real-time AI status, engine info, uptime
- Personality mode selector (Hype / Tactical / Comedy)
- Live stream stats (kills, deaths, K/D ratio, danger level)
- Quick macro buttons (Celebrate, Confused, Tactical, Dramatic)
- Emotion and animation triggers
- Mascot movement controls

### Stream Recap
On shutdown, the AI generates a summary of the stream session including kills, deaths, K/D ratio, duration, and session highlights. Can be used as a clip-worthy sendoff.

---

## Quick Start

### Prerequisites

| Requirement | Required? | Notes |
|-------------|-----------|-------|
| **Python 3.10+** | Yes | [python.org/downloads](https://www.python.org/downloads/) |
| **Microphone** | Yes | Any USB or built-in mic |
| **Groq API Key** | Recommended | Free at [console.groq.com](https://console.groq.com/keys) |
| **Gemini API Key** | Recommended | Free at [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| **Ollama** | Optional | Auto-installs locally — [ollama.com](https://ollama.com) |

### Installation

```bash
# Clone
git clone https://github.com/Bossiq/Tarkov_AI_Frriend.git
cd Tarkov_AI_Frriend

# Virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env → add your GROQ_API_KEY and GEMINI_API_KEY

# Run
python main.py
```

The mascot overlay is served at **http://127.0.0.1:8420/mascot3d** — add this as an OBS Browser Source (1920x1080, transparent background).

---

## Configuration

All settings live in `.env` (copy from `.env.example`):

<details>
<summary><strong>Click to expand full configuration table</strong></summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq cloud API key (primary LLM + STT) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Primary LLM model |
| `GEMINI_API_KEY` | — | Google Gemini API key (vision only) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Local fallback model (auto-downloaded) |
| `OLLAMA_NUM_CTX` | `2048` | Ollama context window |
| `WHISPER_MODEL` | `small` | Local Whisper size: `tiny`, `base`, `small`, `medium` |
| `WHISPER_LANGUAGE` | `auto` | Force language: `auto`, `en`, `ro`, `ru` |
| `TTS_VOICE` | `af_heart` | Kokoro TTS voice (fallback only) |
| `TTS_SPEED` | `1.05` | TTS playback speed |
| `EDGE_RATE` | `+10%` | edge-tts speed adjustment |
| `INPUT_MODE` | `auto` | Mic mode: `auto` (VAD), `toggle`, `push` |
| `TWITCH_TOKEN` | — | Twitch OAuth token (optional) |
| `TWITCH_INITIAL_CHANNELS` | — | Twitch channel to join |
| `LOG_LEVEL` | `INFO` | Logging: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

</details>

---

## Project Structure

```
Tarkov_AI_Frriend/
├── main.py                  # Entry point — orchestrator, screen-driven reactions
├── brain.py                 # Dual-engine LLM (Groq → Ollama), personality, death roasts
├── voice_input.py           # Silero VAD + Whisper STT + noise reduction
├── voice_output.py          # edge-tts + Kokoro TTS + lip-sync amplitude
├── mascot_server.py         # FastAPI + WebSocket, dashboard, danger/macro broadcast
├── expression_engine.py     # 12-emotion state machine + DangerLevel assessment
├── sound_effects.py         # Contextual SFX: kill, death, loot, extract + tactical
├── video_capture.py         # DXGI screen capture + Gemini Vision integration
├── tarkov_data.py           # Tarkov knowledge base (quests, ammo, maps, bosses)
├── tarkov_updater.py        # Live data from tarkov.dev GraphQL API
├── twitch_bot.py            # TwitchIO bot — 16 commands with cooldowns
├── logging_config.py        # Rotating file + console logging
├── requirements.txt         # Python dependencies
├── .env.example             # Configuration template
├── assets/
│   ├── mascot_3d.html       # 3D mascot overlay (Three.js + idle anims + danger)
│   ├── mascot.html          # 2D sprite fallback overlay
│   ├── dashboard_ui.html    # Web dashboard (personality, stats, macros)
│   ├── sprites/             # 2D emotion sprites (8 PNGs)
│   └── animations/          # Mixamo FBX animation files (12 FBX)
├── models/
│   ├── altyn_boss.fbx       # 3D character model (23MB)
│   ├── rpk_gold.glb         # Gold RPK weapon model (8MB)
│   ├── kokoro-v1.0.onnx     # Kokoro TTS model (offline, 325MB)
│   └── voices-v1.0.bin      # Kokoro voice embeddings (27MB)
└── tests/
    ├── test_stress.py       # Stress + integration tests
    └── test_units.py        # Unit tests (54 tests)
```

---

## Technical Highlights

<details>
<summary><strong>For engineers and recruiters — click to expand</strong></summary>

### Concurrency Architecture
- **6 concurrent threads**: main loop, mic listener, screen analysis, Twitch bot, mascot server, Whisper model loading
- **Thread-safe guards**: `_processing_lock` prevents parallel LLM calls, `_toggle_lock` prevents duplicate listen threads
- **Shared interrupt event**: coordinates barge-in between VoiceInput and VoiceOutput across threads
- **Lock-protected memory**: deque-based conversation history with `_memory_lock` for compound operations

### Voice Pipeline Engineering
- **Callback-based audio capture** (not blocking `stream.read()`) — prevents indefinite hangs when mic hardware stalls after TTS
- **Pre-buffer**: 10 chunks (1s) of audio saved before speech onset — captures the first syllable that would be lost
- **Neural VAD to RMS fallback**: Silero VAD runs ~1ms per 512-sample window; falls back to RMS + spectral flatness if torch unavailable
- **Sentence-by-sentence streaming**: TTS speaks each sentence as it arrives from the LLM — no waiting for full response

### LLM Failover System
- **Rate limit parsing**: extracts cooldown duration from API error messages, sets per-engine timers
- **Automatic restore**: background timers restore higher-priority engines after cooldown expires
- **Context injection**: quest/ammo/map data injected only when keyword-triggered (saves tokens)
- **Memory compression**: when conversation exceeds 8 messages, oldest half is summarized to a single message

### Vision + Danger Pipeline
- **Confidence scoring**: filters out menu screens, loading screens, and lobby states to prevent false positives
- **Danger assessment**: regex keyword matching classifies screen state into 4 danger levels
- **Temperature adjustment**: LLM temperature scales with danger level (0.7 calm to 0.9 combat)
- **Screen-driven movement**: mascot navigates toward detected action areas

### Audio Quality
- **Dynamic range compression** (soft-knee, 3:1 ratio above 0.7 threshold)
- **Anti-click fading** (50ms cosine ramps)
- **Trailing artifact stripping** (kills edge-tts MP3 decoder beeps)
- **Post-TTS cooldown** (0.3s) prevents mic from capturing TTS tail

</details>

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **No microphone detected** | Grant mic permission in System Settings. Check `python -c "import sounddevice; print(sounddevice.query_devices())"` |
| **Groq rate limit** | Normal on free tier (30 RPM). Auto-falls back to Ollama. Wait ~60s. |
| **First run slow** | Whisper model downloads on first use (~500MB for `small`). Cached after that. |
| **macOS screen capture** | Grant Screen Recording permission in System Settings |
| **`pip install` fails (Windows)** | Install [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) first |

---

## Status

This project is in **active development**. Core systems are production-ready and deployed for personal streaming use. New features and refinements are being added continuously.

---

## Acknowledgments

- [Groq](https://groq.com) — Ultra-fast cloud LLM inference
- [Google Gemini](https://ai.google.dev/) — Multimodal AI with vision
- [Ollama](https://ollama.com) — Local LLM inference
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper implementation
- [edge-tts](https://github.com/rany2/edge-tts) — Microsoft neural TTS voices
- [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx) — Local ONNX neural TTS
- [Silero VAD](https://github.com/snakers4/silero-vad) — Neural voice activity detection
- [Three.js](https://threejs.org/) — 3D graphics engine
- [Mixamo](https://www.mixamo.com/) — Motion-captured animations
- [Escape from Tarkov](https://www.escapefromtarkov.com/) — Battlestate Games

## License

MIT License — see [LICENSE](LICENSE) for details.

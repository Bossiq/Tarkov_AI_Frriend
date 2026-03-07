<p align="center">
  <img src="assets/avatar.png" alt="PMC Overwatch" width="160" />
</p>

<h1 align="center">PMC Overwatch — Tarkov AI Companion</h1>

<p align="center">
  <strong>Real-time voice AI companion for Escape from Tarkov</strong><br/>
  3D VRM Avatar • Mixamo Animations • Neural Voice • Groq/Ollama LLM
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey?logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/LLM-Groq%20%2B%20Ollama-orange" alt="LLM" />
  <img src="https://img.shields.io/badge/TTS-edge--tts%20Ava-green" alt="TTS" />
  <img src="https://img.shields.io/badge/STT-faster--whisper-red" alt="STT" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎤 **Voice Input** | Real-time speech recognition via faster-whisper (GPU, multilingual) |
| 🧠 **Dual LLM** | Groq cloud (250+ tok/s, free) + Ollama local with **auto-failover** |
| 🔊 **Neural TTS** | Microsoft edge-tts with per-sentence language detection |
| 🎭 **Live Procedural Avatar** | Fully code-drawn character — face, eyes, mouth, hair rendered from scratch each frame |
| 🎵 **Lip Sync** | RMS amplitude → mouth region blend (20ms resolution) |
| 👁️ **Eye Animation** | Multi-stage blinks with independent eye region compositing |
| 😊 **Emotion Detection** | Keyword sentiment → expression overlay changes |
| 🎤 **Push-to-Talk** | Three input modes: Auto VAD, Toggle (F4), Hold (F4) |
| 🌐 **Multilingual** | English, Russian, Romanian — auto-detect speech + per-language voice selection |
| 🔇 **Barge-In** | Interrupt the AI mid-speech — she stops and listens |
| 🛡️ **Anti-Hallucination** | Spectral flatness filter + Whisper hallucination rejection (rejects squeaks, clicks, false "thank you") |
| 📺 **Twitch Bot** | Optional chat integration for stream interactions |
| 🎮 **Tarkov Knowledge** | Built-in quest reference database for accurate game info |
| 🎬 **OBS Overlay** | Transparent window mode (Ctrl+O) — use as streaming overlay |
| 🛡️ **Persona Editor** | Edit AI personality and system prompt (Ctrl+P) |
| 💬 **Chat History** | Auto-saves session logs for review |
| 🔄 **Rate Limit Fallback** | Auto-switches to fast backup model with cooldown cache when rate-limited |
| 🌍 **Language Selector** | UI dropdown to switch language on-the-fly |
| ⚡ **Auto-Failover** | Groq rate-limit → instant Ollama switch → auto switch-back |
| 🔇 **Smart Barge-In** | Interrupt the AI mid-speech (keyboard/laughter ignored, real speech detected) |
| 🎭 **3D VRM Avatar** | Full 3D anime character with motion-captured animations (Three.js + VRM) |
| 💃 **16 Animations** | Wave, clap, dance, think, bow, salute, celebrate, and more — AI-triggered |
| 🔉 **Noise Reduction** | Spectral noise gating removes background sounds before transcription |

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────┐
│                PMC Overwatch GUI              │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │  Sprite  │  │ Activity │  │  Controls   │ │
│  │  Avatar  │  │   Log    │  │ Start/Stop  │ │
│  │  + Holo  │  │          │  │             │ │
│  └─────┬────┘  └────┬─────┘  └──────┬─────┘ │
└────────┼────────────┼────────────────┼───────┘
         │            │                │
    ┌────▼────────────▼────────────────▼───┐
    │             Main Controller          │
    │          (PMCOverwatch)              │
    └──┬──────────┬───────────┬────────────┘
       │          │           │
  ┌────▼──┐  ┌───▼────┐  ┌──▼───────┐
  │Voice  │  │ Brain  │  │  Voice   │
  │Input  │  │Groq/   │  │  Output  │
  │Whisper│  │Ollama  │  │ edge-tts │
  └───────┘  └───┬────┘  └──────────┘
                 │
          ┌──────▼──────┐
          │ Expression  │
          │   Engine    │
          └─────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Required? | Notes |
|-------------|-----------|-------|
| **Python 3.10+** | ✅ Yes | [python.org/downloads](https://www.python.org/downloads/) |
| **Microphone** | ✅ Yes | Any USB/built-in mic works |
| **Groq API Key** | 🟡 Recommended | Free at [console.groq.com/keys](https://console.groq.com/keys) — 250+ tok/s |
| **Ollama** | 🔵 Optional | Local fallback — [ollama.com](https://ollama.com) |
| **CUDA GPU** | 🔵 Optional | Speeds up Whisper transcription |

### Step 1 — Clone the repo

```bash
git clone https://github.com/Bossiq/Tarkov_AI_Frriend.git
cd Tarkov_AI_Frriend
```

### Step 2 — Create a virtual environment

```bash
# Create venv
python -m venv venv

# Activate it
# macOS / Linux:
source venv/bin/activate
# Windows (CMD):
venv\Scripts\activate
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

> **Windows note:** If `sounddevice` fails to install, you may need to install [Microsoft Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).

### Step 4 — Configure environment

```bash
# Copy the example config
cp .env.example .env        # macOS / Linux
copy .env.example .env      # Windows
```

Open `.env` in any text editor and add your **Groq API key**:

```ini
GROQ_API_KEY=gsk_your_key_here
```

That's the only required change. Everything else has sensible defaults.

### Step 5 — Run the app

```bash
python main.py
```

The GUI will launch. Click **Start** to begin listening.

---

## ⚙️ Configuration

All settings are in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq cloud API key (primary, fastest) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama fallback model |
| `OLLAMA_NUM_CTX` | `2048` | Context window size |
| `WHISPER_MODEL` | `small` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `WHISPER_COMPUTE_TYPE` | `float16` | Compute type (`float16`, `int8`, `float32`) |
| `WHISPER_LANGUAGE` | `auto` | Force language (`auto`, `en`, `ro`, `ru`) |
| `EDGE_RATE` | `+0%` | Speech speed adjustment |
| `INPUT_MODE` | `auto` | Input mode (`auto`, `toggle`, `push`) |
| `PTT_KEY` | `f4` | Push-to-talk hotkey |
| `TTS_VOICE` | `af_heart` | Kokoro fallback voice |
| `TTS_SPEED` | `1.1` | Kokoro speech speed |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `TWITCH_TOKEN` | — | Twitch OAuth token (optional) |
| `TWITCH_INITIAL_CHANNELS` | — | Twitch channel name (optional) |
| `AVATAR_3D` | `true` | Enable 3D VRM avatar (set to `false` for sprite-only mode) |

### LLM Engine Selection

| Config | Speed | Quality | Requirement |
|--------|-------|---------|-------------|
| Groq llama-3.3-70b (default) | 276 tok/s | Excellent (70B model) | Free API key |
| Groq llama-3.1-8b-instant (auto-fallback) | 877 tok/s | Good | Free API key |
| Ollama qwen2.5:3b | 20-60 tok/s | Good | Local GPU |
| Ollama qwen2.5:7b | 10-30 tok/s | Better | Good GPU |

> **Rate limiting**: When the primary Groq model is rate-limited (free tier: 30 RPM, 100K tokens/day),
> the app automatically falls back to `llama-3.1-8b-instant` with a cooldown cache to avoid cascading failures.

---

## 📁 Project Structure

```
Tarkov_AI_Frriend/
├── main.py              # Application entry point & controller
├── brain.py             # Dual LLM brain (Groq + Ollama) with rate-limit fallback
├── gui.py               # Sprite-composited holographic avatar GUI
├── voice_input.py       # Speech recognition (faster-whisper + spectral filtering)
├── voice_output.py      # Text-to-speech (edge-tts Ava Multilingual + Kokoro fallback)
├── expression_engine.py # Emotion state machine → sprite selection
├── tarkov_data.py       # Tarkov quest knowledge base
├── twitch_bot.py        # Twitch chat integration
├── video_capture.py     # Screen capture module
├── logging_config.py    # Logging configuration
├── avatar_3d.py         # 3D VRM avatar (Three.js, AnimationMixer, pywebview)
├── download_animations.py # Helper to download Mixamo FBX animations
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template (copy to .env)
├── CHANGELOG.md         # Version history
├── LICENSE              # MIT License
├── assets/              # Avatar assets
│   ├── avatar_3d.html   # 3D VRM renderer (Three.js + embedded animations)
│   ├── animations/      # Optional Mixamo FBX files (upgrade path)
│   ├── idle.png         # Sprite: default idle expression
│   ├── blink.png        # Sprite: blink frame
│   └── ...              # Other sprite expressions
```

## 🎭 Avatar System

The app supports two avatar modes:

### 3D VRM Avatar (default, `AVATAR_3D=true`)

- **VRM model** loaded via Three.js + `@pixiv/three-vrm`
- **16 embedded animations**: idle, wave, clap, think, point, shrug, celebrate, salute, nod, headShake, bow, crossArms, facepalm, dance, laugh, thumbsUp
- **Smooth keyframe interpolation** using `THREE.InterpolateSmooth` (catmull-rom curves)
- **AnimationMixer** with crossfade blending between animations
- **AI-driven gestures**: The LLM uses `[gesture:NAME]` tags to trigger animations
- **Lip sync**: RMS amplitude → mouth blend shapes
- **Emotions**: Smooth blend shape interpolation (happy, angry, sad, surprised)
- **Autonomous behavior**: Random blinks, gaze shifts, idle fidgets
- **Upgrade path**: Drop Mixamo FBX files in `assets/animations/` for motion-captured quality

### Sprite Avatar (`AVATAR_3D=false`)

- 24 expression sprites with cross-fade transitions
- Holographic post-processing (scanlines, glow, flicker)
- Organic motion (Perlin-noise sway, breathing, blinks)

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| **No microphone detected** | Check `sounddevice.query_devices()` — ensure your mic is listed. On macOS, grant Terminal/IDE microphone permission in System Preferences → Privacy. |
| **Groq rate limit errors** | Normal on free tier. The app auto-falls back to `llama-3.1-8b-instant`. Wait ~60s for cooldown. |
| **Whisper model download slow** | First run downloads the model (~500MB for `small`). Subsequent runs use the cached model. |
| **`pip install` fails on Windows** | Install [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/). Run `pip install --upgrade pip` first. |
| **No sound output** | Ensure your system default audio output device is working. The app plays audio through the default device. |
| **Romanian/Russian not detected** | Set `WHISPER_LANGUAGE=ro` or `WHISPER_LANGUAGE=ru` in `.env` to force the language instead of auto-detect. |

---

## 🛠 Development

### Testing

```bash
# Test all imports work
python -c "from voice_output import VoiceOutput; from voice_input import VoiceInput; from brain import Brain; from expression_engine import ExpressionEngine; print('All imports OK')"

# Test GUI launches
python -c "from gui import OverwatchGUI; app = OverwatchGUI(); app.after(3000, app._on_close); app.mainloop()"
```

### Key dependencies

- `groq` — Groq cloud LLM API client
- `ollama` — Local LLM client (fallback)
- `customtkinter` — Modern dark-mode GUI framework
- `faster-whisper` — GPU-accelerated speech recognition (CTranslate2)
- `edge-tts` — Microsoft neural TTS voices
- `sounddevice` / `soundfile` — Audio I/O
- `Pillow` — Image processing for avatar compositing
- `pynput` — Global keyboard listener for push-to-talk
- `twitchio` — Twitch bot framework
- `kokoro-onnx` — Fallback TTS engine

---

## 📝 Contributing

This is a personal project. Feel free to fork and adapt for your own use.

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🚀 Future Roadmap

Planned improvements to push quality, speed, and intelligence:

### AI & LLM
| Improvement | Impact | Description |
|---|---|---|
| **Google Gemini API** | ⚡ More free tokens | 1,500 req/day free tier — 3rd engine option |
| **Context compression** | 🧠 Smarter memory | Summarize old messages instead of dropping them |
| **RAG with Tarkov wiki** | 🎯 Better accuracy | Live item/map/quest data instead of static reference |
| **Response quality scoring** | 📊 Self-improvement | Score responses and tune prompts based on feedback |
| **Streaming function calls** | 🔧 Actions | Let the AI trigger in-game overlays, timers, etc. |

### Voice & Audio
| Improvement | Impact | Description |
|---|---|---|
| **Voice cloning** | 🎭 Custom personality | Clone a specific voice for the AI character |
| **Whisper large-v3** | 🎤 Better STT | More accurate transcription (needs GPU) |
| **Faster TTS (Kokoro v2)** | ⚡ Lower latency | Sub-200ms first-byte TTS |
| **Emotion-aware TTS** | 😊 Natural speech | Adjust TTS pitch/rate based on detected emotion |
| **Noise cancellation** | 🔇 Cleaner input | RNNoise or similar for keyboard/background filtering |

### Avatar & UI
| Improvement | Impact | Description |
|---|---|---|
| **Live2D avatar** | 🎭 Premium look | Replace sprite compositing with Live2D rigging |
| **Emotion-driven expressions** | 😊 Richer reactions | More expression states (angry, surprised, sad) |
| **Webcam face tracking** | 👁️ Mirror user | Map user expressions to avatar |
| **3D avatar (Three.js)** | 🌟 Next-gen | Full 3D model with physics-based animation |
| **Custom avatar builder** | 🎨 User choice | Let users pick/design their own AI companion |

## 🙏 Acknowledgments

- [Groq](https://groq.com) — Ultra-fast cloud LLM inference
- [Ollama](https://ollama.com) — Local LLM inference
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper
- [edge-tts](https://github.com/rany2/edge-tts) — Microsoft neural voices
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — Modern Tkinter
- [Escape from Tarkov](https://www.escapefromtarkov.com/) — Battlestate Games

<p align="center">
  <img src="assets/avatar.png" alt="SCAV-E" width="160" />
</p>

<h1 align="center">PMC Overwatch — Tarkov AI Companion</h1>

<p align="center">
  <strong>Real-time voice AI companion for Escape from Tarkov</strong><br/>
  3D animated avatar • Multilingual speech • Groq/Ollama LLM • Twitch integration
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey?logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/LLM-Groq%20%2B%20Ollama-orange" alt="LLM" />
  <img src="https://img.shields.io/badge/TTS-edge--tts%20Neural-green" alt="TTS" />
  <img src="https://img.shields.io/badge/STT-faster--whisper-red" alt="STT" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎤 **Voice Input** | Real-time speech recognition via faster-whisper (GPU accelerated) |
| 🧠 **Dual LLM** | Groq cloud (250+ tok/s, free) or Ollama local — auto-selects |
| 🔊 **Neural TTS** | Microsoft edge-tts with AriaNeural (EN), DariyaNeural (RU), AlinaNeural (RO) |
| 🎭 **3D Avatar** | Region-composited animation: independent mouth, eye, and expression blending |
| 🌐 **Multilingual** | English, Russian, Romanian — auto-detection or manual override |
| 📺 **Twitch Bot** | Optional chat integration for stream interactions |
| 🎮 **Tarkov Knowledge** | Built-in quest reference database for accurate game info |

## 🏗 Architecture

```
┌──────────────────────────────────────────────┐
│                PMC Overwatch GUI              │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ 3D Avatar│  │ Activity │  │  Controls   │ │
│  │ Region   │  │   Log    │  │ Start/Stop  │ │
│  │Composited│  │          │  │             │ │
│  └─────┬────┘  └────┬─────┘  └──────┬─────┘ │
└────────┼────────────┼────────────────┼───────┘
         │            │                │
    ┌────▼────────────▼────────────────▼───┐
    │              Main Controller          │
    │          (SCAVESystem)                │
    └──┬──────────┬───────────┬────────────┘
       │          │           │
  ┌────▼──┐  ┌───▼────┐  ┌──▼───────┐
  │Voice  │  │ Brain  │  │  Voice   │
  │Input  │  │Groq/   │  │  Output  │
  │Whisper│  │Ollama  │  │ edge-tts │
  └───────┘  └────────┘  └──────────┘
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Groq API key** (recommended) — [console.groq.com/keys](https://console.groq.com/keys)
- **Ollama** (optional fallback) — [ollama.com](https://ollama.com)
- **CUDA** (optional) — For GPU-accelerated Whisper

### Installation

```bash
# Clone the repository
git clone https://github.com/Bossiq/Tarkov_AI_Frriend.git
cd Tarkov_AI_Frriend

# Create virtual environment
python -m venv venv
source venv/bin/activate    # macOS/Linux
.\venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — add your GROQ_API_KEY for fastest responses
```

### Run

```bash
python main.py
```

## ⚙️ Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq cloud API key (primary, fastest) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Ollama fallback model |
| `OLLAMA_NUM_CTX` | `2048` | Context window size |
| `WHISPER_MODEL` | `small` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `TTS_VOICE` | `af_heart` | Kokoro fallback voice |
| `TTS_SPEED` | `1.1` | Kokoro speech speed |
| `TWITCH_TOKEN` | — | Twitch OAuth token (optional) |
| `TWITCH_INITIAL_CHANNELS` | — | Twitch channel name (optional) |

### LLM Engine Selection

| Config | Speed | Quality | Requirement |
|--------|-------|---------|-------------|
| Groq (recommended) | 250+ tok/s | Excellent (70B model) | Free API key |
| Ollama qwen2.5:3b | 20-60 tok/s | Good | Local GPU |
| Ollama qwen2.5:7b | 10-30 tok/s | Better | Good GPU |

## 📁 Project Structure

```
Tarkov_AI_Frriend/
├── main.py              # Application entry point & controller
├── brain.py             # Dual LLM brain (Groq + Ollama)
├── gui.py               # Region-composited avatar GUI
├── voice_input.py       # Speech recognition (faster-whisper)
├── voice_output.py      # Text-to-speech (edge-tts + Kokoro)
├── tarkov_data.py       # Tarkov quest knowledge base
├── twitch_bot.py        # Twitch chat integration
├── video_capture.py     # Screen capture module
├── logging_config.py    # Logging configuration
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
├── assets/              # Avatar expression sprites
│   ├── avatar.png       # Base avatar
│   ├── neutral.png      # Neutral expression
│   ├── talk_a.png       # Mouth slightly open
│   ├── talk_b.png       # Mouth wide open
│   ├── blink.png        # Eyes closed
│   ├── think.png        # Thinking expression
│   └── listen.png       # Listening expression
└── models/              # Downloaded model files (auto)
```

## 🎭 Avatar Animation

The avatar uses a **region-based compositing system** for smooth, natural animation:

- **Mouth region**: Only the lower 35% of the face changes during speech — the rest stays rock-stable
- **Eye region**: Blinks composite only the eye area (22-48% of face height)
- **Gradient masks**: Alpha gradients at region borders prevent visible seams
- **Speaking**: Cycles mouth poses at 5-12 fps with natural timing variation
- **Blinking**: Natural intervals (2.5-5.5 seconds)
- **Breathing**: Subtle full-image micro-motion (±1.2px shift, ±0.2% scale)

## 🛠 Development

### Requirements

```bash
pip install -r requirements.txt
```

Key dependencies:
- `groq` — Groq cloud LLM API client
- `ollama` — Local LLM client (fallback)
- `customtkinter` — Modern GUI framework
- `faster-whisper` — GPU-accelerated speech recognition
- `edge-tts` — Microsoft neural TTS voices
- `sounddevice` / `soundfile` — Audio I/O
- `Pillow` — Image processing for avatar compositing
- `twitchio` — Twitch bot framework

### Testing

```bash
# Test GUI
python -c "from gui import OverwatchGUI; app = OverwatchGUI(); app.after(3000, app._on_close); app.mainloop()"

# Test all imports
python -c "from voice_output import VoiceOutput; from voice_input import VoiceInput; from brain import Brain; print('OK')"
```

## 📝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -am 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Groq](https://groq.com) — Ultra-fast cloud LLM inference
- [Ollama](https://ollama.com) — Local LLM inference
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper
- [edge-tts](https://github.com/rany2/edge-tts) — Microsoft neural voices
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — Modern Tkinter
- [Escape from Tarkov](https://www.escapefromtarkov.com/) — Battlestate Games

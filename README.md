<p align="center">
  <img src="assets/avatar.png" alt="SCAV-E" width="160" />
</p>

<h1 align="center">PMC Overwatch — Tarkov AI Companion</h1>

<p align="center">
  <strong>Real-time voice AI companion for Escape from Tarkov</strong><br/>
  Animated avatar • Multilingual speech • Local LLM • Twitch integration
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey?logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/LLM-Ollama%20%28qwen2.5%29-orange?logo=ollama" alt="LLM" />
  <img src="https://img.shields.io/badge/TTS-edge--tts%20Neural-green" alt="TTS" />
  <img src="https://img.shields.io/badge/STT-faster--whisper-red" alt="STT" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
</p>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎤 **Voice Input** | Real-time speech recognition via faster-whisper (GPU accelerated) |
| 🧠 **Local LLM** | Ollama-powered responses with Tarkov quest knowledge base |
| 🔊 **Neural TTS** | Microsoft edge-tts with AriaNeural (EN), DariyaNeural (RU), AlinaNeural (RO) |
| 🎭 **Animated Avatar** | Sprite-based animation with facial expressions (talk, blink, think) |
| 🌐 **Multilingual** | English, Russian, Romanian — auto-detection or manual override |
| 📺 **Twitch Bot** | Optional chat integration for stream interactions |
| 🎮 **Tarkov Knowledge** | Built-in quest reference database for accurate game info |

## 🏗 Architecture

```
┌──────────────────────────────────────────────┐
│                PMC Overwatch GUI              │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Animated  │  │ Activity │  │  Controls   │ │
│  │  Avatar   │  │   Log    │  │ Start/Stop  │ │
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
  │Input  │  │(Ollama)│  │  Output  │
  │Whisper│  │qwen2.5 │  │ edge-tts │
  └───────┘  └────────┘  └──────────┘
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Ollama** — [ollama.com](https://ollama.com) (download and install)
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

# Pull the LLM model
ollama pull qwen2.5:7b

# Configure environment
cp .env.example .env
# Edit .env with your settings
```

### Run

```bash
python main.py
```

## ⚙️ Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama model for responses |
| `OLLAMA_NUM_CTX` | `4096` | Context window size |
| `WHISPER_MODEL` | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `WHISPER_DEVICE` | `auto` | `cuda` or `cpu` |
| `WHISPER_LANGUAGE` | `en` | Speech language (`en`, `ru`, `ro`, `auto`) |
| `TTS_VOICE` | `af_heart` | Kokoro fallback voice |
| `TTS_SPEED` | `1.1` | Kokoro speech speed |
| `TWITCH_TOKEN` | — | Twitch OAuth token (optional) |
| `TWITCH_CHANNEL` | — | Twitch channel name (optional) |

### Language Configuration

| Use Case | `WHISPER_LANGUAGE` | Description |
|----------|-------------------|-------------|
| English only | `en` (default) | Best accuracy for English speech |
| Russian only | `ru` | For Russian-speaking users |
| Romanian only | `ro` | For Romanian-speaking users |
| Auto-detect | `auto` | Detects language automatically (may misidentify) |

## 📁 Project Structure

```
Tarkov_AI_Frriend/
├── main.py              # Application entry point & controller
├── brain.py             # LLM integration (Ollama)
├── gui.py               # Animated avatar GUI (CustomTkinter)
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
│   └── think.png        # Thinking expression
└── models/              # Downloaded model files (auto)
```

## 🎭 Avatar Animation

The avatar uses a **sprite-based animation system** inspired by VTuber and visual novel engines:

- **Speaking**: Cycles through mouth sprites at 8fps for lip-sync
- **Blinking**: Eyes-closed sprite every 3-6 seconds
- **Thinking**: Thoughtful expression with eyes looking up
- **Idle**: Subtle head micro-movement (32 motion-shifted frames)

## 🛠 Development

### Requirements

```bash
pip install -r requirements.txt
```

Key dependencies:
- `customtkinter` — Modern GUI framework
- `faster-whisper` — GPU-accelerated speech recognition
- `edge-tts` — Microsoft neural TTS voices
- `ollama` — Local LLM client
- `sounddevice` / `soundfile` — Audio I/O
- `Pillow` — Image processing for avatar
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

- [Ollama](https://ollama.com) — Local LLM inference
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper
- [edge-tts](https://github.com/rany2/edge-tts) — Microsoft neural voices
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — Modern Tkinter
- [Escape from Tarkov](https://www.escapefromtarkov.com/) — Battlestate Games

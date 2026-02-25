# PMC Overwatch — Tarkov AI Companion

> **⚠️ Work in Progress** — This project is under active development.

A real-time AI voice companion for Escape from Tarkov that listens, talks, and helps you survive raids. Runs entirely **locally** on macOS — no paid APIs or cloud services required.

## Features

- **🎙 Voice Chat** — Speak naturally and get instant voice responses
- **🧠 AI Brain** — Local LLM (Ollama) with conversation memory and Tarkov expertise  
- **🎤 Speech Recognition** — Local transcription via faster-whisper (offline)
- **🔊 Natural TTS** — High-quality voice via Kokoro neural TTS (female voice)
- **🎨 Animated GUI** — Dark-mode desktop app with animated AI avatar and waveform visualizer
- **📺 Twitch Integration** — Optional Twitch chat bot for stream interaction

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | [Ollama](https://ollama.ai) (Mistral) — local inference |
| TTS | [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx) — neural voice |
| STT | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local transcription |
| GUI | [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — modern dark UI |
| Chat | [TwitchIO](https://github.com/TwitchIO/TwitchIO) — optional Twitch bot |

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- ~8 GB RAM minimum

## Setup

```bash
# Clone the repo
git clone https://github.com/Bossiq/Tarkov_AI_Frriend.git
cd Tarkov_AI_Frriend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env

# Pull the Ollama model
ollama pull mistral

# Run
python main.py
```

## Configuration

All settings are in `.env` — see `.env.example` for documentation:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `mistral` | LLM model name |
| `TTS_VOICE` | `af_heart` | Kokoro voice ID |
| `TTS_SPEED` | `1.1` | Speech speed multiplier |
| `WHISPER_MODEL` | `base` | Whisper model size |

## Project Structure

```
├── main.py            # Entry point — wires everything together
├── brain.py           # AI brain (Ollama LLM with memory)
├── voice_input.py     # Mic capture + faster-whisper transcription
├── voice_output.py    # Kokoro TTS with text preprocessing
├── gui.py             # Animated desktop GUI with avatar
├── twitch_bot.py      # Optional Twitch chat integration
├── video_capture.py   # Optional webcam capture
├── logging_config.py  # Centralized logging
├── assets/
│   └── avatar.png     # AI companion avatar
├── .env.example       # Environment variable template
└── requirements.txt   # Python dependencies
```

## License

MIT License — see [LICENSE](LICENSE).

---

*Built with ❤️ for the Tarkov community.*

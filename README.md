# PMC Overwatch — Tarkov AI Companion

> Real-time AI voice companion for Escape from Tarkov. Speak naturally, get instant voice responses with accurate quest knowledge. **Runs entirely offline on macOS and Windows.**

## ✨ Features

| Feature | Description |
|---------|-------------|
| **🎙 Voice Chat** | Speech → AI → Voice pipeline with natural conversation |
| **🧠 Tarkov Expert** | Complete quest database with 200+ quests, bosses, ammo, extracts |
| **🎤 Offline STT** | faster-whisper speech recognition (callback-based, never blocks) |
| **🔊 Neural TTS** | Kokoro ONNX — warm, natural female voice at 1.2x speed |
| **👩 Animated Avatar** | Photorealistic portrait with orbiting particles, glow ring, voice bars |
| **📺 Twitch Bot** | Optional Twitch chat integration |

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | [Ollama](https://ollama.ai) — local LLM inference |
| TTS | [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx) — neural voice |
| STT | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 / CUDA accelerated |
| GUI | [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) + Canvas animations |

## 💻 Hybrid Workflow (Mac & Windows PC)

This project supports seamless development and usage across macOS (ARM64) and Windows (x64 with NVIDIA GPUs).

**Important Git Rules for Hybrid Workflow:**
- **Virtual Environments:** Create `venv/` on Mac and `venv2/` on Windows. Both are ignored in `.gitignore`.
- **Environment Variables:** Never commit your `.env` file. Keep a separate `.env` on your Mac and your PC.
- **Dependencies:** Run `pip install -r requirements.txt` on both systems. Windows will automatically use CUDA for `faster-whisper` and larger local LLM models if available.

### PC Quick Start (Windows)
```powershell
# 1. Clone & Setup Venv
git clone https://github.com/Bossiq/Tarkov_AI_Frriend.git
cd Tarkov_AI_Frriend
python -m venv venv2
.\venv2\Scripts\activate

# 2. Install Dependencies
pip install -r requirements.txt
copy .env.example .env

# 3. Setup Ollama (Needs to be installed from ollama.com)
ollama pull qwen2.5:7b

# 4. Run!
python main.py
```

## 🔑 Keys and Integrations (OBS & Twitch)

All keys and integrations are managed entirely through the `.env` file in the root of the project.

### 🎮 How to Connect to Twitch
To let PMC Overwatch read your Twitch chat, update your `.env` file:
1. **TWITCH_INITIAL_CHANNELS:** Set this to your Twitch channel name (e.g., `bossiq420`).
2. **TWITCH_TOKEN:** Generate an OAuth token from [twitchapps.com/tmi/](https://twitchapps.com/tmi/). It should look like `oauth:xxxxxxxxxx`.

The app will automatically connect on startup and the AI will respond to chat messages containing "scav" or "blyat".

### 🎥 How to Connect to OBS
OBS integration for video capture relies on the **OBS Virtual Camera**:
1. Open OBS Studio.
2. Click **Start Virtual Camera** in the Controls panel.
3. The python script (`video_capture.py`) grabs frames from the default virtual camera device seamlessly. 
> Note: Video capture AI analysis is an experimental background thread in `main.py` which triggers periodically.

## ⚙️ Configuration (.env)

| Variable | PC Default | Mac Default | Description |
|----------|------------|-------------|-------------|
| `OLLAMA_MODEL` | `qwen2.5:7b` | `qwen2.5:3b` | LLM model. PC handles 7B easily! |
| `OLLAMA_NUM_CTX` | `4096` | `4096` | Context window size |
| `TTS_VOICE` | `af_heart` | `af_heart` | Kokoro voice ID |
| `WHISPER_MODEL` | `small` | `base`| STT size. PC uses `float16` CUDA. |

## 📄 License
MIT — see [LICENSE](LICENSE).

---
*Built by [Bossiq](https://github.com/Bossiq)*

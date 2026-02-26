# Changelog

All notable changes to PMC Overwatch are documented here.

## [16.0] — 2026-02-26

### Added
- **Audio-driven lip sync**: RMS amplitude callback (20ms chunks) maps to mouth poses
- **Emotion detection**: Keyword-based sentiment analysis drives avatar expressions
- **Push-to-talk**: Three input modes (auto/toggle/push) with configurable hotkey (F4)
- `pynput` keyboard listener for global PTT hotkey
- `INPUT_MODE`, `PTT_KEY`, `EDGE_RATE` environment variables
- Input mode indicator in GUI footer

### Changed
- Renamed `SCAVESystem` → `PMCOverwatch` to match PMC branding
- Replaced random mouth cycling with amplitude-driven compositing
- Improved audio normalization: dynamic range compression with soft-knee
- Cleaned 60 lines of dead code from `voice_output.py`
- Updated README with new feature table and architecture diagram
- Updated `.env.example` with full PTT documentation

### Fixed
- Dead code (unreachable Kokoro pipeline) removed from `voice_output.py`

## [Unreleased] — 2026-02-26

### Added
- **Groq cloud API** as primary LLM brain (250+ tokens/sec, free tier)
- Automatic fallback to Ollama local inference when no API key is set
- **Anime-style avatar** with 7 expression sprites (Ghost in the Shell aesthetic)
- **Region-based compositing** — mouth and eye regions blend independently
- Multi-stage blink system (half → full → half → open) with 15% double-blink chance
- Random head micro-sway and micro-expression smile flickers
- `smile.png` and `listen.png` expression sprites
- `CHANGELOG.md` for version history

### Changed
- Replaced vector-drawn geometric face with PIL image sprite engine
- Upgraded `brain.py` to dual-engine architecture (Groq primary, Ollama fallback)
- Reduced context window (4096 → 2048) for faster prompt processing
- Reduced max tokens (256 → 150) for punchier responses
- Added `num_batch=512` option for faster Ollama inference
- Reduced conversation memory from 6 to 4 turns
- Updated `README.md` with Groq setup, architecture diagram, LLM comparison
- Updated `.env.example` with full documentation
- Updated `requirements.txt` with `groq` dependency

### Fixed
- Signal handler crash: `_on_closing` → `_on_close` typo in `main.py`
- `.env` model override: was using `qwen2.5:7b` (too slow), now defaults to `3b`

## [12.0] — 2026-02-26

### Added
- Vector-drawn animated face with independent feature animation (eyes, mouth, brows)
- Linear interpolation and exponential smoothing for fluid facial animation
- Glow ring and voice bars reactive to speaking/thinking/listening states

## [11.0] — 2026-02-25

### Added
- Sprite-based avatar system with pre-rendered 3D character portraits
- Multiple expression states: neutral, talk, blink, think

## [10.0] — 2026-02-25

### Changed
- Language detection improvements for Russian/Romanian
- Enterprise-ready README with badges and architecture diagram

## [5.0] — 2026-02-24

### Added
- Callback-based audio capture (fixes listener blocking)
- Particle effects and micro-sway breathing animation
- Breathing avatar scale animation

### Fixed
- Quest reference data accuracy
- Listening state feedback to GUI

## [1.0] — 2026-02-24

### Added
- Initial project structure
- Video capture module (OpenCV)
- Twitch bot integration (TwitchIO)
- Voice input with faster-whisper STT and VAD
- Voice output with edge-tts neural voices and Kokoro fallback
- AI brain with Ollama local inference
- Tarkov quest knowledge base
- CustomTkinter dark-mode GUI
- MIT License

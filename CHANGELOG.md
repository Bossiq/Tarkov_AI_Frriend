# Changelog

All notable changes to PMC Overwatch are documented here.

## [0.23.0] — 2026-02-26

### Added
- **Procedural live avatar**: entire character drawn from code — ZERO sprite images
- Face shape, eyes (iris/pupil/highlights), mouth, eyebrows, nose, hair, shoulders
- 2x supersampled rendering with LANCZOS downscale for anti-aliasing
- Holographic post-processing on drawn output (scanlines, chromatic aberration)

### Changed
- Avatar pipeline: PIL procedural drawing replaces sprite compositing
- Character is generated fresh every frame — truly alive, not image pasting
- Gaze follows noise-driven targets, eyes track independently
- Mouth shape varies (line → smile arc → open ellipse with teeth)
- Hair sways organically with per-strand noise offsets

## [0.22.0] — 2026-02-26

### Added
- **3D holographic avatar**: upper-body sprites with holographic projection look
- **HoloFX pipeline**: scanlines, chromatic aberration, edge glow, flicker, cyan tint
- **Glass-morphism UI**: darker palette, wider radius corners, accent colors, modern fonts
- Precomputed scanline overlay for zero per-frame cost

### Changed
- All 6 sprites regenerated as holographic upper-body style
- GUI background → `#050810` (deeper), cards → `#0d1117`
- Canvas expanded to 420×440, waveform bars → 35
- Status labels use diamond ◆ prefix
- Send button uses accent blue

## [0.21.0] — 2026-02-26

### Added
- **Alive animation engine**: multi-frequency smoothed noise drives all motion
- Character NEVER sits still — continuous organic micro-motion on head, gaze, scale
- **Micro-expressions**: random smile flickers every 3-8 seconds like a real person
- **Amplitude-synced head nods**: head bobs naturally during speech
- **Listening lean**: avatar leans forward slightly when listening
- **Breathing**: visible up/down + scale oscillation always active

### Changed
- Head/gaze motion uses `_SmoothedNoise` (3 overlapping sine waves) instead of lerp-to-random-target
- All parameters use exponential smoothing for buttery transitions

### Removed
- Ear-piercing `winsound.Beep()` sounds on mode transitions

## [0.20.0] — 2026-02-26

### Added
- **Layered compositing**: independent mouth/eye/expression region blending
- **OBS Overlay mode**: Ctrl+O toggles transparent topmost window for streaming
- **Persona Editor**: Ctrl+P opens panel to edit AI personality/system prompt
- **Chat History**: auto-saves session logs to `logs/` on close
- **Sound Effects**: audio cues on mode transitions (listening/speaking/thinking)
- **Language Selector**: UI dropdown (Auto/EN/RU/RO) changes Whisper on-the-fly
- **Ambient glow**: soft colored circle behind avatar pulses with breathing
- **Session stats**: track words spoken, responses, session duration

### Changed
- All voices → **female**: JennyNeural (EN), SvetlanaNeural (RU), AlinaNeural (RO)
- Speech rate → **+0%** (reset from -5%)
- Whisper STT → **auto-detect** language (was English-only)
- Whisper model → **small** (from base, better multilingual)
- Stronger head motion (±10px) and breathing (2x amplitude)
- Sine-wave voice bars for smoother visualizer

### Fixed
- Romanian speech not understood (Whisper was hardcoded to English)
- Wrong language voice reading (per-sentence detection improved)

## [0.19.0] — 2026-02-26

### Added
- **Full cross-fade animation**: entire expression sprites blend (not region compositing)
- **New high-quality sprites**: 6 dramatically different expression images (neutral/talk_a/talk_b/blink/smile/think)
- **Ambient particles**: floating glow particles around avatar for alive feel
- **Romanian word detection**: detects Romanian without diacritics using common word patterns

### Changed
- English voice: AriaNeural → **ChristopherNeural** (warm male PMC tone)
- Russian voice: DariyaNeural → **DmitryNeural** (natural male)
- Romanian voice: AlinaNeural → **EmilNeural** (natural male)
- Speech rate: +0% → **-5%** (slightly slower, more natural)
- Added German and French voice fallbacks

### Fixed
- Language detection: Romanian text without diacritics now correctly detected
- Per-sentence voice selection prevents wrong-accent reading

## [0.18.0] — 2026-02-26

### Added
- **Premium hybrid renderer**: anime girl sprite base + PIL animated overlays
- Amplitude-driven mouth: elliptical mask fades talk sprites with GaussianBlur edges
- Eyelid curtains: skin-sampled rectangles slide down for natural blinks
- Smooth transition between neutral/talk_a/talk_b/smile/think sprites

### Changed
- Replaced bare Canvas vector face with high-quality sprite hybrid
- Restored PIL/Pillow dependency for avatar compositing
- Better head motion (offset via crop, breathing via scale)

## [0.17.0] — 2026-02-26

### Added
- **Live vector face**: Canvas-drawn face with head, eyes (iris/pupil/eyelid), eyebrows, mouth, nose, hair, ears
- Smooth eyelid blinks with multi-stage animation
- Gaze wander (iris/pupil tracking)
- Bezier mouth morphing driven by audio amplitude (teeth visible when wide open)
- Eyebrow expressions: raised (attentive), furrowed (thinking), relaxed (idle)
- Per-mode facial expressions: attentive listening, thoughtful thinking, animated speaking

### Changed
- Removed PIL/Image dependency for avatar rendering
- Removed PNG sprite system (`_AliveEngine`)
- FPS increased from 24 to 30

### Removed
- Sprite-based avatar engine (replaced entirely by vector rendering)

## [0.16.0] — 2026-02-26

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

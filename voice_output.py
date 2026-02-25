"""
Voice Output — high-quality TTS via Kokoro (ONNX) for PMC Overwatch.

Uses the Kokoro 82M neural TTS model running locally via ONNX Runtime.
Falls back to the macOS ``say`` command if Kokoro fails to initialise.

Model files are auto-downloaded on first run to the ``models/`` directory.
"""

import logging
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# ── Paths & URLs ─────────────────────────────────────────────────────
_MODELS_DIR = Path(__file__).parent / "models"
_ONNX_FILE = _MODELS_DIR / "kokoro-v1.0.onnx"
_VOICES_FILE = _MODELS_DIR / "voices-v1.0.bin"

_ONNX_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# ── Defaults ─────────────────────────────────────────────────────────
_DEFAULT_VOICE = "am_michael"
_DEFAULT_SPEED = 1.0
_DEFAULT_LANG = "en-us"
_SAY_TIMEOUT_S = 30


class VoiceOutput:
    """High-quality TTS with Kokoro, falling back to macOS ``say``."""

    def __init__(self, gui_callback: Optional[Callable[[str], None]] = None) -> None:
        self._gui_callback = gui_callback
        self._voice = os.getenv("TTS_VOICE", _DEFAULT_VOICE)
        self._speed = float(os.getenv("TTS_SPEED", str(_DEFAULT_SPEED)))
        self._kokoro = None

        self._init_kokoro()

    # ── Kokoro initialisation ─────────────────────────────────────────
    def _init_kokoro(self) -> None:
        """Load the Kokoro model, downloading files if necessary."""
        try:
            self._ensure_model_files()

            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(str(_ONNX_FILE), str(_VOICES_FILE))
            logger.info(
                "Kokoro TTS loaded (voice=%s, speed=%.1f)", self._voice, self._speed
            )
        except Exception:
            logger.exception(
                "Failed to load Kokoro TTS — falling back to macOS 'say'"
            )
            self._kokoro = None

    def _ensure_model_files(self) -> None:
        """Download model files if they don't exist."""
        _MODELS_DIR.mkdir(exist_ok=True)

        for path, url, label in [
            (_ONNX_FILE, _ONNX_URL, "model"),
            (_VOICES_FILE, _VOICES_URL, "voices"),
        ]:
            if not path.exists():
                logger.info("Downloading Kokoro %s → %s …", label, path.name)
                urllib.request.urlretrieve(url, path)
                logger.info("Downloaded %s (%.1f MB)", path.name, path.stat().st_size / 1e6)

    # ── Public API ────────────────────────────────────────────────────
    def speak(self, text: str) -> None:
        """Speak *text* aloud.  No-op if text is empty."""
        if not text or not text.strip():
            return

        clean_text = " ".join(text.split())

        if self._gui_callback:
            self._gui_callback(f"🎙 PMC: {clean_text}")

        if self._kokoro is not None:
            self._speak_kokoro(clean_text)
        else:
            self._speak_say(clean_text)

    # ── Kokoro speech ─────────────────────────────────────────────────
    def _speak_kokoro(self, text: str) -> None:
        """Generate speech with Kokoro and play it through speakers."""
        try:
            samples, sample_rate = self._kokoro.create(
                text, voice=self._voice, speed=self._speed, lang=_DEFAULT_LANG
            )
            # Play audio synchronously through the default output device
            sd.play(samples, samplerate=sample_rate)
            sd.wait()
            logger.debug("Kokoro TTS playback complete")
        except Exception:
            logger.exception("Kokoro TTS failed — falling back to 'say'")
            self._speak_say(text)

    # ── macOS fallback ────────────────────────────────────────────────
    def _speak_say(self, text: str) -> None:
        """Fallback: speak via macOS ``say`` command."""
        try:
            subprocess.run(["say", text], check=True, timeout=_SAY_TIMEOUT_S)
        except FileNotFoundError:
            logger.error("'say' command not found — are you on macOS?")
        except subprocess.TimeoutExpired:
            logger.warning("TTS timed out after %ds", _SAY_TIMEOUT_S)
        except Exception:
            logger.exception("Fallback TTS error")


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    vo = VoiceOutput()
    vo.speak("Acknowledged. Holding position. Target acquired at two hundred metres.")

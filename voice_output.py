"""
Voice Output — text-to-speech via macOS ``say`` command.

Provides a simple, dependency-free TTS path for development.
Phase 2 will replace this with XTTSv2 or another local voice clone engine.
"""

import logging
import shlex
import subprocess
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_S = 30  # max time for a single utterance


class VoiceOutput:
    """Speaks text aloud using the macOS native ``say`` command."""

    def __init__(self, gui_callback: Optional[Callable[[str], None]] = None) -> None:
        self._gui_callback = gui_callback

    def speak(self, text: str) -> None:
        """Speak *text* aloud.  No-op if text is empty."""
        if not text or not text.strip():
            return

        # Sanitise: strip control characters, keep it single-line for say
        clean_text = " ".join(text.split())

        if self._gui_callback:
            self._gui_callback(f"🎙 PMC: {clean_text}")

        try:
            subprocess.run(
                ["say", clean_text],
                check=True,
                timeout=_SUBPROCESS_TIMEOUT_S,
            )
        except FileNotFoundError:
            logger.error("'say' command not found — are you on macOS?")
        except subprocess.TimeoutExpired:
            logger.warning("TTS timed out after %ds", _SUBPROCESS_TIMEOUT_S)
        except subprocess.CalledProcessError:
            logger.exception("TTS subprocess returned non-zero exit code")
        except Exception:
            logger.exception("Unexpected TTS error")


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    vo = VoiceOutput()
    vo.speak("Affirmative. Holding position.")

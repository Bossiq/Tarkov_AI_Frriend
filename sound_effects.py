"""
Sound Effects — event-driven UI audio cues for PMC Overwatch.

Plays short, non-intrusive sound effects triggered by system events.
Effects are generated programmatically using numpy (sine waves + filters)
to avoid copyrighted audio.

Features:
  • Event-based: startup, thinking, respond, twitch, barge-in, error,
    kill, death, loot, extract
  • Non-blocking playback via sounddevice
  • Per-effect cooldown (prevents rapid-fire spam)
  • Volume control via SFX_VOLUME env var
  • Graceful fallback if sound files missing
  • Thread-safe — can be triggered from any thread
"""

import logging
import math
import os
import threading
import time
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    logger.info("sounddevice not available — SFX disabled")

_SAMPLERATE = 44100
_SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "sfx")

# Per-effect cooldown (seconds) — prevents spam
_COOLDOWNS = {
    "startup": 5.0,
    "thinking": 3.0,
    "respond": 2.0,
    "twitch": 1.5,
    "bargein": 1.0,
    "error": 3.0,
    "kill": 2.0,
    "death": 3.0,
    "loot": 2.0,
    "extract": 5.0,
}


def _generate_tone(
    freq: float,
    duration: float,
    volume: float = 0.3,
    fade_in: float = 0.01,
    fade_out: float = 0.05,
    sr: int = _SAMPLERATE,
) -> np.ndarray:
    """Generate a smooth sine-wave tone with fade in/out."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    wave = np.sin(2 * math.pi * freq * t) * volume

    # Fade in
    fade_in_samples = int(sr * fade_in)
    if fade_in_samples > 0:
        wave[:fade_in_samples] *= np.linspace(0, 1, fade_in_samples)

    # Fade out
    fade_out_samples = int(sr * fade_out)
    if fade_out_samples > 0:
        wave[-fade_out_samples:] *= np.linspace(1, 0, fade_out_samples)

    return wave.astype(np.float32)


def _generate_startup_sfx() -> np.ndarray:
    """Boot-up chime: ascending two-note arpeggio."""
    note1 = _generate_tone(523.25, 0.12, 0.20, fade_out=0.03)  # C5
    gap = np.zeros(int(_SAMPLERATE * 0.04), dtype=np.float32)
    note2 = _generate_tone(659.25, 0.18, 0.25, fade_out=0.08)  # E5
    return np.concatenate([note1, gap, note2])


def _generate_thinking_sfx() -> np.ndarray:
    """Soft processing blip: gentle low ping."""
    return _generate_tone(440.0, 0.08, 0.12, fade_out=0.04)


def _generate_respond_sfx() -> np.ndarray:
    """Response notification: two quick ascending pings."""
    note1 = _generate_tone(587.33, 0.06, 0.15, fade_out=0.02)  # D5
    gap = np.zeros(int(_SAMPLERATE * 0.03), dtype=np.float32)
    note2 = _generate_tone(783.99, 0.10, 0.18, fade_out=0.05)  # G5
    return np.concatenate([note1, gap, note2])


def _generate_twitch_sfx() -> np.ndarray:
    """Chat message ping: medium pitched blip."""
    return _generate_tone(698.46, 0.10, 0.15, fade_out=0.05)  # F5


def _generate_bargein_sfx() -> np.ndarray:
    """Interruption: quick descending two-note."""
    note1 = _generate_tone(880.0, 0.05, 0.20, fade_out=0.02)   # A5
    note2 = _generate_tone(587.33, 0.08, 0.15, fade_out=0.04)  # D5
    return np.concatenate([note1, note2])


def _generate_error_sfx() -> np.ndarray:
    """Error tone: low descending buzz."""
    note1 = _generate_tone(349.23, 0.10, 0.18, fade_out=0.03)  # F4
    gap = np.zeros(int(_SAMPLERATE * 0.05), dtype=np.float32)
    note2 = _generate_tone(261.63, 0.15, 0.15, fade_out=0.08)  # C4
    return np.concatenate([note1, gap, note2])


def _generate_kill_sfx() -> np.ndarray:
    """Kill confirmed: sharp ascending triple chime."""
    note1 = _generate_tone(659.25, 0.06, 0.22, fade_out=0.02)  # E5
    gap = np.zeros(int(_SAMPLERATE * 0.02), dtype=np.float32)
    note2 = _generate_tone(783.99, 0.06, 0.25, fade_out=0.02)  # G5
    note3 = _generate_tone(1046.50, 0.12, 0.28, fade_out=0.06)  # C6
    return np.concatenate([note1, gap, note2, gap, note3])


def _generate_death_sfx() -> np.ndarray:
    """Death buzzer: descending three-note with longer decay."""
    note1 = _generate_tone(392.00, 0.08, 0.20, fade_out=0.03)   # G4
    note2 = _generate_tone(293.66, 0.10, 0.18, fade_out=0.04)   # D4
    note3 = _generate_tone(196.00, 0.18, 0.15, fade_out=0.10)   # G3
    return np.concatenate([note1, note2, note3])


def _generate_loot_sfx() -> np.ndarray:
    """Loot sparkle: quick shimmering two-note."""
    note1 = _generate_tone(1174.66, 0.05, 0.15, fade_out=0.02)  # D6
    gap = np.zeros(int(_SAMPLERATE * 0.03), dtype=np.float32)
    note2 = _generate_tone(1396.91, 0.08, 0.18, fade_out=0.04)  # F6
    return np.concatenate([note1, gap, note2])


def _generate_extract_sfx() -> np.ndarray:
    """Extract success: triumphant ascending four-note fanfare."""
    note1 = _generate_tone(523.25, 0.10, 0.18, fade_out=0.02)  # C5
    gap = np.zeros(int(_SAMPLERATE * 0.03), dtype=np.float32)
    note2 = _generate_tone(659.25, 0.10, 0.20, fade_out=0.02)  # E5
    note3 = _generate_tone(783.99, 0.10, 0.22, fade_out=0.02)  # G5
    note4 = _generate_tone(1046.50, 0.20, 0.25, fade_out=0.10)  # C6
    return np.concatenate([note1, gap, note2, gap, note3, gap, note4])


# Generator registry
_GENERATORS = {
    "startup": _generate_startup_sfx,
    "thinking": _generate_thinking_sfx,
    "respond": _generate_respond_sfx,
    "twitch": _generate_twitch_sfx,
    "bargein": _generate_bargein_sfx,
    "error": _generate_error_sfx,
    "kill": _generate_kill_sfx,
    "death": _generate_death_sfx,
    "loot": _generate_loot_sfx,
    "extract": _generate_extract_sfx,
}


class SoundEffects:
    """Event-driven sound effect engine.

    Usage:
        sfx = SoundEffects()
        sfx.play("startup")
        sfx.play("thinking")
    """

    def __init__(
        self,
        enabled: Optional[bool] = None,
        volume: Optional[float] = None,
    ) -> None:
        self._enabled = enabled if enabled is not None else (
            os.getenv("SFX_ENABLED", "true").lower() in ("true", "1", "yes")
        )
        self._volume = volume if volume is not None else float(
            os.getenv("SFX_VOLUME", "0.5")
        )
        self._volume = max(0.0, min(1.0, self._volume))

        self._sounds: Dict[str, np.ndarray] = {}
        self._last_played: Dict[str, float] = {}
        self._lock = threading.Lock()

        if self._enabled and _SD_AVAILABLE:
            self._load_or_generate()
            logger.info(
                "SFX engine ready (%d effects, volume=%.0f%%)",
                len(self._sounds), self._volume * 100,
            )
        elif not _SD_AVAILABLE:
            logger.info("SFX disabled — sounddevice not available")
        else:
            logger.info("SFX disabled via config")

    def _load_or_generate(self) -> None:
        """Load WAV files from assets/sfx/ or generate programmatically."""
        os.makedirs(_SFX_DIR, exist_ok=True)

        for name, generator in _GENERATORS.items():
            wav_path = os.path.join(_SFX_DIR, f"{name}.wav")

            # Try loading existing WAV first
            if os.path.exists(wav_path):
                try:
                    import soundfile as sf
                    data, sr = sf.read(wav_path, dtype="float32")
                    if sr != _SAMPLERATE:
                        # Resample roughly
                        ratio = _SAMPLERATE / sr
                        indices = np.arange(0, len(data), 1 / ratio).astype(int)
                        indices = indices[indices < len(data)]
                        data = data[indices]
                    self._sounds[name] = data
                    continue
                except Exception:
                    logger.debug("Could not load %s, generating", wav_path)

            # Generate
            try:
                audio = generator()
                self._sounds[name] = audio

                # Save for future use
                try:
                    import soundfile as sf
                    sf.write(wav_path, audio, _SAMPLERATE)
                except Exception:
                    pass  # Non-critical: generation succeeded

            except Exception:
                logger.warning("Failed to generate SFX: %s", name)

    def play(self, event: str) -> None:
        """Play a sound effect by event name (non-blocking).

        Respects cooldown — won't replay the same effect too quickly.
        """
        if not self._enabled or not _SD_AVAILABLE:
            return

        audio = self._sounds.get(event)
        if audio is None:
            return

        # Check cooldown
        cooldown = _COOLDOWNS.get(event, 1.0)
        now = time.monotonic()
        with self._lock:
            last = self._last_played.get(event, 0.0)
            if now - last < cooldown:
                return
            self._last_played[event] = now

        # Play (non-blocking)
        try:
            scaled = audio * self._volume
            sd.play(scaled, samplerate=_SAMPLERATE, blocking=False)
        except Exception:
            logger.debug("SFX playback failed: %s", event)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_volume(self, volume: float) -> None:
        """Set volume (0.0 to 1.0)."""
        self._volume = max(0.0, min(1.0, volume))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable sound effects at runtime."""
        self._enabled = enabled
        if enabled and not self._sounds and _SD_AVAILABLE:
            self._load_or_generate()


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()

    sfx = SoundEffects(enabled=True, volume=0.6)
    print("Playing SFX demo...")
    for event in _GENERATORS:
        print(f"  {event}")
        sfx.play(event)
        time.sleep(0.8)
    print("SFX demo complete")

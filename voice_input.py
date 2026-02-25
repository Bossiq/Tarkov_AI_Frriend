"""
Voice Input — microphone capture with adaptive VAD + local transcription.

Uses ambient noise calibration to resist background music/noise.
Requires sustained speech before triggering to prevent false activations.
Transcription via faster-whisper (CTranslate2-optimized Whisper) for
high-accuracy, fully offline speech-to-text.
"""

import logging
import os
import time
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────
_SAMPLERATE = 16_000
_CHANNELS = 1
_CHUNK_SECONDS = 0.1
_SILENCE_DURATION = 1.2           # seconds of silence before stopping
_MAX_RECORDING_SECONDS = 30       # safety cap
_CALIBRATION_SECONDS = 1.0        # how long to sample ambient noise
_NOISE_MULTIPLIER = 2.5           # threshold = ambient_rms × this
_MIN_SPEECH_CHUNKS = 3            # require N consecutive loud chunks to start
_DEFAULT_WHISPER_MODEL = "large-v3-turbo"
_DEFAULT_COMPUTE_TYPE = "int8"    # fast on Apple Silicon, good accuracy


class VoiceInput:
    """Captures speech with adaptive noise-resistant VAD + local transcription."""

    def __init__(
        self,
        samplerate: int = _SAMPLERATE,
        channels: int = _CHANNELS,
        silence_duration: float = _SILENCE_DURATION,
        max_duration: float = _MAX_RECORDING_SECONDS,
        shutdown_event: Optional[threading.Event] = None,
        gui_log=None,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.silence_duration = silence_duration
        self.max_duration = max_duration
        self._shutdown = shutdown_event or threading.Event()
        self._gui_log = gui_log
        self._threshold: Optional[float] = None
        self._chunk_size = int(self.samplerate * _CHUNK_SECONDS)

        # Lazy-loaded Whisper model
        self._whisper_model = None
        self._whisper_lock = threading.Lock()

    # ── Whisper model loading ─────────────────────────────────────────
    def _get_whisper_model(self):
        """Lazy-load the faster-whisper model (thread-safe)."""
        if self._whisper_model is not None:
            return self._whisper_model

        with self._whisper_lock:
            if self._whisper_model is not None:
                return self._whisper_model

            model_name = os.getenv("WHISPER_MODEL", _DEFAULT_WHISPER_MODEL)
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", _DEFAULT_COMPUTE_TYPE)

            if self._gui_log:
                self._gui_log(f"🤖 Loading speech model ({model_name}) — first run may download …")
            logger.info(
                "Loading faster-whisper model '%s' (compute=%s) …",
                model_name, compute_type,
            )

            from faster_whisper import WhisperModel

            self._whisper_model = WhisperModel(
                model_name,
                device="cpu",
                compute_type=compute_type,
            )

            if self._gui_log:
                self._gui_log("🤖 Speech model loaded ✅")
            logger.info("Whisper model loaded successfully")
            return self._whisper_model

    # ── Adaptive noise calibration ────────────────────────────────────
    def calibrate(self, gui_log=None) -> float:
        """Sample ambient noise and set an adaptive threshold.

        Returns the computed threshold.
        """
        if gui_log:
            gui_log("🔊 Calibrating mic — stay quiet for 1 second …")
        logger.info("Calibrating ambient noise level …")

        samples = []
        num_chunks = int(_CALIBRATION_SECONDS / _CHUNK_SECONDS)

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
            ) as stream:
                for _ in range(num_chunks):
                    chunk, _ = stream.read(self._chunk_size)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    samples.append(rms)
        except Exception:
            logger.exception("Calibration failed — using fallback threshold")
            self._threshold = 0.03
            return self._threshold

        ambient_rms = float(np.mean(samples)) if samples else 0.005
        # Floor to prevent near-zero thresholds in silent rooms
        self._threshold = max(ambient_rms * _NOISE_MULTIPLIER, 0.015)
        logger.info(
            "Ambient RMS: %.4f → Threshold: %.4f (×%.1f)",
            ambient_rms, self._threshold, _NOISE_MULTIPLIER,
        )
        if gui_log:
            gui_log(f"🔊 Calibrated (threshold: {self._threshold:.3f})")

        # Pre-load Whisper model in background while user starts talking
        threading.Thread(
            target=self._get_whisper_model, name="WhisperLoad", daemon=True
        ).start()

        return self._threshold

    # ── Main listening loop ───────────────────────────────────────────
    def listen(self, output_filename: str = "temp_audio.wav") -> Optional[str]:
        """Block until speech is detected, recorded, and silence follows.

        Returns the path to the saved WAV file, or None.
        """
        # Auto-calibrate on first call
        if self._threshold is None:
            self.calibrate()

        logger.info("Listening (threshold=%.4f) …", self._threshold)
        recorded_frames: list[np.ndarray] = []
        is_recording = False
        silence_start: Optional[float] = None
        recording_start: Optional[float] = None
        consecutive_loud = 0   # debounce counter

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
            ) as stream:
                while not self._shutdown.is_set():
                    chunk, _ = stream.read(self._chunk_size)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))

                    if rms > self._threshold:
                        consecutive_loud += 1
                        silence_start = None

                        if not is_recording and consecutive_loud >= _MIN_SPEECH_CHUNKS:
                            # Sustained speech confirmed — start recording
                            logger.info("Speech detected — recording …")
                            is_recording = True
                            recording_start = time.monotonic()

                        if is_recording:
                            recorded_frames.append(chunk.copy())
                    else:
                        consecutive_loud = 0

                        if is_recording:
                            recorded_frames.append(chunk.copy())
                            if silence_start is None:
                                silence_start = time.monotonic()
                            elif time.monotonic() - silence_start > self.silence_duration:
                                logger.debug("Silence reached — done")
                                break

                    # Safety: cap total recording length
                    if (
                        is_recording
                        and recording_start
                        and time.monotonic() - recording_start > self.max_duration
                    ):
                        logger.warning("Max duration (%ss) reached", self.max_duration)
                        break

        except sd.PortAudioError:
            logger.exception("PortAudio error — is a microphone connected?")
            return None
        except Exception:
            logger.exception("Unexpected error in VAD loop")
            return None

        if self._shutdown.is_set():
            logger.info("Listening cancelled (shutdown)")
            return None

        if not recorded_frames:
            return None

        audio_data = np.concatenate(recorded_frames, axis=0)
        sf.write(output_filename, audio_data, self.samplerate)
        duration = len(audio_data) / self.samplerate
        logger.info("Saved %.1fs recording → %s", duration, output_filename)
        return output_filename

    # ── Transcription ─────────────────────────────────────────────────
    def transcribe(self, audio_path: str) -> Optional[str]:
        """Transcribe a WAV file to text using faster-whisper (local).

        Returns the transcribed text, or None if transcription fails.
        """
        start_time = time.monotonic()
        try:
            model = self._get_whisper_model()

            segments, info = model.transcribe(
                audio_path,
                language="en",
                beam_size=5,
                vad_filter=True,              # filter out non-speech
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                    speech_pad_ms=200,
                ),
            )

            # Collect segments into full text
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            text = " ".join(text_parts).strip()

            elapsed = time.monotonic() - start_time
            logger.info(
                "Transcription (%.1fs): %s",
                elapsed, text[:100] if text else "(empty)",
            )

            return text if text else None

        except Exception:
            logger.exception("Transcription failed")
            return None


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    vi = VoiceInput()
    vi.calibrate()
    path = vi.listen(output_filename="test_audio.wav")
    if path:
        text = vi.transcribe(path)
        print(f"You said: {text}")

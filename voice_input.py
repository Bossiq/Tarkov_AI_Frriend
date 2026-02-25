"""
Voice Input — microphone capture with adaptive VAD + local transcription.

Key design:
  • Ring buffer keeps audio BEFORE speech confirmed (preserves first word)
  • Adaptive silence: tracks speech volume, silence = big drop from peak
  • Robust: uses volume-relative silence detection, not absolute threshold
  • Transcription via faster-whisper (local, offline)
"""

import logging
import os
import time
import threading
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────
_SAMPLERATE = 16_000
_CHANNELS = 1
_CHUNK_SECONDS = 0.1
_SILENCE_DURATION = 1.2           # 1.2s quiet = done talking
_MAX_RECORDING_SECONDS = 15       # Hard cap — prevents infinite recording
_CALIBRATION_SECONDS = 1.0
_NOISE_MULTIPLIER = 3.0           # Higher = less sensitive to noise
_MIN_SPEECH_CHUNKS = 2
_PRE_BUFFER_CHUNKS = 6            # 600ms pre-speech
_SILENCE_FACTOR = 0.4             # Silence = RMS drops to 40% of speech avg
_DEFAULT_WHISPER_MODEL = "base"
_DEFAULT_COMPUTE_TYPE = "int8"


class VoiceInput:
    """Mic capture with adaptive VAD and local Whisper transcription."""

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
        self._whisper_model = None
        self._whisper_lock = threading.Lock()

    # ── Whisper model loading ─────────────────────────────────────────
    def _get_whisper_model(self):
        if self._whisper_model is not None:
            return self._whisper_model
        with self._whisper_lock:
            if self._whisper_model is not None:
                return self._whisper_model
            model_name = os.getenv("WHISPER_MODEL", _DEFAULT_WHISPER_MODEL)
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", _DEFAULT_COMPUTE_TYPE)
            if self._gui_log:
                self._gui_log(f"🤖 Loading speech model ({model_name}) …")
            logger.info("Loading faster-whisper '%s' (compute=%s) …", model_name, compute_type)
            from faster_whisper import WhisperModel
            self._whisper_model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
            if self._gui_log:
                self._gui_log("🤖 Speech model loaded ✅")
            logger.info("Whisper model loaded")
            return self._whisper_model

    # ── Noise calibration ─────────────────────────────────────────────
    def calibrate(self, gui_log=None) -> float:
        if gui_log:
            gui_log("🔊 Calibrating mic — stay quiet for 1 second …")
        logger.info("Calibrating ambient noise …")
        samples = []
        num_chunks = int(_CALIBRATION_SECONDS / _CHUNK_SECONDS)
        try:
            with sd.InputStream(
                samplerate=self.samplerate, channels=self.channels, dtype="float32",
            ) as stream:
                for _ in range(num_chunks):
                    chunk, _ = stream.read(self._chunk_size)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    samples.append(rms)
        except Exception:
            logger.exception("Calibration failed")
            self._threshold = 0.03
            return self._threshold

        ambient = float(np.mean(samples)) if samples else 0.005
        self._threshold = max(ambient * _NOISE_MULTIPLIER, 0.015)
        logger.info("Ambient: %.4f → Threshold: %.4f", ambient, self._threshold)
        if gui_log:
            gui_log(f"🔊 Calibrated (threshold: {self._threshold:.3f})")

        # Pre-load Whisper
        threading.Thread(target=self._get_whisper_model, name="WhisperLoad", daemon=True).start()
        return self._threshold

    # ── Main listening loop ───────────────────────────────────────────
    def listen(self, output_filename: str = "temp_audio.wav") -> Optional[str]:
        """Block until speech detected, recorded, and speaker goes quiet.

        Uses:
          1. Ring buffer to keep pre-speech audio (first word preserved)
          2. Adaptive silence: tracks average speech volume, considers
             silence when RMS drops to 30% of running speech average
        """
        if self._threshold is None:
            self.calibrate()

        logger.info("Listening (threshold=%.4f) …", self._threshold)

        pre_buffer: deque = deque(maxlen=_PRE_BUFFER_CHUNKS)
        recorded_frames: list[np.ndarray] = []
        is_recording = False
        silence_start: Optional[float] = None
        recording_start: Optional[float] = None
        consecutive_loud = 0
        speech_rms_sum = 0.0
        speech_rms_count = 0

        try:
            with sd.InputStream(
                samplerate=self.samplerate, channels=self.channels, dtype="float32",
            ) as stream:
                while not self._shutdown.is_set():
                    chunk, _ = stream.read(self._chunk_size)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))

                    if rms > self._threshold:
                        consecutive_loud += 1
                        silence_start = None

                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                            if consecutive_loud >= _MIN_SPEECH_CHUNKS:
                                logger.info("Speech detected — recording …")
                                is_recording = True
                                recording_start = time.monotonic()
                                recorded_frames.extend(list(pre_buffer))
                                pre_buffer.clear()
                        else:
                            recorded_frames.append(chunk.copy())
                            # Track speech volume for adaptive silence
                            speech_rms_sum += rms
                            speech_rms_count += 1

                    else:
                        consecutive_loud = 0
                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                        else:
                            recorded_frames.append(chunk.copy())

                            # Adaptive silence check:
                            # Consider it "silence" if RMS dropped significantly
                            # from the average speech volume
                            speech_avg = (speech_rms_sum / speech_rms_count
                                          if speech_rms_count > 0 else self._threshold)
                            silence_threshold = speech_avg * _SILENCE_FACTOR
                            is_quiet = rms < max(silence_threshold, self._threshold * 0.8)

                            if is_quiet:
                                if silence_start is None:
                                    silence_start = time.monotonic()
                                elif time.monotonic() - silence_start > self.silence_duration:
                                    logger.debug("Silence reached — done")
                                    break
                            else:
                                silence_start = None

                    # Safety cap
                    if (is_recording and recording_start
                            and time.monotonic() - recording_start > self.max_duration):
                        logger.warning("Max duration (%ss) reached", self.max_duration)
                        break

        except sd.PortAudioError:
            logger.exception("Mic error")
            return None
        except Exception:
            logger.exception("Audio capture error")
            return None

        if not recorded_frames:
            return None

        audio = np.concatenate(recorded_frames, axis=0)
        sf.write(output_filename, audio, self.samplerate)
        duration = len(audio) / self.samplerate
        logger.info("Recorded %.1fs → %s", duration, output_filename)
        return output_filename

    # ── Transcription ─────────────────────────────────────────────────
    def transcribe(self, audio_path: str) -> Optional[str]:
        start = time.monotonic()
        try:
            model = self._get_whisper_model()
            segments, _ = model.transcribe(
                audio_path, language="en", beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=300),
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            logger.info("Transcribed in %.1fs: '%s'", time.monotonic() - start, text[:80])
            return text if text else None
        except Exception:
            logger.exception("Transcription failed")
            return None


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    vi = VoiceInput()
    vi.calibrate()
    print("Speak now …")
    path = vi.listen()
    if path:
        print(f"You said: {vi.transcribe(path)}")

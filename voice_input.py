"""
Voice input — mic capture with adaptive VAD + local Whisper STT.

Uses CALLBACK-BASED audio capture to prevent blocking.
The `stream.read()` approach can block indefinitely when mic hardware
stalls after TTS playback. This version uses a callback that puts
chunks into a queue, with `queue.get(timeout=0.2)` ensuring the
main loop always progresses and timeout checks fire reliably.
"""

import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
_SAMPLERATE = 16_000
_CHANNELS = 1
_CHUNK_SECONDS = 0.1
_SILENCE_DURATION = 1.2
_MAX_RECORDING_SECONDS = 15
_CALIBRATION_SECONDS = 1.0
_NOISE_MULTIPLIER = 3.0
_MIN_SPEECH_CHUNKS = 2
_PRE_BUFFER_CHUNKS = 6
_SILENCE_FACTOR = 0.4
_QUEUE_TIMEOUT = 0.2             # Never block longer than 200ms
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
        self._chunk_size = int(samplerate * _CHUNK_SECONDS)
        self._threshold: Optional[float] = None
        self._whisper_model = None
        self._whisper_lock = threading.Lock()
        self._audio_q: queue.Queue = queue.Queue()

    def _get_whisper_model(self):
        if self._whisper_model is None:
            with self._whisper_lock:
                if self._whisper_model is None:
                    model = os.getenv("WHISPER_MODEL", _DEFAULT_WHISPER_MODEL)
                    compute = os.getenv("WHISPER_COMPUTE_TYPE", _DEFAULT_COMPUTE_TYPE)
                    logger.info("Loading faster-whisper '%s' (compute=%s) …", model, compute)
                    from faster_whisper import WhisperModel
                    # Use CUDA on Windows/Linux if available, else CPU
                    device = os.getenv("WHISPER_DEVICE", "auto")
                    if device == "auto":
                        try:
                            import torch
                            device = "cuda" if torch.cuda.is_available() else "cpu"
                        except ImportError:
                            device = "cpu"
                    self._whisper_model = WhisperModel(
                        model, device=device, compute_type=compute)
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

        threading.Thread(target=self._get_whisper_model, name="WhisperLoad", daemon=True).start()
        return self._threshold

    # ── Audio callback (runs on OS audio thread) ──────────────────────
    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block. Never blocks."""
        if status:
            logger.debug("Audio status: %s", status)
        self._audio_q.put(indata.copy())

    # ── Main listening loop (CALLBACK-BASED — never blocks) ───────────
    def listen(self, output_filename: str = "temp_audio.wav") -> Optional[str]:
        """Block until speech detected, recorded, and speaker goes quiet.

        Uses callback-based audio capture with queue.get(timeout) to ensure
        timeout safety checks ALWAYS fire, even if mic hardware stalls.
        """
        if self._threshold is None:
            self.calibrate()

        logger.info("Listening (threshold=%.4f) …", self._threshold)

        # Clear any stale audio from previous session
        while not self._audio_q.empty():
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break

        pre_buffer: deque = deque(maxlen=_PRE_BUFFER_CHUNKS)
        recorded_frames: list[np.ndarray] = []
        is_recording = False
        silence_start: Optional[float] = None
        recording_start: Optional[float] = None
        consecutive_loud = 0
        speech_rms_sum = 0.0
        speech_rms_count = 0
        listen_start = time.monotonic()

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
                blocksize=self._chunk_size,
                callback=self._audio_callback,
            ):
                while not self._shutdown.is_set():
                    # Non-blocking: wait at most 200ms for audio
                    try:
                        chunk = self._audio_q.get(timeout=_QUEUE_TIMEOUT)
                    except queue.Empty:
                        # No audio arrived — check timeouts anyway
                        if is_recording and recording_start:
                            elapsed = time.monotonic() - recording_start
                            if elapsed > self.max_duration:
                                logger.warning("Max duration (%ss) — no audio", self.max_duration)
                                break
                        # Don't hang forever waiting for speech either
                        wait_elapsed = time.monotonic() - listen_start
                        if not is_recording and wait_elapsed > 120:
                            logger.warning("No speech for 120s — recalibrating")
                            return None
                        # Periodic feedback every 30s
                        if not is_recording and int(wait_elapsed) % 30 == 0 and int(wait_elapsed) > 0:
                            logger.info("Still listening (%.0fs)…", wait_elapsed)
                        continue

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
                            speech_rms_sum += rms
                            speech_rms_count += 1
                    else:
                        consecutive_loud = 0
                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                        else:
                            recorded_frames.append(chunk.copy())

                            speech_avg = (speech_rms_sum / speech_rms_count
                                          if speech_rms_count > 0 else self._threshold)
                            silence_threshold = speech_avg * _SILENCE_FACTOR
                            is_quiet = rms < max(silence_threshold,
                                                 self._threshold * 0.8)

                            if is_quiet:
                                if silence_start is None:
                                    silence_start = time.monotonic()
                                elif time.monotonic() - silence_start > self.silence_duration:
                                    logger.debug("Silence reached — done")
                                    break
                            else:
                                silence_start = None

                    # Safety cap (ALWAYS fires, even if queue was full)
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

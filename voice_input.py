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
_SILENCE_DURATION = 1.8          # Wait longer before ending (was 1.2 — too fast)
_MAX_RECORDING_SECONDS = 30      # Allow longer utterances
_CALIBRATION_SECONDS = 1.0
_NOISE_MULTIPLIER = 2.5          # Less aggressive threshold (was 3.0)
_MIN_SPEECH_CHUNKS = 2
_PRE_BUFFER_CHUNKS = 10          # Capture more speech start (was 6)
_SILENCE_FACTOR = 0.35           # Slightly more sensitive silence detection
_QUEUE_TIMEOUT = 0.2             # Never block longer than 200ms
_DEFAULT_WHISPER_MODEL = "small"
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
                    device = os.getenv("WHISPER_DEVICE", "auto")

                    # Auto-detect: use CUDA if available, else CPU
                    if device == "auto":
                        try:
                            import ctranslate2
                            device = "cuda" if "cuda" in ctranslate2.get_supported_compute_types("cuda") else "cpu"
                        except Exception:
                            device = "cpu"

                    # Force safe compute type on CPU
                    if device == "cpu" and compute == "float16":
                        logger.warning("float16 not supported on CPU — falling back to int8")
                        compute = "int8"

                    logger.info("Loading faster-whisper '%s' (device=%s, compute=%s) …", model, device, compute)
                    from faster_whisper import WhisperModel
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
            # Auto-detect language for multilingual support (EN/RU/RO).
            # Set WHISPER_LANGUAGE=en in .env to force English only.
            whisper_lang = os.getenv("WHISPER_LANGUAGE", "auto")
            lang_arg = None if whisper_lang == "auto" else whisper_lang

            segments, info = model.transcribe(
                audio_path, language=lang_arg, beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250),
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            detected = getattr(info, 'language', 'unknown')
            logger.info(
                "Transcribed in %.1fs [lang=%s]: '%s'",
                time.monotonic() - start, detected, text[:80]
            )
            return text if text else None
        except Exception:
            logger.exception("Transcription failed")
            return None

    # ══════════════════════════════════════════════════════════════════
    #  BARGE-IN MONITOR — parallel mic monitoring during TTS
    # ══════════════════════════════════════════════════════════════════
    # Runs on a background thread while the AI speaks. When the user
    # starts talking (sustained speech above threshold), it fires an
    # interrupt_event and captures the audio for immediate transcription.
    #
    # Filtering:
    #   • Energy > 3x ambient threshold (rejects quiet sounds)
    #   • Duration > 0.6s sustained (rejects clicks, laughs, coughs)
    #   • Records post-trigger audio for ~2s so STT gets a full phrase

    _BARGEIN_SUSTAINED_CHUNKS = 6   # 6 * 100ms = 0.6s sustained speech
    _BARGEIN_ENERGY_MULT = 3.0      # 3x ambient = clearly speaking
    _BARGEIN_POST_RECORD_S = 2.0    # record 2s after trigger for STT

    def start_bargein_monitor(
        self,
        interrupt_event: threading.Event,
        threshold: Optional[float] = None,
    ) -> None:
        """Start background mic monitor that fires interrupt_event on speech.

        Args:
            interrupt_event: Set this when sustained speech detected.
            threshold: Energy threshold (defaults to calibrated value * 3).
        """
        if self._threshold is None:
            self.calibrate()

        self._bargein_stop = threading.Event()
        self._bargein_audio: list[np.ndarray] = []
        self._bargein_triggered = False
        self._bargein_interrupt = interrupt_event

        bargein_threshold = (threshold or self._threshold) * self._BARGEIN_ENERGY_MULT

        def _monitor():
            consecutive_loud = 0
            triggered = False
            post_record_start = None
            q: queue.Queue = queue.Queue()

            def _cb(indata, frames, time_info, status):
                q.put(indata.copy())

            try:
                with sd.InputStream(
                    samplerate=self.samplerate,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=self._chunk_size,
                    callback=_cb,
                ):
                    while not self._bargein_stop.is_set():
                        try:
                            chunk = q.get(timeout=0.15)
                        except queue.Empty:
                            continue

                        rms = float(np.sqrt(np.mean(chunk ** 2)))

                        if triggered:
                            # Post-trigger: keep recording for STT
                            self._bargein_audio.append(chunk)
                            if (post_record_start and
                                    time.monotonic() - post_record_start > self._BARGEIN_POST_RECORD_S):
                                logger.debug("Barge-in: post-record complete")
                                break
                            # End early if silence returns
                            if rms < bargein_threshold * 0.5:
                                if post_record_start is None:
                                    post_record_start = time.monotonic()
                            else:
                                post_record_start = None
                        else:
                            if rms > bargein_threshold:
                                consecutive_loud += 1
                                self._bargein_audio.append(chunk)
                                if consecutive_loud >= self._BARGEIN_SUSTAINED_CHUNKS:
                                    logger.info(
                                        "Barge-in TRIGGERED (rms=%.4f, threshold=%.4f, "
                                        "chunks=%d)",
                                        rms, bargein_threshold, consecutive_loud,
                                    )
                                    triggered = True
                                    self._bargein_triggered = True
                                    interrupt_event.set()
                                    post_record_start = None
                            else:
                                consecutive_loud = 0
                                self._bargein_audio.clear()

            except Exception:
                logger.debug("Barge-in monitor mic error (expected during TTS)")

        self._bargein_thread = threading.Thread(
            target=_monitor, name="BargeInMonitor", daemon=True
        )
        self._bargein_thread.start()
        logger.debug("Barge-in monitor started (threshold=%.4f)", bargein_threshold)

    def stop_bargein_monitor(self) -> Optional[str]:
        """Stop the barge-in monitor. Returns audio path if speech was captured."""
        if not hasattr(self, '_bargein_stop'):
            return None

        self._bargein_stop.set()
        if hasattr(self, '_bargein_thread'):
            self._bargein_thread.join(timeout=2.0)

        if not self._bargein_triggered or not self._bargein_audio:
            return None

        # Save captured audio for transcription
        audio = np.concatenate(self._bargein_audio, axis=0)
        path = "bargein_capture.wav"
        sf.write(path, audio, self.samplerate)
        duration = len(audio) / self.samplerate
        logger.info("Barge-in audio: %.1fs -> %s", duration, path)
        return path


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    vi = VoiceInput()
    vi.calibrate()
    print("Speak now …")
    path = vi.listen()
    if path:
        print(f"You said: {vi.transcribe(path)}")

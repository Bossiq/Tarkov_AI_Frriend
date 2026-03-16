"""
Voice input — mic capture with Silero VAD + local Whisper STT.

Uses CALLBACK-BASED audio capture to prevent blocking.
The `stream.read()` approach can block indefinitely when mic hardware
stalls after TTS playback. This version uses a callback that puts
chunks into a queue, with `queue.get(timeout=0.2)` ensuring the
main loop always progresses and timeout checks fire reliably.

VAD Strategy (as of March 2026):
  • ONSET detection: Silero VAD neural model (snakers4/silero-vad)
    — 512-sample windows, ~1ms inference, far superior to RMS-based
  • END-OF-SPEECH: RMS energy drop detection (simpler, faster)
  • Fallback: if Silero is unavailable, uses the legacy RMS onset logic
"""

import logging
import os
import queue
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# ── Silero VAD (lazy-loaded, neural speech detection) ─────────────────
_silero_model = None
_silero_lock = threading.Lock()


def _get_silero_model():
    """Lazy-load the Silero VAD model (thread-safe, singleton)."""
    global _silero_model
    if _silero_model is not None:
        return _silero_model
    with _silero_lock:
        if _silero_model is not None:
            return _silero_model
        try:
            from silero_vad import load_silero_vad
            _silero_model = load_silero_vad()
            logger.info("Silero VAD loaded (neural onset detection)")
            return _silero_model
        except Exception:
            logger.warning("Silero VAD unavailable — using legacy RMS onset")
            return None


def _silero_speech_prob(model, chunk_16k: np.ndarray) -> float:
    """Get speech probability [0.0-1.0] from Silero for a 512-sample window.

    Silero requires exactly 512 samples at 16kHz.  If the chunk is larger,
    we take the last 512 samples (most recent audio).
    """
    import torch
    if len(chunk_16k) < 512:
        return 0.0
    # Take last 512 samples
    window = chunk_16k[-512:].flatten()
    tensor = torch.from_numpy(window).float()
    with torch.no_grad():
        prob = model(tensor, 16000).item()
    return prob


# ── Constants ────────────────────────────────────────────────────────
_SAMPLERATE = 16_000
_CHANNELS = 1
_CHUNK_SECONDS = 0.1
_END_OF_SPEECH_SILENCE = 1.2     # End-of-speech silence (give user time to breathe/pause)
_MAX_RECORDING_SECONDS = 30      # Allow longer utterances
_CALIBRATION_SECONDS = 1.0
_NOISE_MULTIPLIER = 2.5          # Legacy RMS onset sensitivity (fallback)
_MIN_SPEECH_CHUNKS = 3           # Consecutive speech chunks before recording
_PRE_BUFFER_CHUNKS = 10          # 1s of pre-speech audio (captures first syllable)
_SILENCE_FACTOR = 0.15           # End-of-speech = 15% of peak RMS
_QUEUE_TIMEOUT = 0.2             # Never block longer than 200ms
_RMS_WINDOW = 3                  # Sliding-window frames for RMS smoothing
_MIN_SPEECH_SECONDS = 0.8        # Ignore speech shorter than this
_TRIM_TRAILING_SILENCE = True    # Trim dead air from end of recording
_DEFAULT_WHISPER_MODEL = "small"
_DEFAULT_COMPUTE_TYPE = "int8"
_SPECTRAL_FLATNESS_THRESHOLD = 0.85  # Legacy: reject flat-spectrum noise
_POST_TTS_COOLDOWN = 0.5        # Seconds to ignore audio after TTS stops
_SILERO_ONSET_THRESHOLD = 0.5   # Silero speech probability for onset (0.0-1.0)
_SILERO_BARGEIN_THRESHOLD = 0.7 # Higher bar for barge-in (avoid TTS echo triggers)

# Common Whisper hallucinations to reject
_WHISPER_HALLUCINATIONS = {
    ".", "..", "...", "…",
    "thank you.", "thanks.", "thank you", "thanks",
    "thank you for watching.", "thanks for watching.",
    "subtitles by", "subtitled by",
    "subscribe", "like and subscribe",
    "you", "bye.", "bye", "the end.", "the end",
    "so", "i", "hmm", "huh", "uh", "um",
    "mulțumesc.", "mulțumesc", "la revedere.",
    "спасибо.", "спасибо", "до свидания.",
}


def _spectral_flatness(chunk: np.ndarray) -> float:
    """Compute spectral flatness (0=tonal/speech, 1=flat/noise).

    Chair squeaks, clicks, and other non-speech transients have a
    flat spectral profile.  Human speech has formant peaks, giving
    low flatness.  Threshold of ~0.85 separates them.
    """
    spectrum = np.abs(np.fft.rfft(chunk.flatten()))
    # Avoid log(0)
    spectrum = np.maximum(spectrum, 1e-10)
    geo_mean = np.exp(np.mean(np.log(spectrum)))
    arith_mean = np.mean(spectrum)
    if arith_mean < 1e-10:
        return 1.0
    return float(geo_mean / arith_mean)


class VoiceInput:
    """Mic capture with Silero VAD neural onset detection + Whisper STT."""

    def __init__(
        self,
        samplerate: int = _SAMPLERATE,
        channels: int = _CHANNELS,
        silence_duration: float = _END_OF_SPEECH_SILENCE,
        max_duration: float = _MAX_RECORDING_SECONDS,
        shutdown_event: Optional[threading.Event] = None,
        gui_log=None,
        device: Optional[int] = None,
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
        self.device: Optional[int] = device
        self._bargein_frames: list[np.ndarray] = []
        self._bargein_detected = threading.Event()

        # ── Silero VAD (loaded in background) ──────────────────────
        self._silero_model = None
        threading.Thread(target=self._load_silero, name="SileroLoad", daemon=True).start()

    def _load_silero(self):
        """Load Silero VAD model in a background thread."""
        self._silero_model = _get_silero_model()

    # ── Device enumeration & switching ────────────────────────────────
    @staticmethod
    def list_input_devices() -> list[tuple[int, str]]:
        """Return a list of (index, name) for all input-capable audio devices."""
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append((i, d["name"]))
        return result

    def set_device(self, device_index: Optional[int], gui_log=None) -> None:
        """Switch to a different input device and recalibrate.

        Keeps the old threshold while recalibrating so the listen loop
        never sees ``None`` in a concurrent comparison.
        """
        self.device = device_index
        name = "Default" if device_index is None else sd.query_devices(device_index)["name"]
        logger.info("Mic device changed → %s (index=%s)", name, device_index)
        # Keep old threshold during recalibration — prevents NoneType crash
        # in concurrent listen() loop.  calibrate() will overwrite it.
        self.calibrate(gui_log=gui_log)

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
                device=self.device,
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
    def listen(self, output_filename: str = "temp_audio.wav",
               assume_speaking: bool = False) -> Optional[str]:
        """Block until speech detected, recorded, and speaker goes quiet.

        Uses callback-based audio capture with queue.get(timeout) to ensure
        timeout safety checks ALWAYS fire, even if mic hardware stalls.

        Args:
            assume_speaking: If True, skip onset detection and start recording
                immediately.  Used after barge-in when the user is already
                mid-sentence.

        Detection strategy:
          • RAW RMS for speech onset   — instant, no smoothing delay
          • SMOOTHED RMS for silence   — 3-frame window resists noise spikes
          • Failsafe absolute-silence  — raw RMS near-zero for 0.4s = done
          • Minimum speech guard       — 0.5s prevents coughs/clicks
          • Trailing silence trim       — cleaner Whisper input
        """
        if self._threshold is None:
            self.calibrate()

        # Clear any stale audio from previous session
        while not self._audio_q.empty():
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break

        pre_buffer: deque = deque(maxlen=_PRE_BUFFER_CHUNKS)
        rms_window: deque = deque(maxlen=_RMS_WINDOW)  # sliding RMS for silence only
        recorded_frames: list[np.ndarray] = []
        is_recording = assume_speaking
        silence_start: Optional[float] = None
        abs_silence_start: Optional[float] = None    # failsafe near-zero detector
        recording_start: Optional[float] = time.monotonic() if assume_speaking else None
        consecutive_loud = 0
        peak_rms = 0.0
        speech_rms_sum = 0.0
        speech_rms_count = 0
        listen_start = time.monotonic()

        if assume_speaking:
            logger.info("Listening after barge-in (threshold=%.4f) — recording immediately …",
                        self._threshold)
        else:
            logger.info("Listening (threshold=%.4f) …", self._threshold)

        # Absolute-silence threshold: anything below this is "dead air"
        # (set to 20% of the onset threshold — for a 0.015 threshold this = 0.003)
        abs_silence_threshold = self._threshold * 0.2
        abs_silence_timeout = 0.7  # 700ms of dead air = guaranteed end

        # After barge-in, don't trigger end-of-speech until we hear actual
        # speech in this new session.  This prevents the silence detectors
        # from firing during the transition gap.
        heard_voice = not assume_speaking  # True in normal mode, False after barge-in

        # After barge-in, prepend the audio captured by the monitor so
        # the beginning of the user's speech is not lost.
        if assume_speaking and self._bargein_frames:
            recorded_frames.extend(self._bargein_frames)
            for frame in self._bargein_frames:
                rms = float(np.sqrt(np.mean(frame ** 2)))
                if rms > self._threshold:
                    speech_rms_sum += rms
                    speech_rms_count += 1
                    if rms > peak_rms:
                        peak_rms = rms
            logger.info("Prepended %d barge-in frames (%.1fs)",
                        len(self._bargein_frames),
                        len(self._bargein_frames) * self._chunk_size / self.samplerate)
            self._bargein_frames = []
            self._bargein_detected.clear()
            heard_voice = True  # we already confirmed speech in the monitor
            recording_start = time.monotonic()  # reset for end-of-speech timing

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
                blocksize=self._chunk_size,
                callback=self._audio_callback,
                device=self.device,
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
                        wait_elapsed = time.monotonic() - listen_start
                        if not is_recording and wait_elapsed > 120:
                            logger.warning("No speech for 120s — recalibrating")
                            return None
                        if not is_recording and int(wait_elapsed) % 30 == 0 and int(wait_elapsed) > 0:
                            logger.info("Still listening (%.0fs)…", wait_elapsed)
                        continue

                    raw_rms = float(np.sqrt(np.mean(chunk ** 2)))

                    # Smoothed RMS (for silence detection ONLY)
                    rms_window.append(raw_rms)
                    smoothed_rms = float(np.mean(rms_window))

                    # ── ONSET: Silero VAD (neural) with RMS fallback ──
                    # Silero gives a speech probability [0.0-1.0].
                    # If unavailable, fall back to legacy RMS + spectral flatness.
                    is_speech_chunk = False
                    if self._silero_model is not None:
                        # Neural onset: much more accurate than RMS
                        prob = _silero_speech_prob(self._silero_model, chunk)
                        is_speech_chunk = prob > _SILERO_ONSET_THRESHOLD
                    else:
                        # Legacy fallback: RMS + spectral flatness
                        if raw_rms > (self._threshold or 0.015):
                            if not is_recording:
                                flatness = _spectral_flatness(chunk)
                                if flatness > _SPECTRAL_FLATNESS_THRESHOLD:
                                    pre_buffer.append(chunk.copy())
                                    continue
                            is_speech_chunk = True

                    if is_speech_chunk:
                        consecutive_loud += 1
                        silence_start = None
                        abs_silence_start = None

                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                            if consecutive_loud >= _MIN_SPEECH_CHUNKS:
                                vad_label = "Silero" if self._silero_model else "RMS"
                                logger.info(
                                    "Speech detected [%s] (rms=%.4f) — recording …",
                                    vad_label, raw_rms,
                                )
                                is_recording = True
                                recording_start = time.monotonic()
                                recorded_frames.extend(list(pre_buffer))
                                pre_buffer.clear()
                        else:
                            recorded_frames.append(chunk.copy())
                            speech_rms_sum += raw_rms
                            speech_rms_count += 1
                            if raw_rms > peak_rms:
                                peak_rms = raw_rms
                            # After barge-in, mark that we've heard real speech
                            if not heard_voice and consecutive_loud >= _MIN_SPEECH_CHUNKS:
                                heard_voice = True
                                recording_start = time.monotonic()
                                logger.info("Post-barge-in speech confirmed (rms=%.4f)", raw_rms)
                    else:
                        consecutive_loud = 0
                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                        else:
                            recorded_frames.append(chunk.copy())

                            speech_elapsed = (time.monotonic() - recording_start
                                              if recording_start else 0)

                            # Don't check for end-of-speech until we've
                            # heard real speech (critical after barge-in)
                            if not heard_voice:
                                continue

                            # ── FAILSAFE: absolute silence (near-zero) ─
                            # If raw energy is basically zero, we're done.
                            # This fires even if smoothed_rms hasn't
                            # caught up yet.
                            if raw_rms < abs_silence_threshold:
                                if abs_silence_start is None:
                                    abs_silence_start = time.monotonic()
                                elif (time.monotonic() - abs_silence_start
                                      > abs_silence_timeout
                                      and speech_elapsed >= _MIN_SPEECH_SECONDS):
                                    logger.info(
                                        "End-of-speech [failsafe] "
                                        "(dead_air=%.2fs, speech=%.1fs)",
                                        time.monotonic() - abs_silence_start,
                                        speech_elapsed,
                                    )
                                    break
                            else:
                                abs_silence_start = None

                            # ── PRIMARY: energy-drop silence (smoothed) ─
                            speech_avg = (speech_rms_sum / speech_rms_count
                                          if speech_rms_count > 0
                                          else self._threshold)
                            silence_threshold = max(
                                peak_rms * _SILENCE_FACTOR,
                                speech_avg * _SILENCE_FACTOR,
                                self._threshold * 0.6,
                            )
                            is_quiet = smoothed_rms < silence_threshold

                            if is_quiet:
                                if silence_start is None:
                                    silence_start = time.monotonic()
                                else:
                                    silence_elapsed = (time.monotonic()
                                                       - silence_start)
                                    if (silence_elapsed > self.silence_duration
                                            and speech_elapsed
                                            >= _MIN_SPEECH_SECONDS):
                                        logger.info(
                                            "End-of-speech "
                                            "(silence=%.2fs, speech=%.1fs, "
                                            "peak=%.4f, thr=%.4f)",
                                            silence_elapsed, speech_elapsed,
                                            peak_rms, silence_threshold,
                                        )
                                        break
                            else:
                                silence_start = None

                    # Safety cap
                    if (is_recording and recording_start
                            and time.monotonic() - recording_start
                            > self.max_duration):
                        logger.warning("Max duration (%ss) reached",
                                       self.max_duration)
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

        # ── Trim trailing silence ─────────────────────────────────────
        if _TRIM_TRAILING_SILENCE and len(audio) > self.samplerate:
            trim_samples = int(self.silence_duration * self.samplerate)
            # Walk backwards to find where energy last exceeded threshold
            frame_size = self._chunk_size
            end = len(audio)
            earliest_trim = max(0, end - trim_samples)
            while end > earliest_trim + frame_size:
                frame = audio[end - frame_size:end]
                frame_rms = float(np.sqrt(np.mean(frame ** 2)))
                if frame_rms > self._threshold * 0.8:
                    break
                end -= frame_size
            # Keep a tiny tail (50ms) so Whisper doesn't clip the last word
            tail_pad = int(0.05 * self.samplerate)
            end = min(len(audio), end + tail_pad)
            audio = audio[:end]

        # ── Noise reduction (spectral gating) ─────────────────────────
        try:
            import noisereduce as nr
            # Use first 0.5s as noise profile (captured during onset detection)
            noise_len = min(int(0.5 * self.samplerate), len(audio) // 4)
            noise_clip = audio[:noise_len]
            audio = nr.reduce_noise(
                y=audio.flatten(),
                sr=self.samplerate,
                y_noise=noise_clip.flatten(),
                prop_decrease=0.75,   # reduce noise by 75% (keep some naturalness)
                stationary=True,      # good for constant background noise
            )
            audio = audio.reshape(-1, 1)  # restore shape for soundfile
            logger.debug("Noise reduction applied (profile=%.1fs)", noise_len / self.samplerate)
        except ImportError:
            pass  # noisereduce not installed — skip
        except Exception:
            logger.debug("Noise reduction failed — using raw audio", exc_info=True)

        sf.write(output_filename, audio, self.samplerate)
        duration = len(audio) / self.samplerate
        logger.info("Recorded %.1fs → %s", duration, output_filename)
        return output_filename

    # ── Barge-in: speech monitor with audio capture ─────────────────────
    def monitor_for_speech(self, stop_event: threading.Event) -> bool:
        """Watch the mic for speech onset during TTS playback.

        Uses a HIGHER threshold than normal listening (4x) to avoid
        false triggers from TTS audio bleeding through the microphone
        (acoustic echo).  Also requires more consecutive chunks (5) and
        applies a spectral flatness check to distinguish real speech from
        speaker playback.

        When speech is detected, continues capturing audio into
        ``_bargein_frames`` until *stop_event* is set.  The next call
        to ``listen(assume_speaking=True)`` will prepend those frames
        so no audio is lost during the transition.

        Returns True if speech was detected, False otherwise.
        """
        if self._threshold is None:
            self.calibrate()

        # Barge-in needs a MUCH higher bar than normal listening:
        # TTS playing through speakers typically creates mic RMS of
        # 0.02–0.06 — well above the 0.015 noise threshold.
        # 4x ensures only real, close-range speech triggers it.
        bargein_threshold = max((self._threshold or 0.015) * 4.0, 0.05)
        bargein_required = 5  # 500ms of confirmed speech

        monitor_q: queue.Queue = queue.Queue()
        pre_chunks: list[np.ndarray] = []  # chunks that triggered onset

        def _cb(indata, frames, time_info, status):
            monitor_q.put(indata.copy())

        consecutive_loud = 0
        detected = False

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
                blocksize=self._chunk_size,
                callback=_cb,
                device=self.device,
            ):
                while not stop_event.is_set() and not self._shutdown.is_set():
                    try:
                        chunk = monitor_q.get(timeout=_QUEUE_TIMEOUT)
                    except queue.Empty:
                        continue

                    if not detected:
                        # ── Phase 1: detect speech onset ──────────
                        raw_rms = float(np.sqrt(np.mean(chunk ** 2)))
                        if raw_rms > bargein_threshold:
                            # Spectral flatness check: speaker echo has
                            # flat spectrum, real speech has formant peaks
                            flatness = _spectral_flatness(chunk)
                            if flatness > _SPECTRAL_FLATNESS_THRESHOLD:
                                consecutive_loud = 0
                                pre_chunks.clear()
                                continue

                            consecutive_loud += 1
                            pre_chunks.append(chunk)
                            if consecutive_loud >= bargein_required:
                                logger.info(
                                    "Barge-in speech detected (rms=%.4f, thr=%.4f, flat=%.2f)",
                                    raw_rms, bargein_threshold, flatness,
                                )
                                detected = True
                                self._bargein_frames = list(pre_chunks)
                                self._bargein_detected.set()
                        else:
                            consecutive_loud = 0
                            pre_chunks.clear()
                    else:
                        # ── Phase 2: keep capturing until told to stop ─
                        self._bargein_frames.append(chunk)

        except Exception:
            logger.exception("Barge-in monitor error")

        if detected:
            duration = len(self._bargein_frames) * self._chunk_size / self.samplerate
            logger.info("Barge-in captured %.1fs of audio for next listen()", duration)

        return detected

    # ── Transcription ─────────────────────────────────────────────────
    def transcribe(self, audio_path: str) -> Optional[tuple[str, str]]:
        """Transcribe audio file. Returns (text, detected_language) or None."""
        start = time.monotonic()
        try:
            # ── Short audio guard ─────────────────────────────────────
            import soundfile as _sf
            audio_info = _sf.info(audio_path)
            if audio_info.duration < 0.6:
                logger.info("Audio too short (%.2fs) — skipping transcription", audio_info.duration)
                return None

            # ── Try Groq cloud first (fast + accurate) ────────────────
            groq_key = os.getenv("GROQ_API_KEY", "").strip()
            if groq_key:
                result = self._transcribe_groq(audio_path, groq_key, start)
                if result is not None:
                    result = self._filter_hallucination(result)
                    if result is not None:
                        return result
                logger.warning("Groq transcription failed or hallucination — falling back to local Whisper")

            # ── Fallback: local Whisper ────────────────────────────────
            result = self._transcribe_local(audio_path, start)
            if result is not None:
                result = self._filter_hallucination(result)
            return result

        except Exception:
            logger.exception("Transcription failed")
            return None

    @staticmethod
    def _filter_hallucination(result: tuple[str, str]) -> Optional[tuple[str, str]]:
        """Reject common Whisper hallucinations (e.g. '...', 'Thank you.')."""
        text, lang = result
        cleaned = text.strip().lower()
        if cleaned in _WHISPER_HALLUCINATIONS:
            logger.info("Rejected Whisper hallucination: '%s'", text)
            return None
        # Reject text that is ONLY punctuation or whitespace
        if not any(c.isalnum() for c in cleaned):
            logger.info("Rejected punctuation-only transcription: '%s'", text)
            return None
        # Reject very short transcriptions (< 2 real words)
        words = [w for w in cleaned.split() if len(w) > 1]
        if len(words) < 1:
            logger.info("Rejected too-short transcription: '%s'", text)
            return None
        return (text, lang)

    def _transcribe_groq(self, audio_path: str, api_key: str,
                         start: float) -> Optional[tuple[str, str]]:
        """Transcribe via Groq cloud API (whisper-large-v3-turbo)."""
        try:
            import groq
            client = groq.Groq(api_key=api_key)

            whisper_lang = os.getenv("WHISPER_LANGUAGE", "auto")
            lang_arg = None if whisper_lang == "auto" else whisper_lang

            with open(audio_path, "rb") as f:
                # Build a Whisper prompt based on language setting.
                # Romanian prompt helps accuracy when lang=ro but would
                # bias English/auto detection towards Romanian.
                whisper_prompt = "Escape from Tarkov, PMC, raid, loot, quest."
                if lang_arg == "ro":
                    whisper_prompt += (
                        " Salut frate, ce faci, hai să mergem,"
                        " ești gata de raid."
                    )
                elif lang_arg == "ru":
                    whisper_prompt += " Привет, братан, го в рейд."

                kwargs = {
                    "model": "whisper-large-v3-turbo",
                    "file": ("audio.wav", f, "audio/wav"),
                    "response_format": "verbose_json",
                    "prompt": whisper_prompt,
                }
                if lang_arg:
                    kwargs["language"] = lang_arg

                transcription = client.audio.transcriptions.create(**kwargs)

            text = transcription.text.strip() if transcription.text else ""
            raw_lang = getattr(transcription, 'language', lang_arg or 'en')
            # Normalize Groq's full language names to ISO codes
            # (Groq returns 'English', 'Romanian', etc. but our system uses 'en', 'ro')
            _LANG_NORM = {
                'english': 'en', 'romanian': 'ro', 'russian': 'ru',
                'en': 'en', 'ro': 'ro', 'ru': 'ru',
            }
            detected = _LANG_NORM.get(raw_lang.lower().strip(), raw_lang[:2].lower())
            elapsed = time.monotonic() - start

            logger.info(
                "Groq transcribed in %.1fs [lang=%s]: '%s'",
                elapsed, detected, text[:80]
            )

            return (text, detected) if text else None

        except Exception:
            logger.exception("Groq STT error")
            return None

    def _transcribe_local(self, audio_path: str,
                          start: float) -> Optional[tuple[str, str]]:
        """Transcribe via local faster-whisper model."""
        try:
            model = self._get_whisper_model()
            whisper_lang = os.getenv("WHISPER_LANGUAGE", "auto")
            lang_arg = None if whisper_lang == "auto" else whisper_lang
            lang_forced = lang_arg is not None
            if lang_forced:
                logger.debug("Language forced to: %s", lang_arg)

            # Prime Whisper with Tarkov-specific vocabulary so it
            # recognises game terms instead of guessing common words
            # (e.g. "Escape from Tarkov" not "this Q from Tarkov").
            # Covers: game terms, 1.0 content, maps, weapons, bosses,
            # traders, items, mechanics, EFT slang, and Twitch lingo.
            initial_prompt = (
                # ── Core game terminology (language-neutral) ──
                "Escape from Tarkov, Tarkov, EFT, Battlestate Games, BSG, Nikita, "
                "PMC, Scav, Scavs, player Scav, USEC, BEAR, "

                # ── Maps ──
                "Customs, Woods, Interchange, Shoreline, Reserve, Labs, "
                "Streets of Tarkov, Streets, Lighthouse, Ground Zero, Factory, "
                "Terminal, Harbour, Icebreaker, End of Line, "

                # ── Bosses ──
                "Reshala, Killa, Shturman, Sanitar, Tagilla, Glukhar, Kaban, "
                "Knight, Birdeye, Big Pipe, The Partisan, "

                # ── Traders ──
                "Prapor, Therapist, Skier, Peacekeeper, Mechanic, Ragman, "
                "Jaeger, Ref, Fence, BTR Driver, Lightkeeper, "

                # ── Weapons ──
                "AK-74, AK-74N, AKS-74U, AK-12, AK-103, AK-104, AK-308, "
                "M4A1, M16, HK 416, MDR, SCAR, SA-58, "
                "Mosin, DVL, SV-98, SVD, RSASS, SR-25, M1A, "
                "MP5, MP7, MPX, UMP, PP-19, Vector, P90, "
                "AS VAL, VSS Vintorez, RPK, RPK-16, PKM, "
                "Saiga-12, MP-133, TOZ, KS-23, Glock, Desert Eagle, "

                # ── Ammo ──
                "M995, M856A1, M855A1, M61, M62, M80, "
                "BS ammo, BT ammo, BP ammo, SNB, LPS Gzh, "
                "AP-20, Flechette, Magnum buckshot, "

                # ── Items ──
                "LEDX, Salewa, IFAK, morphine, CMS kit, Surv12, "
                "Red Rebel, SICC case, docs case, keytool, "
                "graphics card, bitcoin, moonshine, flash drive, dogtags, "
                "Kappa container, Epsilon container, Gamma container, "

                # ── Gameplay ──
                "flea market, hideout, stash, Bitcoin Farm, Scav Case, "
                "extracts, exfil, extraction point, V-Ex, "
                "quest, task, raid, wipe, extract, PMC run, Scav run, "
                "found in raid, FIR, PvP, PvE, co-op, "
                "chad, rat, extract camper, head-eyes, one-tap, "
                "juiced, kitted, thicc, meta, loot run, "

                # ── Twitch ──
                "Twitch, stream, streamer, chat, Twitch chat, "
                "subscriber, sub, donation, dono, bits, raid, follow, "
                "clip, emote, OBS, overlay, "

                # ── Romanian greetings (brief — avoids detection bias) ──
                "Salut, bună ziua, ce faci, mulțumesc, "
                "hai să mergem, frate, sunt gata, ești gata, "

                # ── Russian greetings (brief — avoids detection bias) ──
                "Привет, здарова, как дела, спасибо, "
                "давай, погнали, братан, го в рейд"
            )

            segments, info = model.transcribe(
                audio_path, language=lang_arg, beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250),
                initial_prompt=initial_prompt,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            detected = getattr(info, 'language', 'unknown')
            probability = getattr(info, 'language_probability', 0.0)
            logger.info(
                "Transcribed in %.1fs [lang=%s, conf=%.0f%%]: '%s'",
                time.monotonic() - start, detected, probability * 100, text[:80]
            )

            # ── Confidence filtering ──────────────────────────────────
            if probability < 0.5:
                logger.warning(
                    "Low confidence (%.0f%%) — rejecting transcription: '%s'",
                    probability * 100, text[:60]
                )
                return None

            return (text, detected) if text else None
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
        path = os.path.join(tempfile.gettempdir(), "pmc_bargein_capture.wav")
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

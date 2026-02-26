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
_END_OF_SPEECH_SILENCE = 0.7     # End-of-speech silence (natural pause is ~300-600ms)
_MAX_RECORDING_SECONDS = 30      # Allow longer utterances
_CALIBRATION_SECONDS = 1.0
_NOISE_MULTIPLIER = 2.0          # More sensitive onset detection
_MIN_SPEECH_CHUNKS = 3           # Prevent false starts from clicks/coughs
_PRE_BUFFER_CHUNKS = 5           # Less pre-speech capture (faster response)
_SILENCE_FACTOR = 0.15           # End-of-speech = 15% of peak RMS
_QUEUE_TIMEOUT = 0.2             # Never block longer than 200ms
_RMS_WINDOW = 3                  # Sliding-window frames for RMS smoothing
_MIN_SPEECH_SECONDS = 0.5        # Ignore speech shorter than this
_TRIM_TRAILING_SILENCE = True    # Trim dead air from end of recording
_DEFAULT_WHISPER_MODEL = "small"
_DEFAULT_COMPUTE_TYPE = "int8"


class VoiceInput:
    """Mic capture with adaptive VAD and local Whisper transcription."""

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
        """Switch to a different input device and recalibrate."""
        self.device = device_index
        name = "Default" if device_index is None else sd.query_devices(device_index)["name"]
        logger.info("Mic device changed → %s (index=%s)", name, device_index)
        self._threshold = None  # force recalibration on next listen
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
        abs_silence_timeout = 0.4  # 400ms of dead air = guaranteed end

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

                    # ── ONSET: use RAW RMS (no averaging delay) ───────
                    if raw_rms > self._threshold:
                        consecutive_loud += 1
                        silence_start = None
                        abs_silence_start = None

                        if not is_recording:
                            pre_buffer.append(chunk.copy())
                            if consecutive_loud >= _MIN_SPEECH_CHUNKS:
                                logger.info(
                                    "Speech detected (rms=%.4f) — recording …",
                                    raw_rms,
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

        sf.write(output_filename, audio, self.samplerate)
        duration = len(audio) / self.samplerate
        logger.info("Recorded %.1fs → %s", duration, output_filename)
        return output_filename

    # ── Barge-in: speech monitor with audio capture ─────────────────────
    def monitor_for_speech(self, stop_event: threading.Event) -> bool:
        """Watch the mic for speech onset during TTS playback.

        When speech is detected, continues capturing audio into
        ``_bargein_frames`` until *stop_event* is set.  The next call
        to ``listen(assume_speaking=True)`` will prepend those frames
        so no audio is lost during the transition.

        Returns True if speech was detected, False otherwise.
        """
        if self._threshold is None:
            self.calibrate()

        monitor_q: queue.Queue = queue.Queue()
        pre_chunks: list[np.ndarray] = []  # chunks that triggered onset

        def _cb(indata, frames, time_info, status):
            monitor_q.put(indata.copy())

        consecutive_loud = 0
        required = _MIN_SPEECH_CHUNKS
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
                        if raw_rms > self._threshold:
                            consecutive_loud += 1
                            pre_chunks.append(chunk)
                            if consecutive_loud >= required:
                                logger.info(
                                    "Barge-in speech detected (rms=%.4f, thr=%.4f)",
                                    raw_rms, self._threshold,
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
    def transcribe(self, audio_path: str) -> Optional[str]:
        start = time.monotonic()
        try:
            model = self._get_whisper_model()
            # Auto-detect language for multilingual support (EN/RU/RO).
            # Set WHISPER_LANGUAGE=en in .env to force English only.
            whisper_lang = os.getenv("WHISPER_LANGUAGE", "auto")
            lang_arg = None if whisper_lang == "auto" else whisper_lang

            # Prime Whisper with Tarkov-specific vocabulary so it
            # recognises game terms instead of guessing common words
            # (e.g. "Escape from Tarkov" not "this Q from Tarkov").
            # Covers: game terms, 1.0 content, maps, weapons, bosses,
            # traders, items, mechanics, EFT slang, and Twitch lingo.
            initial_prompt = (
                # ── Core game & factions ──
                "Escape from Tarkov, Tarkov, EFT, Battlestate Games, BSG, Nikita, "
                "PMC, Scav, Scavs, player Scav, USEC, BEAR, "

                # ── Maps (incl. 1.0 + upcoming) ──
                "Customs, Woods, Interchange, Shoreline, Reserve, Labs, "
                "Streets of Tarkov, Streets, Lighthouse, Ground Zero, Factory, "
                "Terminal, Harbour, Icebreaker, End of Line, "

                # ── Bosses ──
                "Reshala, Killa, Shturman, Sanitar, Tagilla, Glukhar, Kaban, "
                "Knight, Birdeye, Big Pipe, The Partisan, "

                # ── Traders ──
                "Prapor, Therapist, Skier, Peacekeeper, Mechanic, Ragman, "
                "Jaeger, Ref, Fence, BTR Driver, Lightkeeper, "

                # ── Weapons & attachments (incl. 1.0 additions) ──
                "AK-74, AK-74N, AKS-74U, AK-12, AK-103, AK-104, AK-308, "
                "M4A1, M16, HK 416, MDR, SCAR, SA-58, "
                "Mosin, DVL, SV-98, SVD, RSASS, SR-25, M1A, "
                "MP5, MP7, MPX, UMP, PP-19, Vector, P90, "
                "AS VAL, VSS Vintorez, RPK, RPK-16, PKM, "
                "G36, PPSh, AA-12, M60, AUG, Radian Model 1, "
                "Saiga-12, MP-133, MP-153, TOZ, KS-23, "
                "Glock, Desert Eagle, SR-1MP, TT pistol, "
                "Hybrid 46 suppressor, Valday scope, FLIR, REAP-IR, "

                # ── Ammo tiers ──
                "M995, M856A1, M855A1, M61, M62, M80, "
                "BS ammo, BT ammo, BP ammo, PP ammo, SNB, LPS Gzh, "
                "SP-6, SPP, SP-5, PBP, AP 6.3, Pst gzh, "
                "AP-20, Flechette, Magnum buckshot, "

                # ── Items & keys ──
                "LEDX, Salewa, IFAK, morphine, CMS kit, Surv12, "
                "Red Rebel, SICC case, docs case, keytool, "
                "graphics card, bitcoin, moonshine, intelligence folder, "
                "flash drive, dogtags, paracord, "
                "KIBA key, dorms key, marked room, "

                # ── Containers ──
                "Kappa container, Epsilon container, Gamma container, "
                "Alpha container, Theta container, secure container, "

                # ── Mechanics & gameplay ──
                "flea market, hideout, stash, Bitcoin Farm, Scav Case, "
                "work bench, medstation, intelligence center, "
                "extracts, exfil, extraction point, V-Ex, "
                "quest, task, raid, wipe, extract, PMC run, Scav run, "
                "found in raid, FIR, PvP, PvE, co-op, story mode, "
                "barter, breach, armor class, penetration, "
                "BTR, armored train, reconnect, DLSS, "

                # ── EFT community slang ──
                "chad, rat, extract camper, head-eyes, one-tap, "
                "juiced, kitted, thicc, meta, loot run, "
                "rat attack, zero to hero, factory gate, "
                "Tarkov Shooter, Punisher, Gunsmith, "
                "Sherpa, TarkovLFG, "

                # ── EFT Arena ──
                "Escape from Tarkov Arena, Arena, battlepass, "

                # ── Twitch & streaming ──
                "Twitch, stream, streamer, streaming, live stream, "
                "chat, Twitch chat, subscriber, sub, resub, gifted sub, "
                "donation, dono, bits, cheer, hype train, "
                "raid, host, follow, follower, "
                "VOD, clip, highlight, emote, "
                "Kappa emote, Poggers, PogChamp, KEKW, LUL, "
                "MonkaS, Sadge, Jebaited, Kreygasm, copium, "
                "mod, moderator, ban, timeout, "
                "OBS, overlay, alerts, scene, "
                "stream sniper, stream sniping, TOS, "
                "affiliate, partner, "
                "drops, Twitch drops, watch party"
            )

            segments, info = model.transcribe(
                audio_path, language=lang_arg, beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250),
                initial_prompt=initial_prompt,
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


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    vi = VoiceInput()
    vi.calibrate()
    print("Speak now …")
    path = vi.listen()
    if path:
        print(f"You said: {vi.transcribe(path)}")

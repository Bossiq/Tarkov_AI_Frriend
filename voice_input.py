"""
Voice Input — microphone capture with Volume Activity Detection + transcription.

Listens for speech via the default input device, records until silence,
writes a WAV file, and optionally transcribes it to text using
Google's free Speech Recognition API (no API key required).
"""

import logging
import time
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────
_SAMPLERATE = 16_000
_CHANNELS = 1
_CHUNK_SECONDS = 0.1
_DEFAULT_THRESHOLD = 0.02
_DEFAULT_SILENCE_DURATION = 1.5
_MAX_RECORDING_SECONDS = 30  # safety cap — prevent infinite recording


class VoiceInput:
    """Captures speech from the microphone and transcribes it to text."""

    def __init__(
        self,
        samplerate: int = _SAMPLERATE,
        channels: int = _CHANNELS,
        threshold: float = _DEFAULT_THRESHOLD,
        silence_duration: float = _DEFAULT_SILENCE_DURATION,
        max_duration: float = _MAX_RECORDING_SECONDS,
        shutdown_event: Optional[threading.Event] = None,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.max_duration = max_duration
        self._shutdown = shutdown_event or threading.Event()
        self._recognizer = sr.Recognizer()

    def listen(self, output_filename: str = "temp_audio.wav") -> Optional[str]:
        """Block until speech is detected, recorded, and silence follows.

        Returns the path to the saved WAV file, or ``None`` if nothing
        was captured (or if a shutdown was requested).
        """
        logger.info("Listening for speech …")
        recorded_frames: list[np.ndarray] = []
        is_recording = False
        silence_start: Optional[float] = None
        recording_start: Optional[float] = None
        chunk_size = int(self.samplerate * _CHUNK_SECONDS)

        try:
            with sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
            ) as stream:
                while not self._shutdown.is_set():
                    chunk, _ = stream.read(chunk_size)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))

                    if rms > self.threshold:
                        if not is_recording:
                            logger.info("Speech detected — recording …")
                            is_recording = True
                            recording_start = time.monotonic()
                        recorded_frames.append(chunk.copy())
                        silence_start = None
                    elif is_recording:
                        recorded_frames.append(chunk.copy())
                        if silence_start is None:
                            silence_start = time.monotonic()
                        elif time.monotonic() - silence_start > self.silence_duration:
                            logger.debug("Silence threshold reached — stopping")
                            break

                    # Safety: cap total recording length
                    if (
                        is_recording
                        and recording_start is not None
                        and time.monotonic() - recording_start > self.max_duration
                    ):
                        logger.warning(
                            "Max recording duration (%ss) reached", self.max_duration
                        )
                        break

        except sd.PortAudioError:
            logger.exception("PortAudio error — is a microphone connected?")
            return None
        except Exception:
            logger.exception("Unexpected error in VAD listening loop")
            return None

        if self._shutdown.is_set():
            logger.info("Listening cancelled (shutdown requested)")
            return None

        if not recorded_frames:
            return None

        audio_data = np.concatenate(recorded_frames, axis=0)
        sf.write(output_filename, audio_data, self.samplerate)
        logger.info(
            "Saved recording to %s (%d samples)", output_filename, len(audio_data)
        )
        return output_filename

    def transcribe(self, audio_path: str) -> Optional[str]:
        """Transcribe a WAV file to text using Google Speech Recognition.

        This uses the free, no-API-key-required Google web speech API.
        Returns the transcription string, or None on failure.
        """
        try:
            with sr.AudioFile(audio_path) as source:
                audio_data = self._recognizer.record(source)

            logger.info("Transcribing audio …")
            text = self._recognizer.recognize_google(audio_data)
            logger.info("Transcription: %s", text)
            return text

        except sr.UnknownValueError:
            logger.warning("Could not understand the audio")
            return None
        except sr.RequestError as exc:
            logger.error("Speech recognition service error: %s", exc)
            return None
        except Exception:
            logger.exception("Transcription failed")
            return None


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    vi = VoiceInput()
    path = vi.listen(output_filename="test_audio.wav")
    if path:
        text = vi.transcribe(path)
        print(f"You said: {text}")

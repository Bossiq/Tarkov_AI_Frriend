"""
Voice Output — Multilingual TTS via edge-tts (Microsoft Neural Voices).

Primary engine: edge-tts (neural voices for EN/RU/RO)
Fallback: Kokoro ONNX (English only)
Last resort: macOS `say` command

Features:
  * Auto language detection (Cyrillic → Russian, Romanian chars → Romanian)
  * Neural female voices per language
  * Async pipeline: pre-synthesizes next sentence while current plays
  * Text preprocessing for natural speech
"""

import asyncio
import io
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Generator, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# ── Paths & URLs ─────────────────────────────────────────────────────
_MODELS_DIR = Path(__file__).parent / "models"
_ONNX_FILE = _MODELS_DIR / "kokoro-v1.0.onnx"
_VOICES_FILE = _MODELS_DIR / "voices-v1.0.bin"

_ONNX_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# ── edge-tts Voice Mapping ───────────────────────────────────────────
_EDGE_VOICES = {
    "en": "en-US-JennyNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ro": "ro-RO-AlinaNeural",
}
_EDGE_RATE = "+0%"  # Normal speaking rate

# ── Defaults ─────────────────────────────────────────────────────────
_DEFAULT_VOICE = "af_heart"
_DEFAULT_SPEED = 1.1
_DEFAULT_LANG = "en-us"
_SAY_TIMEOUT_S = 30
_SENTINEL = object()

# ── Language detection patterns ──────────────────────────────────────
_CYRILLIC = re.compile(r'[\u0400-\u04FF]')
_ROMANIAN_CHARS = re.compile(r'[ĂăÂâÎîȘșȚț]')

# ── Text-preprocessing patterns ──────────────────────────────────────
_NUMBER_UNIT = re.compile(r"(\d+)\s*(m|km|kg|hrs?|mins?|secs?)\b", re.IGNORECASE)
_MULTI_PUNCT = re.compile(r"([.!?]){2,}")
_MARKDOWN_BOLD = re.compile(r"\*\*(.*?)\*\*")
_MARKDOWN_ITALIC = re.compile(r"\*(.*?)\*")
_MARKDOWN_BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_MARKDOWN_HEADER = re.compile(r"^#+\s+", re.MULTILINE)
_EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+",
    re.UNICODE,
)

_ABBREV_MAP = {
    r"\bETA\b": "E.T.A.",
    r"\bMIA\b": "M.I.A.",
    r"\bKIA\b": "K.I.A.",
    r"\bPMC\b": "P.M.C.",
    r"\bRPM\b": "R.P.M.",
}

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
]


def _number_to_words(n: int) -> str:
    """Convert an integer (0-9999) to English words."""
    if n < 0:
        return f"minus {_number_to_words(-n)}"
    if n < 20:
        return _ONES[n]
    if n < 100:
        return f"{_TENS[n // 10]} {_ONES[n % 10]}".strip()
    if n < 1000:
        remainder = _number_to_words(n % 100)
        return f"{_ONES[n // 100]} hundred {remainder}".strip()
    if n < 10000:
        remainder = _number_to_words(n % 1000)
        return f"{_number_to_words(n // 1000)} thousand {remainder}".strip()
    return str(n)


def _detect_language(text: str) -> str:
    """Detect language from text content. Returns 'ru', 'ro', or 'en'."""
    if _CYRILLIC.search(text):
        return "ru"
    if _ROMANIAN_CHARS.search(text):
        return "ro"
    return "en"


class VoiceOutput:
    """Multilingual TTS with edge-tts neural voices, Kokoro fallback."""

    def __init__(self, gui_callback: Optional[Callable[[str], None]] = None) -> None:
        self._gui_callback = gui_callback
        self._voice = os.getenv("TTS_VOICE", _DEFAULT_VOICE)
        self._speed = float(os.getenv("TTS_SPEED", str(_DEFAULT_SPEED)))
        self._lang = os.getenv("TTS_LANG", _DEFAULT_LANG)
        self._kokoro = None
        self._kokoro_lock = threading.Lock()
        self._edge_available = False

        # Check edge-tts availability
        try:
            import edge_tts  # noqa: F401
            self._edge_available = True
            logger.info("edge-tts available (multilingual neural voices)")
        except ImportError:
            logger.warning("edge-tts not installed — falling back to Kokoro")

        # Initialize Kokoro in background as fallback
        threading.Thread(target=self._init_kokoro, name="KokoroInit", daemon=True).start()

    # ── Kokoro initialisation ─────────────────────────────────────────
    def _init_kokoro(self) -> None:
        try:
            self._ensure_model_files()
            from kokoro_onnx import Kokoro
            self._kokoro = Kokoro(str(_ONNX_FILE), str(_VOICES_FILE))
            logger.info("Kokoro TTS loaded as fallback (voice=%s)", self._voice)
        except Exception:
            logger.exception("Failed to load Kokoro fallback")
            self._kokoro = None

    def _ensure_model_files(self) -> None:
        _MODELS_DIR.mkdir(exist_ok=True)
        for path, url, label in [
            (_ONNX_FILE, _ONNX_URL, "model"),
            (_VOICES_FILE, _VOICES_URL, "voices"),
        ]:
            if not path.exists():
                logger.info("Downloading Kokoro %s -> %s ...", label, path.name)
                urllib.request.urlretrieve(url, path)
                logger.info("Downloaded %s (%.1f MB)", path.name, path.stat().st_size / 1e6)

    # ══════════════════════════════════════════════════════════════════
    #  LANGUAGE DETECTION + EDGE-TTS
    # ══════════════════════════════════════════════════════════════════
    def _speak_edge(self, text: str, lang: str) -> bool:
        """Synthesize and play speech via edge-tts. Returns True on success."""
        try:
            import edge_tts

            voice = _EDGE_VOICES.get(lang, _EDGE_VOICES["en"])
            logger.debug("edge-tts: voice=%s lang=%s text=%s", voice, lang, text[:60])

            # Run async edge-tts in a new event loop
            async def _synthesize():
                communicate = edge_tts.Communicate(text, voice, rate=_EDGE_RATE)
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]
                return audio_data

            loop = asyncio.new_event_loop()
            try:
                mp3_bytes = loop.run_until_complete(_synthesize())
            finally:
                loop.close()

            if not mp3_bytes:
                logger.warning("edge-tts returned empty audio")
                return False

            # Decode MP3 to PCM via temp file
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(mp3_bytes)
                tmp_path = tmp.name

            try:
                audio, sr = sf.read(tmp_path, dtype="float32")
                # Normalize
                peak = np.max(np.abs(audio))
                if peak > 0.01:
                    audio = audio * (0.85 / peak)
                # Fade edges (15ms)
                fade_len = min(int(sr * 15 / 1000), len(audio) // 4)
                if fade_len > 1:
                    audio[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
                    audio[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
                sd.play(audio, samplerate=sr)
                sd.wait()
                return True
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception:
            logger.exception("edge-tts failed")
            return False

    # ══════════════════════════════════════════════════════════════════
    #  TEXT PREPROCESSING
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _preprocess_for_speech(text: str) -> str:
        """Transform raw LLM text into TTS-friendly speech text."""
        t = text
        t = _EMOJI_PATTERN.sub("", t)
        t = _MARKDOWN_HEADER.sub("", t)
        t = _MARKDOWN_BOLD.sub(r"\1", t)
        t = _MARKDOWN_ITALIC.sub(r"\1", t)
        t = _MARKDOWN_BULLET.sub("", t)

        # Only expand numbers for English text
        if not _CYRILLIC.search(t):
            def _expand_number_unit(m: re.Match) -> str:
                num = int(m.group(1))
                unit = m.group(2).lower().rstrip("s")
                unit_map = {
                    "m": "metres", "km": "kilometres", "kg": "kilograms",
                    "hr": "hours", "min": "minutes", "sec": "seconds",
                }
                word_unit = unit_map.get(unit, m.group(2))
                if num == 1:
                    word_unit = word_unit.rstrip("s")
                return f"{_number_to_words(num)} {word_unit}"

            t = _NUMBER_UNIT.sub(_expand_number_unit, t)

            def _expand_standalone_number(m: re.Match) -> str:
                n = int(m.group(0))
                return _number_to_words(n) if n <= 9999 else m.group(0)

            t = re.sub(r"\b\d{1,4}\b", _expand_standalone_number, t)

            for pattern, replacement in _ABBREV_MAP.items():
                t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

        t = _MULTI_PUNCT.sub(r"\1", t)
        t = " ".join(t.split())
        return t.strip()

    # ══════════════════════════════════════════════════════════════════
    #  AUDIO POST-PROCESSING
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _postprocess_audio(samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Light post-processing: normalize + anti-click fade."""
        audio = samples.copy().astype(np.float32)
        if len(audio) == 0:
            return audio
        peak = np.max(np.abs(audio))
        if peak > 0.01:
            audio = audio * (0.85 / peak)
        fade_len = min(int(sample_rate * 15 / 1000), len(audio) // 4)
        if fade_len > 1:
            audio[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            audio[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        padding = np.zeros(int(sample_rate * 0.05), dtype=np.float32)
        audio = np.concatenate([audio, padding])
        return audio

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════
    def speak(self, text: str) -> None:
        """Speak *text* aloud with auto language detection."""
        if not text or not text.strip():
            return
        clean = self._preprocess_for_speech(text)
        if self._gui_callback:
            self._gui_callback(f"[PMC] {text}")

        # Detect language and try edge-tts first
        lang = _detect_language(text)
        if self._edge_available and self._speak_edge(clean, lang):
            return

        # Fallback: Kokoro (English only)
        if self._kokoro is not None:
            self._speak_kokoro(clean)
        else:
            self._speak_say(clean)

    def speak_streamed(self, sentences: Generator[str, None, None]) -> None:
        """Speak sentences as they stream from the LLM.

        Uses edge-tts for multilingual, Kokoro fallback for English.
        """
        full_response: list[str] = []

        # If edge-tts available, use it for each sentence directly
        if self._edge_available:
            for sentence in sentences:
                if not sentence.strip():
                    continue
                full_response.append(sentence)
                clean = self._preprocess_for_speech(sentence)
                if not clean.strip():
                    continue
                lang = _detect_language(sentence)
                if not self._speak_edge(clean, lang):
                    # Fallback to Kokoro for this sentence
                    if self._kokoro is not None:
                        self._speak_kokoro(clean)
            if self._gui_callback and full_response:
                self._gui_callback(f"[PMC] {' '.join(full_response)}")
            return

        # No edge-tts: use Kokoro async pipeline
        if self._kokoro is None:
            full = []
            for s in sentences:
                if not s.strip():
                    continue
                full.append(s)
                self._speak_say(self._preprocess_for_speech(s))
            if self._gui_callback and full:
                self._gui_callback(f"[PMC] {' '.join(full)}")
            return

        audio_q: queue.Queue = queue.Queue(maxsize=3)

        def _producer() -> None:
            for sentence in sentences:
                if not sentence.strip():
                    continue
                full_response.append(sentence)
                speech_text = self._preprocess_for_speech(sentence)
                if not speech_text or not speech_text.strip():
                    continue
                try:
                    with self._kokoro_lock:
                        samples, sr = self._kokoro.create(
                            speech_text,
                            voice=self._voice,
                            speed=self._speed,
                            lang=self._lang,
                        )
                    processed = self._postprocess_audio(samples, sr)
                    audio_q.put((processed, sr))
                except Exception:
                    logger.exception("TTS synthesis failed: %s", speech_text[:50])
                    continue
            audio_q.put(_SENTINEL)

        producer = threading.Thread(target=_producer, name="TTSSynth", daemon=True)
        producer.start()

        while True:
            try:
                item = audio_q.get(timeout=30.0)
            except queue.Empty:
                logger.warning("TTS playback timed out")
                break
            if item is _SENTINEL:
                break
            audio_data, sr = item
            try:
                sd.play(audio_data, samplerate=sr)
                sd.wait()
            except Exception:
                logger.exception("Playback error")

        producer.join(timeout=5.0)
        if self._gui_callback and full_response:
            self._gui_callback(f"[PMC] {' '.join(full_response)}")

    # ── Kokoro single sentence ────────────────────────────────────────
    def _speak_kokoro(self, text: str) -> None:
        try:
            with self._kokoro_lock:
                samples, sr = self._kokoro.create(
                    text, voice=self._voice, speed=self._speed, lang=self._lang,
                )
            processed = self._postprocess_audio(samples, sr)
            sd.play(processed, samplerate=sr)
            sd.wait()
        except Exception:
            logger.exception("Kokoro TTS failed -- falling back to 'say'")
            self._speak_say(text)

    # ── Platform fallback ─────────────────────────────────────────────
    def _speak_say(self, text: str) -> None:
        import platform
        if platform.system() == "Darwin":
            try:
                subprocess.run(["say", "-v", "Daniel", text], check=True, timeout=_SAY_TIMEOUT_S)
            except FileNotFoundError:
                logger.error("'say' command not found")
            except subprocess.TimeoutExpired:
                logger.warning("TTS timed out after %ds", _SAY_TIMEOUT_S)
            except Exception:
                logger.exception("Fallback TTS error")
        else:
            logger.warning("No fallback TTS on this platform")


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    vo = VoiceOutput()
    vo.speak("Hello there, this is a test of the neural voice engine.")

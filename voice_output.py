"""
Voice Output — Multilingual TTS via edge-tts (Microsoft Neural Voices).

Primary engine: edge-tts (neural voices for EN/RU/RO)
Fallback: Kokoro ONNX (English only)
Last resort: macOS `say` command

Features:
  * Auto language detection (Cyrillic → Russian, Romanian chars → Romanian)
  * Neural female voices per language
  * Real-time amplitude callback for lip-sync animation
  * Text preprocessing for natural speech
"""

import asyncio
import io
import logging
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
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
# Ava Multilingual is Microsoft's flagship neural voice (2024) —
# the most natural prosody and human-like quality available in edge-tts.
_EDGE_VOICES = {
    "en": "en-US-AvaMultilingualNeural",     # Microsoft's best — ultra-natural female
    "ru": "ru-RU-SvetlanaNeural",            # natural female Russian (only option)
    "ro": "ro-RO-AlinaNeural",               # female Romanian  (only option)
    "de": "de-DE-KatjaNeural",               # German female fallback
    "fr": "fr-FR-DeniseNeural",              # French female fallback
}

# Per-language rate overrides — Romanian and Russian neural voices
# sound unnatural when sped up, so cap them at +0% regardless of
# the user's EDGE_RATE setting (which is tuned for English).
_EDGE_RATE_OVERRIDES = {
    "ro": "+0%",
    "ru": "+0%",
}

# ── Defaults ─────────────────────────────────────────────────────────
_DEFAULT_VOICE = "af_heart"
_DEFAULT_SPEED = 1.1
_DEFAULT_LANG = "en-us"
_DEFAULT_EDGE_RATE = "+0%"
_SAY_TIMEOUT_S = 30
_AMPLITUDE_CHUNK_MS = 20  # RMS calculation every 20ms
_POST_TTS_COOLDOWN = 0.3  # seconds of silence after TTS before re-listening

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
    # Internet / chat abbreviations → spoken words
    r"\bbtw\b": "by the way",
    r"\bBTW\b": "by the way",
    r"\bimo\b": "in my opinion",
    r"\bIMO\b": "in my opinion",
    r"\btbh\b": "to be honest",
    r"\bTBH\b": "to be honest",
    r"\bomg\b": "oh my god",
    r"\bOMG\b": "oh my god",
    r"\bidk\b": "I don't know",
    r"\bIDK\b": "I don't know",
    r"\bafk\b": "away from keyboard",
    r"\bAFK\b": "away from keyboard",
    r"\bgg\b": "good game",
    r"\bGG\b": "good game",
    r"\bglhf\b": "good luck have fun",
    r"\bGLHF\b": "good luck have fun",
    r"\brn\b": "right now",
    r"\bRN\b": "right now",
    r"\bngl\b": "not gonna lie",
    r"\bNGL\b": "not gonna lie",
    r"\bsmh\b": "shaking my head",
    r"\bSMH\b": "shaking my head",
    r"\bfyi\b": "for your information",
    r"\bFYI\b": "for your information",
    r"\basap\b": "as soon as possible",
    r"\bASAP\b": "as soon as possible",
    r"\blol\b": "haha",
    r"\bLOL\b": "haha",
    r"\blmao\b": "haha",
    r"\bLMAO\b": "haha",
    r"\bwdym\b": "what do you mean",
    r"\bWDYM\b": "what do you mean",
    r"\bnvm\b": "never mind",
    r"\bNVM\b": "never mind",
    r"\bw/\b": "with",
    r"\bw/o\b": "without",
    # Gaming / Tarkov
    r"\bETA\b": "E.T.A.",
    r"\bMIA\b": "missing in action",
    r"\bKIA\b": "killed in action",
    r"\bPMC\b": "P.M.C.",
    r"\bRPM\b": "R.P.M.",
    r"\bHP\b": "health points",
    r"\bXP\b": "experience",
    r"\bDPS\b": "damage per second",
    r"\bOP\b": "overpowered",
    r"\bPVP\b": "player versus player",
    r"\bPVE\b": "player versus environment",
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


# ── Language detection word sets ─────────────────────────────────────
# Words that are UNIQUELY Romanian (won't appear in English text).
# A single match is enough to classify as Romanian.
_RO_UNIQUE = re.compile(
    r'\b('
    # Pronouns / articles
    r'eu|tu|el|ea|noi|voi|ei|ele|'
    r'meu|mea|mei|mele|tau|ta|tai|tale|'
    r'lui|lor|nostru|nostra|vostru|voastra|'
    # Verbs — common forms
    r'sunt|esti|este|suntem|sunteti|'
    r'eram|erai|era|eram|erati|erau|'
    r'fost|fi|fii|fie|fiind|'
    r'fac|faci|facem|faceti|faca|facut|'
    r'stiu|stii|stie|stim|stiti|'
    r'vreau|vrei|vrea|vrem|vreti|'
    r'pot|poti|poate|putem|puteti|putea|'
    r'trebuie|trebui|'
    r'spun|spui|spune|spunem|spuneti|spus|'
    r'merg|mergi|merge|mergem|mergeti|mers|'
    r'vin|vii|vine|venim|veniti|venit|'
    r'dau|dai|dam|dati|dat|'
    r'iau|iei|ia|luam|luati|luat|'
    r'vad|vezi|vede|vedem|vedeti|vazut|'
    r'aud|auzi|aude|auzit|'
    r'cred|crezi|crede|crezut|'
    r'zic|zici|zice|zicem|zis|'
    # Prepositions / conjunctions
    r'pentru|despre|dintre|dintr|catre|'
    r'prin|peste|langa|intre|inainte|'
    r'dupa|fara|pana|contra|'
    r'daca|deci|insa|deoarece|fiindca|'
    r'totusi|asadar|altfel|'
    # Adverbs / common words
    r'acum|inca|apoi|deja|doar|chiar|mereu|'
    r'niciodata|intotdeauna|probabil|'
    r'oriunde|oricum|oricand|oricine|tocmai|'
    r'foarte|bine|rau|acolo|aici|'
    r'nimic|nimeni|totul|ceva|cineva|fiecare|'
    # Common nouns / adjectives
    r'frumos|mare|mic|bun|buna|nou|vechi|'
    r'mult|putin|repede|incet|'
    # Greetings / interjections
    r'salut|buna|noroc|multumesc|te rog|'
    r'hai|haide|gata|destul|'
    # Question words
    r'ce|cine|cand|unde|cat|de ce|cum'
    r')\b',
    re.IGNORECASE
)

# Words that MIGHT be Romanian but could also appear in other contexts.
# Need 2+ to confirm.
_RO_COMMON = re.compile(
    r'\b(da|nu|si|sau|mai|la|pe|cu|din|am|ai|a|o|un|una|sa|ce)\b',
    re.IGNORECASE
)

# Common transliterated Russian words (when LLM writes Russian in Latin script)
_RU_TRANSLIT = re.compile(
    r'\b('
    r'privet|zdorovo|nu|da|net|'
    r'spasibo|pozhaluysta|khorosho|'
    r'davay|poydem|brat|bratishka|bratan|'
    r'kak|dela|chto|gde|kogda|pochemu|'
    r'mozhno|nado|nyet|harasho|'
    r'poka|dosvidaniya|zdravstvuyte'
    r')\b',
    re.IGNORECASE
)


def _detect_language(text: str) -> str:
    """Detect language from text content. Returns 'ru', 'ro', or 'en'.

    Uses a multi-signal scoring approach:
      1. Cyrillic characters → instant Russian
      2. Romanian diacritics (ă, â, î, ș, ț) → instant Romanian
      3. Uniquely Romanian words → 1 match = Romanian
      4. Common short Romanian words (da, nu, si) → 2+ matches = Romanian
      5. Transliterated Russian words → fallback Russian detection
      6. Default → English
    """
    # Level 1: Character-based (instant, unambiguous)
    if _CYRILLIC.search(text):
        return "ru"
    if _ROMANIAN_CHARS.search(text):
        return "ro"

    # Level 2: Uniquely Romanian words (1 match = confident)
    unique_ro = _RO_UNIQUE.findall(text)
    if unique_ro:
        logger.debug("Romanian detected (unique words: %s)", unique_ro[:3])
        return "ro"

    # Level 3: Common short Romanian words (ambiguous alone, 2+ = Romanian)
    common_ro = _RO_COMMON.findall(text)
    if len(common_ro) >= 2:
        logger.debug("Romanian detected (common words: %s)", common_ro[:4])
        return "ro"

    # Level 4: Transliterated Russian
    if _RU_TRANSLIT.search(text):
        return "ru"

    return "en"


# ── macOS 'say' voice mapping ────────────────────────────────────────
_SAY_VOICES = {
    "en": "Daniel",
    "ro": "Ioana",
    "ru": "Milena",
}


class VoiceOutput:
    """Multilingual TTS with edge-tts neural voices, amplitude-driven lip sync.

    Supports barge-in interruption: pass an interrupt_event that, when set,
    will stop playback between audio chunks and stop consuming sentences.
    """

    def __init__(
        self,
        gui_callback: Optional[Callable[[str], None]] = None,
        on_speak_start: Optional[Callable[[], None]] = None,
        on_speak_end: Optional[Callable[[], None]] = None,
        on_amplitude: Optional[Callable[[float], None]] = None,
        interrupt_event: Optional[threading.Event] = None,
    ) -> None:
        self._gui_callback = gui_callback
        self._on_speak_start = on_speak_start
        self._on_speak_end = on_speak_end
        self._on_amplitude = on_amplitude
        self._interrupt = interrupt_event or threading.Event()
        self._was_interrupted = False
        self._voice = os.getenv("TTS_VOICE", _DEFAULT_VOICE)
        self._speed = float(os.getenv("TTS_SPEED", str(_DEFAULT_SPEED)))
        self._lang = os.getenv("TTS_LANG", _DEFAULT_LANG)
        self._edge_rate = os.getenv("EDGE_RATE", _DEFAULT_EDGE_RATE)
        self._kokoro = None
        self._kokoro_lock = threading.Lock()
        self._edge_available = False
        self._language_hint: str = "en"          # Hint from Whisper detection

        # If the user pinned a language via WHISPER_LANGUAGE, force ALL
        # TTS output to that language — eliminates mid-response voice
        # switches caused by per-sentence language detection.
        _wl = os.getenv("WHISPER_LANGUAGE", "auto").strip().lower()
        self._forced_lang: Optional[str] = _wl if _wl and _wl != "auto" else None

        # Persistent asyncio loop for edge-tts (avoid creating/destroying per call)
        self._edge_loop = asyncio.new_event_loop()
        self._edge_tts_mod = None  # cached module import

        # ── Barge-in interrupt support ─────────────────────────────
        # NOTE: self._interrupt already set above from interrupt_event param
        self._was_interrupted = False
        self._speaking_started = threading.Event()  # set when audio playback begins

        # Check edge-tts availability
        try:
            import edge_tts  # noqa: F401
            self._edge_available = True
            logger.info("edge-tts available (multilingual neural voices)")
        except ImportError:
            logger.warning("edge-tts not installed -- falling back to Kokoro")

        # Initialize Kokoro in background as fallback
        threading.Thread(target=self._init_kokoro, name="KokoroInit", daemon=True).start()

    # ── Interrupt control ─────────────────────────────────────────────
    def was_interrupted(self) -> bool:
        """True if the last speak/speak_streamed was cut short by barge-in."""
        return self._was_interrupted

    def reset_interrupt(self) -> None:
        """Clear interrupt state before starting a new speech cycle."""
        self._interrupt.clear()
        self._was_interrupted = False

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
    #  AUDIO PLAYBACK WITH AMPLITUDE CALLBACK
    # ══════════════════════════════════════════════════════════════════
    def _play_with_amplitude(self, audio: np.ndarray, sr: int) -> bool:
        """Play audio while firing amplitude callbacks for lip-sync.

        Returns True if playback completed, False if interrupted.
        Checks interrupt_event every chunk (~20ms) for fast barge-in.
        """
        if self._on_speak_start:
            self._on_speak_start()
        self._speaking_started.set()  # signal that audio is actually playing

        interrupted = False

        # Mono conversion for amplitude calculation
        if audio.ndim > 1:
            mono = audio.mean(axis=1)
        else:
            mono = audio

        chunk_size = max(1, int(sr * _AMPLITUDE_CHUNK_MS / 1000))

        try:
            with sd.OutputStream(samplerate=sr, channels=1 if audio.ndim == 1 else audio.shape[1],
                                 dtype='float32') as stream:
                pos = 0
                while pos < len(audio):
                    # ── Barge-in check (every ~20ms) ─────────────
                    if self._interrupt.is_set():
                        logger.info("Barge-in: playback interrupted")
                        interrupted = True
                        break

                    end = min(pos + chunk_size, len(audio))
                    chunk = audio[pos:end]
                    stream.write(chunk if chunk.ndim > 1 else chunk.reshape(-1, 1))

                    # RMS amplitude for lip sync
                    if self._on_amplitude:
                        mono_chunk = mono[pos:end]
                        rms = float(np.sqrt(np.mean(mono_chunk ** 2)))
                        amplitude = min(1.0, rms * 4.0)
                        self._on_amplitude(amplitude)

                    pos = end

            # Signal silence after playback
            if self._on_amplitude:
                self._on_amplitude(0.0)

            # Post-TTS cooldown — brief silence so the mic doesn't
            # immediately capture the tail of TTS audio as speech
            if not interrupted:
                time.sleep(_POST_TTS_COOLDOWN)

        except Exception:
            logger.exception("Audio playback error")

        if self._on_speak_end:
            self._on_speak_end()

        return not interrupted

    # ══════════════════════════════════════════════════════════════════
    #  EDGE-TTS SYNTHESIS
    # ══════════════════════════════════════════════════════════════════
    def _speak_edge(self, text: str, lang: str) -> bool:
        """Synthesize and play speech via edge-tts. Returns True on success."""
        try:
            if self._edge_tts_mod is None:
                import edge_tts
                self._edge_tts_mod = edge_tts
            edge_tts = self._edge_tts_mod

            voice = _EDGE_VOICES.get(lang, _EDGE_VOICES["en"])
            # Use per-language rate override if available (RO/RU stay at natural pace)
            rate = _EDGE_RATE_OVERRIDES.get(lang, self._edge_rate)
            logger.debug("edge-tts: voice=%s lang=%s rate=%s text=%s", voice, lang, rate, text[:60])

            async def _synthesize():
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]
                return audio_data

            # Reuse persistent loop instead of creating/destroying per call
            mp3_bytes = self._edge_loop.run_until_complete(_synthesize())

            if not mp3_bytes:
                logger.warning("edge-tts returned empty audio")
                return False

            # Decode MP3 to PCM via temp file
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(mp3_bytes)
                tmp_path = tmp.name

            try:
                audio, sr = sf.read(tmp_path, dtype="float32")
                audio = self._postprocess_audio(audio, sr)
                self._play_with_amplitude(audio, sr)
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
        """Normalize + dynamic range compression + anti-click fade + strip trailing artifacts."""
        audio = samples.copy().astype(np.float32)
        if len(audio) == 0:
            return audio

        # ── Strip trailing near-silence (kills edge-tts MP3 decoder beeps) ──
        # Walk backwards from end, trimming samples below a noise floor.
        # The beep artifact is typically a short burst at the very end.
        trim_threshold = 0.005
        end = len(audio)
        while end > 0 and abs(audio[end - 1]) < trim_threshold:
            end -= 1
        # Keep at least 80% of original audio (safety)
        min_keep = int(len(audio) * 0.8)
        end = max(end, min_keep)
        audio = audio[:end]

        if len(audio) == 0:
            return audio

        # Dynamic range compression (soft knee)
        peak = np.max(np.abs(audio))
        if peak > 0.01:
            # Compress peaks above 0.7 threshold
            threshold = 0.7
            ratio = 3.0
            mask = np.abs(audio) > threshold * peak
            if np.any(mask):
                over = np.abs(audio[mask]) - threshold * peak
                compressed = threshold * peak + over / ratio
                audio[mask] = np.sign(audio[mask]) * compressed

            # Final normalization
            peak_new = np.max(np.abs(audio))
            if peak_new > 0.01:
                audio = audio * (0.40 / peak_new)

        # Anti-click fade (50ms — longer fade eliminates edge-tts tail artifacts)
        fade_len = min(int(sample_rate * 50 / 1000), len(audio) // 4)
        if fade_len > 1:
            audio[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            audio[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

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

        lang = _detect_language(text)
        if self._edge_available and self._speak_edge(clean, lang):
            return

        if self._kokoro is not None:
            self._speak_kokoro(clean)
        else:
            self._speak_say(clean)

    def speak_streamed(self, sentences: Generator[str, None, None]) -> None:
        """Speak sentences as they stream from the LLM.

        Each sentence is spoken AS IT ARRIVES -- true streaming for
        instant feedback.  Stops consuming sentences if barge-in
        interrupt is detected between sentences.
        """
        self._was_interrupted = False

        for sentence in sentences:
            # Check interrupt BETWEEN sentences
            if self._interrupt.is_set():
                logger.info("Barge-in: stopping sentence stream")
                self._was_interrupted = True
                # Drain remaining sentences to avoid generator leak
                for _ in sentences:
                    pass
                break

            if not sentence.strip():
                continue

            # Show in GUI immediately
            if self._gui_callback:
                self._gui_callback(f"[PMC] {sentence}")

            clean = self._preprocess_for_speech(sentence)
            if not clean.strip():
                continue

            # Detect language per sentence for correct voice selection
            lang = self._forced_lang or _detect_language(sentence)

            spoke = False
            if self._edge_available:
                spoke = self._speak_edge(clean, lang)
            if not spoke and self._kokoro is not None:
                self._speak_kokoro(clean)
            elif not spoke:
                self._speak_say(clean)

            # Check again after speaking (playback might have been cut)
            if self._interrupt.is_set():
                logger.info("Barge-in: interrupted during playback")
                self._was_interrupted = True
                for _ in sentences:
                    pass
                break

    # ── Kokoro single sentence ────────────────────────────────────────
    def _speak_kokoro(self, text: str) -> None:
        try:
            with self._kokoro_lock:
                samples, sr = self._kokoro.create(
                    text, voice=self._voice, speed=self._speed, lang=self._lang,
                )
            processed = self._postprocess_audio(samples, sr)
            self._play_with_amplitude(processed, sr)
        except Exception:
            logger.exception("Kokoro TTS failed -- falling back to 'say'")
            self._speak_say(text)

    # ── Platform fallback ─────────────────────────────────────────────
    def _speak_say(self, text: str, lang: str = "en") -> None:
        import platform
        if platform.system() == "Darwin":
            try:
                voice = _SAY_VOICES.get(lang, _SAY_VOICES["en"])
                if self._on_speak_start:
                    self._on_speak_start()
                subprocess.run(["say", "-v", voice, text], check=True, timeout=_SAY_TIMEOUT_S)
                if self._on_speak_end:
                    self._on_speak_end()
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

    def _print_amp(a: float) -> None:
        bars = int(a * 30)
        print(f"  {'█' * bars}{'░' * (30 - bars)} {a:.2f}", end="\r")

    vo = VoiceOutput(on_amplitude=_print_amp)
    vo.speak("Hello there, this is a test of the neural voice engine with lip sync.")
    print()

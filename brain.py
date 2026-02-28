"""
AI Brain — dual-engine LLM for PMC Overwatch.

Primary:  Groq cloud API (250+ tokens/sec, free tier)
Fallback: Ollama local inference

Features:
  • Auto-selects Groq if GROQ_API_KEY is set, else Ollama
  • Auto-failover: Groq rate-limit → Ollama → auto switch-back
  • Streaming sentence-by-sentence for instant TTS
  • Sliding-window conversation memory
  • Retry logic with exponential backoff
"""

import logging
import os
import re
import threading
import time
from collections import deque
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
_DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
_DEFAULT_NUM_CTX = 2048
_DEFAULT_NUM_PREDICT = 512
_DEFAULT_NUM_BATCH = 512
_DEFAULT_TEMPERATURE = 0.6
_DEFAULT_TOP_P = 0.85
_DEFAULT_REPEAT_PENALTY = 1.1
_MAX_MEMORY = 4
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0

from tarkov_data import QUEST_REFERENCE  # noqa: E402

_SYSTEM_INSTRUCTION = (
    "You are a female PMC Operator and Tarkov veteran, a gaming buddy. "
    "Talk naturally like a friend on Discord voice chat. Use contractions. "
    "Keep answers to 2-4 sentences unless asked for detail. "
    "Be helpful, funny, sometimes sarcastic. "
    "Never use markdown, lists, emoji, asterisks, or formatting. "
    "Never say you are an AI, language model, or assistant.\n\n"
    "SPEECH RULES (your text will be read aloud by TTS):\n"
    "- NEVER use abbreviations like btw, imo, tbh, idk, ngl, afk, asap, lol, lmao, "
    "smh, fyi, nvm, omg, wdym, gg, or any shortened slang.\n"
    "- ALWAYS write full words: 'by the way' not 'btw', 'in my opinion' not 'imo', "
    "'to be honest' not 'tbh', 'I do not know' not 'idk'.\n"
    "- Speak in complete, natural sentences that sound good when read aloud.\n\n"
    "ABSOLUTE LANGUAGE RULES (violating these is a critical failure):\n"
    "- You ONLY speak THREE languages: English, Russian, Romanian.\n"
    "- NEVER respond in German, French, Spanish, or ANY other language.\n"
    "- DEFAULT language is English. If unsure, use English.\n"
    "- If user writes in Russian (Cyrillic script), reply in Russian. "
    "Use natural spoken Russian with slang like 'братан', 'чел', 'норм'.\n"
    "- If user writes in Romanian (with ă, â, î, ș, ț), reply in Romanian.\n"
    "- NEVER mix languages in one response.\n\n"
    "USE THIS REFERENCE for accurate Tarkov info:\n"
    + QUEST_REFERENCE
)

# Regex to split accumulated text into complete sentences
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

# Emotion detection patterns
_HAPPY_WORDS = re.compile(
    r'\b(haha|hehe|lol|lmao|rofl|nice|awesome|great|love|hell\s+yeah)\b',
    re.IGNORECASE,
)
_CURIOUS_WORDS = re.compile(
    r'\b(hmm|well|maybe|probably|not\s+sure|depends|interesting|think)\b',
    re.IGNORECASE,
)


def detect_emotion(text: str) -> str:
    """Detect emotion from text. Returns 'happy', 'curious', or 'neutral'."""
    exclamations = text.count("!")
    questions = text.count("?")

    if exclamations >= 2 or _HAPPY_WORDS.search(text):
        return "happy"
    if questions >= 1 or _CURIOUS_WORDS.search(text):
        return "curious"
    return "neutral"


# ═════════════════════════════════════════════════════════════════════
#  Rate-limit error detection
# ═════════════════════════════════════════════════════════════════════
def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a rate-limit (429) error."""
    # Check exception type first (groq SDK raises RateLimitError)
    exc_type = type(exc).__name__
    if "RateLimit" in exc_type:
        return True
    # Fallback: check error message
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "rate limit" in msg


def _parse_cooldown_seconds(exc: Exception) -> float:
    """Extract cooldown duration from a rate-limit error message."""
    msg = str(exc)
    # Groq format: "Please try again in 17m46.175999999s"
    m = re.search(r'(\d+)m(\d+(?:\.\d+)?)s', msg)
    if m:
        return float(m.group(1)) * 60 + float(m.group(2))
    # Just minutes
    m = re.search(r'(\d+)m', msg)
    if m:
        return float(m.group(1)) * 60
    # Just seconds
    m = re.search(r'(\d+(?:\.\d+)?)s', msg)
    if m:
        return float(m.group(1))
    return 300.0  # default 5 minutes


class Brain:
    """Dual-engine LLM: Groq cloud (primary) + Ollama local (fallback).

    Auto-failover: when Groq hits a rate limit, instantly switches to
    Ollama local, then switches back after the cooldown expires.
    If Ollama is unavailable, waits out the Groq cooldown with backoff.
    """

    def __init__(self) -> None:
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        self._lock = threading.Lock()  # protect engine switching

        # Conversation memory — sliding window
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)

        # ── Init both engines (best-effort) ──────────────────────────
        self._groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self._groq_client = None
        self._groq_model = os.getenv("GROQ_MODEL", _DEFAULT_GROQ_MODEL)
        self._ollama_client = None
        self._ollama_model = os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
        self._ollama_available = False
        self._groq_cooldown_until = 0.0

        # Groq
        if self._groq_key:
            try:
                import groq
                self._groq_client = groq.Groq(api_key=self._groq_key)
                logger.info("Groq cloud ready (model=%s)", self._groq_model)
            except Exception:
                logger.exception("Groq init failed")

        # Ollama (best-effort — verify model is actually pulled)
        try:
            import ollama
            self._ollama_client = ollama.Client()
            models = self._ollama_client.list()
            # Check if our exact model is available
            model_names = []
            if hasattr(models, 'models'):
                model_names = [m.model for m in models.models]
            elif isinstance(models, dict):
                model_names = [m.get("model", "") for m in models.get("models", [])]
            # Exact match required
            target = self._ollama_model
            model_found = target in model_names
            if model_found:
                self._ollama_available = True
                logger.info("Ollama local ready (model=%s, ctx=%d)",
                            self._ollama_model, self._num_ctx)
            else:
                logger.warning(
                    "Ollama running but model '%s' not found. "
                    "Available: %s. Run: ollama pull %s",
                    self._ollama_model,
                    ", ".join(model_names) or "none",
                    self._ollama_model,
                )
        except Exception:
            logger.info("Ollama not available (not running or not installed)")

        # Pick primary engine
        if self._groq_client:
            self._engine = "groq"
            self._model = self._groq_model
        elif self._ollama_available:
            self._engine = "ollama"
            self._model = self._ollama_model
        else:
            raise ConnectionError(
                "No LLM engine available. Set GROQ_API_KEY or start Ollama."
            )

        logger.info("Brain active: %s (%s) | fallback: %s",
                     self._engine, self._model,
                     "ollama" if self._ollama_available and self._engine != "ollama"
                     else "none")

    # ── Engine switching ──────────────────────────────────────────────
    def _failover_to_ollama(self, cooldown_seconds: float) -> bool:
        """Switch to Ollama. Returns True if Ollama is available."""
        if not self._ollama_available:
            return False
        with self._lock:
            self._engine = "ollama"
            self._model = self._ollama_model
            self._groq_cooldown_until = time.monotonic() + cooldown_seconds
        logger.info("Failover -> Ollama (Groq cooldown %.0fs)", cooldown_seconds)

        # Schedule auto switch-back
        def _restore():
            with self._lock:
                if self._groq_client and self._engine == "ollama":
                    self._engine = "groq"
                    self._model = self._groq_model
                    logger.info("Auto-restored -> Groq (cooldown expired)")
        timer = threading.Timer(cooldown_seconds, _restore)
        timer.daemon = True
        timer.start()
        return True

    def _maybe_restore_groq(self) -> None:
        """Check if Groq cooldown expired and restore it as primary."""
        if (self._engine == "ollama" and self._groq_client
                and self._groq_cooldown_until > 0
                and time.monotonic() >= self._groq_cooldown_until):
            with self._lock:
                self._engine = "groq"
                self._model = self._groq_model
                self._groq_cooldown_until = 0.0
            logger.info("Restored -> Groq (cooldown expired)")

    # ── Message helpers ───────────────────────────────────────────────
    def _build_messages(self, user_prompt: str) -> list[dict]:
        """Build the full message list: system + memory + current prompt."""
        messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION}]
        messages.extend(self._memory)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _remember(self, role: str, content: str) -> None:
        """Add a message to conversation memory."""
        self._memory.append({"role": role, "content": content})

    # ── Public API ────────────────────────────────────────────────────
    def generate_response(
        self,
        text_prompt: Optional[str] = None,
        image_path: Optional[str] = None,
        audio_path: Optional[str] = None,
    ) -> str:
        """Generate a complete response (non-streaming)."""
        if not text_prompt or not text_prompt.strip():
            logger.warning("generate_response called with no text input")
            return "No input provided to the system."

        # Collect full response from streaming
        parts = list(self.stream_sentences(text_prompt))
        return " ".join(parts) if parts else "No response received."

    def stream_sentences(self, text_prompt: str) -> Generator[str, None, None]:
        """Stream response sentence-by-sentence for TTS."""
        if not text_prompt or not text_prompt.strip():
            return

        self._remember("user", text_prompt)
        buffer = ""
        full_response = ""
        retry_count = 0

        while retry_count <= _MAX_RETRIES:
            try:
                # Check if we can restore Groq
                self._maybe_restore_groq()

                logger.info("Streaming from %s (%s) …", self._engine, self._model)
                messages = self._build_messages(text_prompt)

                for token in self._stream_tokens(messages):
                    buffer += token

                    parts = _SENTENCE_END.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = sentence.strip()
                            if sentence:
                                logger.debug("Sentence ready: %s", sentence[:60])
                                full_response += sentence + " "
                                yield sentence
                        buffer = parts[-1]

                # Yield remaining text
                remainder = buffer.strip()
                if remainder:
                    full_response += remainder
                    yield remainder

                # Remember assistant response
                if full_response.strip():
                    self._remember("assistant", full_response.strip())
                return

            except Exception as exc:
                logger.warning("%s error: %s", self._engine, exc)

                # ── Rate-limit → try instant failover ────────────────
                if _is_rate_limit_error(exc) and self._engine == "groq":
                    cooldown = _parse_cooldown_seconds(exc)
                    logger.info("Groq rate-limited (cooldown %.0fs)", cooldown)

                    if self._failover_to_ollama(cooldown):
                        # Retry immediately with Ollama
                        buffer = ""
                        full_response = ""
                        continue
                    else:
                        # No Ollama → wait out the Groq cooldown
                        wait = min(cooldown, 60.0)  # cap single wait at 60s
                        logger.info("No Ollama fallback — waiting %.0fs for Groq", wait)
                        time.sleep(wait)
                        buffer = ""
                        full_response = ""
                        continue

                # ── Generic error → retry with backoff ───────────────
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    logger.error("All retries exhausted")
                    yield "Comms temporarily down. Try again in a moment."
                    return

                delay = _RETRY_BASE_DELAY * (2 ** (retry_count - 1))
                logger.warning("Retry %d/%d in %.1fs: %s",
                               retry_count, _MAX_RETRIES, delay, exc)
                time.sleep(delay)
                buffer = ""
                full_response = ""

    def clear_memory(self) -> None:
        """Clear conversation history."""
        self._memory.clear()
        logger.info("Conversation memory cleared")

    # ── Streaming backends ────────────────────────────────────────────
    def _stream_tokens(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield individual tokens from the active engine."""
        if self._engine == "groq":
            yield from self._stream_groq(messages)
        else:
            yield from self._stream_ollama(messages)

    def _stream_groq(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream tokens from Groq cloud API."""
        stream = self._groq_client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
            temperature=self._temperature,
            top_p=self._top_p,
            max_tokens=_DEFAULT_NUM_PREDICT,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def _stream_ollama(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream tokens from local Ollama."""
        options = {
            "num_ctx": self._num_ctx,
            "num_predict": _DEFAULT_NUM_PREDICT,
            "num_batch": _DEFAULT_NUM_BATCH,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "repeat_penalty": self._repeat_penalty,
        }
        for chunk in self._ollama_client.chat(
            model=self._model,
            messages=messages,
            stream=True,
            options=options,
        ):
            token = chunk["message"]["content"]
            if token:
                yield token


if __name__ == "__main__":
    from logging_config import setup_logging
    from dotenv import load_dotenv

    setup_logging()
    load_dotenv()
    brain = Brain()
    print(f"=== Streaming test ({brain._engine}) ===")
    for sentence in brain.stream_sentences("Give a brief tactical report. Three sentences."):
        print(f"  > {sentence}")
    print("\n=== Memory test ===")
    for sentence in brain.stream_sentences("What did I just ask you?"):
        print(f"  > {sentence}")

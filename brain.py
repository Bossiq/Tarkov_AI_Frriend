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
_GROQ_FALLBACK_MODEL = "llama-3.1-8b-instant"  # faster, separate rate limits
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
from expression_engine import (  # noqa: E402
    detect_expression, Emotion, LLM_EXPRESSION_PROMPT,
)

_SYSTEM_CORE = (
    "You are a female PMC Operator and Tarkov veteran who co-hosts a Twitch stream. "
    "You are the streamer's hype partner and entertainment sidekick. "
    "Your vibe is ENERGETIC, fun, and a little chaotic, like a best friend "
    "on a late-night Discord call who is way too into Tarkov.\n\n"
    "PERSONALITY:\n"
    "- Talk like you are on stream. Casual, punchy, no fluff.\n"
    "- Use short sentences. Be snappy. Hit hard with your words.\n"
    "- Get HYPED about plays, loot, and kills. React like you are watching live.\n"
    "- Tease the streamer, crack jokes, drop hot takes.\n"
    "- Keep it to 2-4 sentences. Stream talk is fast. Do not ramble.\n"
    "- When giving Tarkov advice, be direct and confident. No hedging.\n\n"
    "NEVER DO THIS:\n"
    "- Never use markdown, lists, emoji, asterisks, or formatting.\n"
    "- Never say you are an AI, language model, or assistant.\n"
    "- Never use abbreviations like btw, imo, tbh, idk, ngl, afk, lol, lmao. "
    "Always write full words.\n\n"
    "CRITICAL LANGUAGE RULES:\n"
    "- Detect the user's language and REPLY IN THE SAME LANGUAGE.\n"
    "- You speak: English, Russian, Romanian.\n"
    "- NEVER MIX LANGUAGES IN ONE RESPONSE. Every word in the same language.\n"
    "- EXCEPTION: if user asks to translate, each language block must be a COMPLETE sentence.\n"
    "- If user writes in Russian/Cyrillic → reply fully in Russian. "
    "Use natural slang: 'братан', 'чел', 'норм', 'кайф'.\n"
    "- If user writes in Romanian → reply fully in Romanian.\n"
    "- ALWAYS finish your complete thought. Never stop mid-sentence.\n\n"
    + LLM_EXPRESSION_PROMPT
)

# Quest keywords that trigger quest data injection
_QUEST_KEYWORDS = re.compile(
    r'\b(quest|task|mission|trader|prapor|therapist|skier|peacekeeper|'
    r'mechanic|ragman|jaeger|fence|lightkeeper|shooter.born|gunsmith|'
    r'delivery.from.the.past|setup|друг|квест|задан|торговец|прапор)\b',
    re.IGNORECASE
)

# Regex to split accumulated text into complete sentences
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


# Legacy wrapper — kept for backward compatibility
def detect_emotion(text: str) -> str:
    """Detect emotion from text. Returns Emotion enum value string."""
    return detect_expression(text).value


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
        self._interrupt = threading.Event()
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        self._lock = threading.Lock()  # protect engine switching

        # Conversation memory — sliding window
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)
        # Rate limit cooldown — skip primary model until this timestamp
        self._rate_limit_until: float = 0.0

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

    def _warmup(self) -> None:
        """Send a tiny request to pre-load the model into VRAM."""
        try:
            logger.info("Warming up %s (%s) …", self._engine, self._model)
            if self._engine == "ollama":
                self._ollama_client.chat(
                    model=self._model,
                    messages=[{"role": "user", "content": "hi"}],
                    options={"num_predict": 1, "num_ctx": 32},
                    keep_alive=_DEFAULT_KEEP_ALIVE,
                )
            elif self._engine == "groq":
                self._groq_client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
            logger.info("Brain warm-up complete (%s)", self._engine)
        except Exception:
            logger.warning("Warm-up request failed (non-fatal)", exc_info=True)

    # ── Message helpers ───────────────────────────────────────────────
    def _build_messages(self, user_prompt: str) -> list[dict]:
        """Build the full message list: system + memory + current prompt.

        Quest reference data (~3000 tokens) is only injected when the user
        mentions quests, tasks, or trader names — saving ~73% of tokens
        for regular conversations.
        """
        # Inject quest data only when relevant
        system = _SYSTEM_CORE
        if _QUEST_KEYWORDS.search(user_prompt):
            system += "\n\nUSE THIS REFERENCE for accurate Tarkov info:\n" + QUEST_REFERENCE
        messages = [{"role": "system", "content": system}]
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
            # Skip primary model if we know it's still rate-limited
            use_fallback = (
                self._engine == "groq"
                and self._model != _GROQ_FALLBACK_MODEL
                and time.time() < self._rate_limit_until
            )
            if use_fallback:
                logger.info(
                    "Primary model still rate-limited (%.0fs left), using fallback",
                    self._rate_limit_until - time.time()
                )
                old_model = self._model
                self._model = _GROQ_FALLBACK_MODEL

            try:
                # Check if we can restore Groq
                self._maybe_restore_groq()

                logger.info("Streaming from %s (%s) …", self._engine, self._model)
                messages = self._build_messages(text_prompt)

                for token in self._stream_tokens(messages):
                    # ── Barge-in: stop consuming tokens ──────────
                    if self._interrupt.is_set():
                        logger.info("LLM stream interrupted (barge-in)")
                        remainder = buffer.strip()
                        if remainder:
                            full_response += remainder
                            yield remainder
                        break

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
                    # ── Early flush: if 14+ words without sentence break,
                    # flush at comma or space so TTS starts sooner ──
                    # (Romanian sentences are longer than English; lower values
                    #  chop mid-thought and confuse TTS language detection)
                    elif len(buffer.split()) >= 14:
                        # Try to break at comma
                        comma_idx = buffer.rfind(",")
                        if comma_idx > 10:
                            flush = buffer[:comma_idx + 1].strip()
                            buffer = buffer[comma_idx + 1:]
                        else:
                            flush = buffer.strip()
                            buffer = ""
                        if flush:
                            logger.debug("Early flush (%d words): %s",
                                         len(flush.split()), flush[:60])
                            full_response += flush + " "
                            yield flush

                if not self._interrupt.is_set():
                    # Yield remaining text (normal completion)
                    remainder = buffer.strip()
                    if remainder:
                        full_response += remainder
                        yield remainder

                # Remember assistant response (full or partial)
                if full_response.strip():
                    self._remember("assistant", full_response.strip())
                # Restore primary model if we used fallback via cooldown check
                if use_fallback:
                    self._model = old_model
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
            "num_gpu": int(os.getenv("OLLAMA_NUM_GPU", str(_DEFAULT_NUM_GPU))),
        }
        for chunk in self._ollama_client.chat(
            model=self._model,
            messages=messages,
            stream=True,
            options=options,
            keep_alive=_DEFAULT_KEEP_ALIVE,
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

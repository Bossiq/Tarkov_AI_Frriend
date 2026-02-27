"""
AI Brain — dual-engine LLM for PMC Overwatch.

Primary:  Groq cloud API (250+ tokens/sec, free tier)
Fallback: Ollama local inference

Features:
  • Auto-selects Groq if GROQ_API_KEY is set, else Ollama
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
_DEFAULT_NUM_PREDICT = 256
_DEFAULT_NUM_BATCH = 512
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 0.9
_DEFAULT_REPEAT_PENALTY = 1.15
_DEFAULT_NUM_GPU = -1  # -1 = offload all layers to GPU
_DEFAULT_KEEP_ALIVE = "30m"  # keep model in VRAM for 30 min
_MAX_MEMORY = 8
_MAX_RETRIES = 3
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


class Brain:
    """Dual-engine LLM: Groq cloud (primary) + Ollama local (fallback)."""

    def __init__(self) -> None:
        self._interrupt = threading.Event()
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))

        # Conversation memory — sliding window
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)
        # Rate limit cooldown — skip primary model until this timestamp
        self._rate_limit_until: float = 0.0

        # Determine engine
        self._groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self._engine = "groq" if self._groq_key else "ollama"

        if self._engine == "groq":
            self._init_groq()
        else:
            self._init_ollama()

    # ── Engine initialization ────────────────────────────────────────
    def _init_groq(self) -> None:
        """Initialize Groq cloud client.

        max_retries=0 disables the Groq SDK's built-in retry logic.
        We handle retries ourselves (including fallback to a different model)
        so the SDK's 19-27s retry delays don't block our fallback.
        """
        import groq
        self._groq_client = groq.Groq(
            api_key=self._groq_key,
            max_retries=0,  # we handle retries + model fallback ourselves
        )
        self._model = os.getenv("GROQ_MODEL", _DEFAULT_GROQ_MODEL)
        logger.info("Brain using Groq cloud (model=%s)", self._model)

    def _init_ollama(self) -> None:
        """Initialize local Ollama client."""
        import ollama
        self._ollama_client = ollama.Client()
        self._model = os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
        try:
            self._ollama_client.list()
            logger.info("Brain using Ollama local (model=%s, ctx=%d)", self._model, self._num_ctx)
        except Exception as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama. Is it running? "
                f"Launch the Ollama app or run: ollama serve. "
                f"Error: {exc}"
            ) from exc

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
                logger.exception("%s streaming failed", self._engine)
                error_msg = str(exc).lower()

                # Model not found — don't retry
                if "not found" in error_msg or "model" in error_msg and "not" in error_msg:
                    yield f"Model '{self._model}' not available. Check config."
                    return

                # Rate limit — try fallback model instead of retrying same one
                if "rate_limit" in error_msg or "429" in error_msg or "rate limit" in error_msg:
                    if (self._engine == "groq"
                            and self._model != _GROQ_FALLBACK_MODEL):
                        # Cache rate limit cooldown to skip primary model next time
                        try:
                            # Extract retry-after time from error message
                            import re as _re
                            m = _re.search(r'try again in (\d+(?:\.\d+)?)s', error_msg)
                            if not m:
                                m = _re.search(r'try again in (\d+)m', error_msg)
                                cooldown = float(m.group(1)) * 60 if m else 120
                            else:
                                cooldown = float(m.group(1))
                            self._rate_limit_until = time.time() + cooldown
                        except Exception:
                            self._rate_limit_until = time.time() + 120

                        logger.warning(
                            "Rate limited on %s — switching to fallback %s",
                            self._model, _GROQ_FALLBACK_MODEL
                        )
                        old_model = self._model
                        self._model = _GROQ_FALLBACK_MODEL
                        # Retry with fallback model (with TPM retry)
                        max_fallback_attempts = 2
                        for fb_attempt in range(max_fallback_attempts):
                            try:
                                messages = self._build_messages(text_prompt)
                                for token in self._stream_tokens(messages):
                                    if self._interrupt.is_set():
                                        break
                                    buffer += token
                                    full_response += token
                                    # Same sentence splitting logic
                                    parts = _SENTENCE_END.split(buffer)
                                    if len(parts) > 1:
                                        for sentence in parts[:-1]:
                                            s = sentence.strip()
                                            if s:
                                                yield s
                                        buffer = parts[-1]
                                remainder = buffer.strip()
                                if remainder:
                                    full_response += remainder
                                    yield remainder
                                if full_response.strip():
                                    self._remember("assistant", full_response.strip())
                                return
                            except Exception as fb_exc:
                                fb_msg = str(fb_exc).lower()
                                if ("rate_limit" in fb_msg or "429" in fb_msg) and fb_attempt < max_fallback_attempts - 1:
                                    # TPM limit — wait 15s and retry
                                    logger.warning("Fallback TPM limited — waiting 15s …")
                                    time.sleep(15)
                                    continue
                                logger.exception("Fallback model also failed")
                                break
                        self._model = old_model  # restore original
                    # If fallback also failed or not available
                    yield "Rate limited. Try again in a minute."
                    return

                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    yield "Comms error. Check Ollama/Groq status."
                    return

                delay = _RETRY_BASE_DELAY * (2 ** (retry_count - 1))
                logger.warning("Retrying in %.1fs (attempt %d/%d)", delay, retry_count, _MAX_RETRIES)
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

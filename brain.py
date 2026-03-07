"""
AI Brain -- triple-engine LLM for PMC Overwatch.

Engines (in priority order):
  1. Groq cloud API  (250+ tok/s, free tier)
  2. Google Gemini   (1500 req/day free, smart)
  3. Ollama local    (offline fallback)

Features:
  * Automatic failover chain: Groq -> Gemini -> Ollama
  * Context compression (summarize old messages instead of dropping)
  * Streaming sentence-by-sentence for instant TTS
  * Sliding-window conversation memory
  * Retry logic with exponential backoff
  * Never returns comms error if any engine is reachable
  * Screen vision analysis via Gemini Vision API
"""

import logging
import os
import re
import base64
import threading
import time
from collections import deque
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# -- Constants -----------------------------------------------------------------
_DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
_GROQ_FALLBACK_MODEL = "llama-3.1-8b-instant"
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
_DEFAULT_NUM_CTX = 2048
_DEFAULT_NUM_PREDICT = 512
_DEFAULT_NUM_BATCH = 512
_DEFAULT_NUM_GPU = 99
_DEFAULT_KEEP_ALIVE = "10m"
_DEFAULT_TEMPERATURE = 0.6
_DEFAULT_TOP_P = 0.85
_DEFAULT_REPEAT_PENALTY = 1.1
_MAX_MEMORY = 6       # more messages before compression kicks in
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0
_COMPRESS_THRESHOLD = 8  # compress when memory exceeds this many messages

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
    """Triple-engine LLM: Groq -> Gemini -> Ollama.

    Auto-failover chain: if the current engine fails (rate-limit,
    connection error), instantly tries the next engine in the chain.
    Never returns 'comms error' if any engine is reachable.
    """

    # Engine priority order
    _ENGINE_CHAIN = ["groq", "gemini", "ollama"]

    def __init__(self) -> None:
        self._interrupt = threading.Event()
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        self._lock = threading.Lock()

        # Conversation memory -- sliding window
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)
        self._rate_limit_until: float = 0.0

        # -- Engine clients (all best-effort) --
        self._engines: dict[str, bool] = {}  # engine_name -> available

        # Groq
        self._groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self._groq_client = None
        self._groq_model = os.getenv("GROQ_MODEL", _DEFAULT_GROQ_MODEL)
        self._groq_cooldown_until = 0.0
        if self._groq_key:
            try:
                import groq
                self._groq_client = groq.Groq(api_key=self._groq_key)
                self._engines["groq"] = True
                logger.info("Groq cloud ready (model=%s)", self._groq_model)
            except Exception:
                logger.exception("Groq init failed")

        # Gemini
        self._gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self._gemini_client = None
        self._gemini_model = os.getenv("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
        self._gemini_cooldown_until = 0.0
        if self._gemini_key:
            try:
                from google import genai
                self._gemini_client = genai.Client(api_key=self._gemini_key)
                self._engines["gemini"] = True
                logger.info("Gemini ready (model=%s)", self._gemini_model)
            except Exception:
                logger.exception("Gemini init failed")

        # Ollama
        self._ollama_client = None
        self._ollama_model = os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
        self._ollama_available = False
        try:
            import ollama
            self._ollama_client = ollama.Client()
            models = self._ollama_client.list()
            model_names = []
            if hasattr(models, 'models'):
                model_names = [m.model for m in models.models]
            elif isinstance(models, dict):
                model_names = [m.get("model", "") for m in models.get("models", [])]
            if self._ollama_model in model_names:
                self._ollama_available = True
                self._engines["ollama"] = True
                logger.info("Ollama local ready (model=%s, ctx=%d)",
                            self._ollama_model, self._num_ctx)
            else:
                logger.warning(
                    "Ollama running but model '%s' not found. Available: %s",
                    self._ollama_model, ", ".join(model_names) or "none")
        except Exception:
            logger.info("Ollama not available")

        # Pick primary engine (first available in chain)
        self._engine = None
        self._model = None
        for eng in self._ENGINE_CHAIN:
            if self._engines.get(eng):
                self._engine = eng
                self._model = self._get_model_for_engine(eng)
                break
        if not self._engine:
            raise ConnectionError(
                "No LLM engine available. Set GROQ_API_KEY, GEMINI_API_KEY, or start Ollama."
            )

        available = [e for e in self._ENGINE_CHAIN if self._engines.get(e)]
        logger.info("Brain active: %s (%s) | engines: %s",
                     self._engine, self._model, " -> ".join(available))

        # Vision state
        self._last_vision_time: float = 0.0
        self._last_vision_result: str = ""

    def _get_model_for_engine(self, engine: str) -> str:
        return {"groq": self._groq_model, "gemini": self._gemini_model,
                "ollama": self._ollama_model}.get(engine, "")

    # -- Failover chain --------------------------------------------------------
    def _next_engine(self, current: str) -> Optional[str]:
        """Get the next available engine in the failover chain."""
        chain = self._ENGINE_CHAIN
        try:
            idx = chain.index(current)
        except ValueError:
            idx = -1
        for eng in chain[idx + 1:] + chain[:idx]:
            if eng != current and self._engines.get(eng):
                return eng
        return None

    def _switch_engine(self, target: str, cooldown_source: Optional[str] = None,
                       cooldown_seconds: float = 0) -> bool:
        """Switch to a specific engine. Returns True if successful."""
        if not self._engines.get(target):
            return False
        with self._lock:
            self._engine = target
            self._model = self._get_model_for_engine(target)
            if cooldown_source == "groq":
                self._groq_cooldown_until = time.monotonic() + cooldown_seconds
            elif cooldown_source == "gemini":
                self._gemini_cooldown_until = time.monotonic() + cooldown_seconds
        logger.info("Switched -> %s (%s)", target, self._model)

        # Schedule auto-restore if there's a cooldown
        if cooldown_source and cooldown_seconds > 0:
            def _restore():
                with self._lock:
                    if self._engines.get(cooldown_source):
                        self._engine = cooldown_source
                        self._model = self._get_model_for_engine(cooldown_source)
                        logger.info("Cooldown expired -> restored %s", cooldown_source)
            timer = threading.Timer(cooldown_seconds, _restore)
            timer.daemon = True
            timer.start()
        return True

    def _failover(self, failed_engine: str, exc: Exception) -> bool:
        """Try to failover to the next available engine. Returns True if switched."""
        cooldown = 0.0
        if _is_rate_limit_error(exc):
            cooldown = _parse_cooldown_seconds(exc)

        next_eng = self._next_engine(failed_engine)
        if next_eng and self._switch_engine(
            next_eng, cooldown_source=failed_engine, cooldown_seconds=cooldown
        ):
            logger.info("Failover: %s -> %s (cooldown %.0fs)",
                        failed_engine, next_eng, cooldown)
            return True
        return False

    def _warmup(self) -> None:
        """Send a tiny request to pre-load the model."""
        try:
            logger.info("Warming up %s (%s)", self._engine, self._model)
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
            elif self._engine == "gemini":
                self._gemini_client.models.generate_content(
                    model=self._gemini_model,
                    contents="hi",
                    config={"max_output_tokens": 1},
                )
            logger.info("Brain warm-up complete (%s)", self._engine)
        except Exception:
            logger.warning("Warm-up failed (non-fatal)", exc_info=True)

    # -- Context compression ---------------------------------------------------
    def _maybe_compress_memory(self) -> None:
        """If memory is too large, compress older messages into a summary."""
        if len(self._memory) < _COMPRESS_THRESHOLD:
            return

        # Take the oldest half of messages for compression
        half = len(self._memory) // 2
        old_msgs = list(self._memory)[:half]

        # Build a simple summary from the old messages
        summary_parts = []
        for msg in old_msgs:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                summary_parts.append(f"User asked: {content[:100]}")
            else:
                summary_parts.append(f"AI replied: {content[:100]}")

        summary = "[Earlier conversation summary: " + " | ".join(summary_parts) + "]"

        # Remove old messages and prepend summary
        for _ in range(half):
            self._memory.popleft()
        self._memory.appendleft({"role": "system", "content": summary})
        logger.info("Memory compressed: %d messages -> summary + %d recent",
                    half, len(self._memory) - 1)

    # -- Message helpers -------------------------------------------------------
    def _build_messages(self, user_prompt: str) -> list[dict]:
        """Build the full message list: system + memory + current prompt."""
        # Compress memory if it's getting large
        self._maybe_compress_memory()

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
            use_fallback = (
                self._engine == "groq"
                and self._model != _GROQ_FALLBACK_MODEL
                and time.time() < self._rate_limit_until
            )
            if use_fallback:
                logger.info("Primary model rate-limited, using Groq fallback")
                old_model = self._model
                self._model = _GROQ_FALLBACK_MODEL

            try:
                logger.info("Streaming from %s (%s)", self._engine, self._model)
                messages = self._build_messages(text_prompt)

                for token in self._stream_tokens(messages):
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
                                logger.debug("Sentence: %s", sentence[:60])
                                full_response += sentence + " "
                                yield sentence
                        buffer = parts[-1]
                    elif len(buffer.split()) >= 14:
                        comma_idx = buffer.rfind(",")
                        if comma_idx > 10:
                            flush = buffer[:comma_idx + 1].strip()
                            buffer = buffer[comma_idx + 1:]
                        else:
                            flush = buffer.strip()
                            buffer = ""
                        if flush:
                            full_response += flush + " "
                            yield flush

                if not self._interrupt.is_set():
                    remainder = buffer.strip()
                    if remainder:
                        full_response += remainder
                        yield remainder

                if full_response.strip():
                    self._remember("assistant", full_response.strip())
                if use_fallback:
                    self._model = old_model
                return

            except Exception as exc:
                logger.warning("%s error: %s", self._engine, exc)

                # Restore model name if we were using fallback
                if use_fallback:
                    self._model = old_model

                # -- Try failover to next engine in chain --
                if self._failover(self._engine, exc):
                    buffer = ""
                    full_response = ""
                    continue

                # -- No failover available, retry with backoff --
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    logger.error("All retries exhausted on all engines")
                    yield "Comms temporarily down. Try again in a moment."
                    return

                delay = _RETRY_BASE_DELAY * (2 ** (retry_count - 1))
                logger.warning("Retry %d/%d in %.1fs", retry_count, _MAX_RETRIES, delay)
                time.sleep(delay)
                buffer = ""
                full_response = ""

    def clear_memory(self) -> None:
        """Clear conversation history."""
        self._memory.clear()
        logger.info("Conversation memory cleared")

    # ── Screen Vision Analysis ─────────────────────────────────────────
    _VISION_PROMPT = (
        "You are an AI co-host watching Escape from Tarkov gameplay live. "
        "Look at this screenshot and give a SHORT, ENERGETIC reaction (1-2 sentences). "
        "Focus on WHAT IS HAPPENING RIGHT NOW: combat, looting, injuries, map location, "
        "inventory management, trader screens, or menu screens.\n\n"
        "RULES:\n"
        "- Be casual and hyped like a Twitch co-host.\n"
        "- If nothing interesting is happening, say NOTHING (return empty).\n"
        "- Never use markdown, lists, or formatting.\n"
        "- Never say 'I can see' or 'screenshot shows'. Talk like you're watching live.\n"
        "- If you see combat or danger, react with urgency!\n"
        "- If it's a menu or loading screen, stay quiet (return empty).\n"
    )

    _VISION_COOLDOWN = 10.0  # seconds between vision requests

    def __init_vision__(self) -> None:
        """Initialize vision-related state (called from __init__)."""
        self._last_vision_time: float = 0.0
        self._last_vision_result: str = ""

    def analyze_screen(self, frame_path: str) -> Optional[str]:
        """Analyze a gameplay screenshot via Gemini Vision.

        Returns a short reaction string, or None if:
          - Gemini is not available
          - Cooldown hasn't elapsed
          - Nothing interesting was detected

        Uses Gemini exclusively (only engine with vision support).
        """
        if not self._gemini_client:
            return None

        # Rate limit
        now = time.monotonic()
        if now - self._last_vision_time < self._VISION_COOLDOWN:
            return None
        self._last_vision_time = now

        try:
            # Read and encode the image
            with open(frame_path, "rb") as f:
                image_bytes = f.read()

            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            # Send to Gemini Vision
            response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": self._VISION_PROMPT},
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": image_b64,
                                }
                            },
                        ],
                    }
                ],
                config={"max_output_tokens": 100, "temperature": 0.7},
            )

            result = response.text.strip() if response.text else ""

            # Filter out empty / non-interesting responses
            if not result or len(result) < 5:
                return None

            self._last_vision_result = result
            logger.info("Vision analysis: %s", result[:80])
            return result

        except Exception:
            logger.debug("Vision analysis failed", exc_info=True)
            return None

    def get_screen_context(self, frame_path: Optional[str]) -> str:
        """Get a brief screen context string to append to user prompts.

        This gives the AI awareness of what's on screen during
        normal voice interactions.
        """
        if not frame_path or not self._gemini_client:
            return ""

        try:
            with open(frame_path, "rb") as f:
                image_bytes = f.read()

            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Describe this Tarkov gameplay screenshot in "
                                    "one brief sentence. Focus only on the key visible "
                                    "game state (combat, inventory, map area, health). "
                                    "If it's a menu or loading screen, say 'menu'.\n"
                                )
                            },
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": image_b64,
                                }
                            },
                        ],
                    }
                ],
                config={"max_output_tokens": 50, "temperature": 0.3},
            )

            ctx = response.text.strip() if response.text else ""
            if ctx and ctx.lower() != "menu":
                return f"\n[Screen context: {ctx}]"
            return ""

        except Exception:
            logger.debug("Screen context failed", exc_info=True)
            return ""

    # -- Streaming backends ----------------------------------------------------
    def _stream_tokens(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield individual tokens from the active engine."""
        if self._engine == "groq":
            yield from self._stream_groq(messages)
        elif self._engine == "gemini":
            yield from self._stream_gemini(messages)
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

    def _stream_gemini(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream tokens from Google Gemini API."""
        # Convert messages to Gemini format
        system_msg = ""
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg += msg["content"] + "\n"
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})

        config = {
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_output_tokens": _DEFAULT_NUM_PREDICT,
        }
        if system_msg:
            config["system_instruction"] = system_msg.strip()

        response = self._gemini_client.models.generate_content_stream(
            model=self._gemini_model,
            contents=contents,
            config=config,
        )
        for chunk in response:
            if chunk.text:
                yield chunk.text

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

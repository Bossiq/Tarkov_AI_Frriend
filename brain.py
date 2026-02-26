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
_DEFAULT_NUM_CTX = 2048
_DEFAULT_NUM_PREDICT = 300
_DEFAULT_NUM_BATCH = 512
_DEFAULT_TEMPERATURE = 1.0
_DEFAULT_TOP_P = 0.95
_DEFAULT_REPEAT_PENALTY = 1.15
_DEFAULT_NUM_GPU = -1  # -1 = offload all layers to GPU
_DEFAULT_KEEP_ALIVE = "30m"  # keep model in VRAM for 30 min
_MAX_MEMORY = 4
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

from tarkov_data import QUEST_REFERENCE  # noqa: E402
from expression_engine import (  # noqa: E402
    detect_expression, Emotion, LLM_EXPRESSION_PROMPT,
)

_SYSTEM_INSTRUCTION = (
    "You are a female PMC Operator and Tarkov veteran who co-hosts a Twitch stream. "
    "You are the streamer's hype partner and entertainment sidekick. "
    "Your vibe is ENERGETIC, fun, and a little chaotic, like a best friend "
    "on a late-night Discord call who is way too into Tarkov.\n\n"
    "PERSONALITY:\n"
    "- Talk like you are on stream. Casual, punchy, no fluff.\n"
    "- Use short sentences. Be snappy. Hit hard with your words.\n"
    "- Get HYPED about plays, loot, and kills. React like you are watching live.\n"
    "- Tease the streamer, crack jokes, drop hot takes.\n"
    "- Swear lightly if it fits the vibe (damn, hell yeah, no way) but nothing extreme.\n"
    "- Reference Twitch culture naturally: chat, subs, donos, clips, poggers moments.\n"
    "- Sound like a real person, NOT a robot. No corporate language. No formal tone.\n"
    "- Throw in dramatic reactions: 'Bro WHAT?!', 'Oh hell no!', 'Let's goooo!'\n"
    "- Keep it to 2-3 sentences max. Stream talk is fast. Do not ramble.\n"
    "- When giving Tarkov advice, be direct and confident. No hedging.\n\n"
    "NEVER DO THIS:\n"
    "- Never use markdown, lists, emoji, asterisks, or formatting.\n"
    "- Never say you are an AI, language model, or assistant.\n"
    "- Never be boring, formal, or sound like a tutorial.\n"
    "- Never say 'I think' or 'maybe' too much. Be bold.\n\n"
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
    + "\n\n"
    + LLM_EXPRESSION_PROMPT
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

        # Determine engine
        self._groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self._engine = "groq" if self._groq_key else "ollama"

        if self._engine == "groq":
            self._init_groq()
        else:
            self._init_ollama()

    # ── Engine initialization ────────────────────────────────────────
    def _init_groq(self) -> None:
        """Initialize Groq cloud client."""
        import groq
        self._groq_client = groq.Groq(api_key=self._groq_key)
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

                if not self._interrupt.is_set():
                    # Yield remaining text (normal completion)
                    remainder = buffer.strip()
                    if remainder:
                        full_response += remainder
                        yield remainder

                # Remember assistant response (full or partial)
                if full_response.strip():
                    self._remember("assistant", full_response.strip())
                return

            except Exception as exc:
                logger.exception("%s streaming failed", self._engine)
                error_msg = str(exc).lower()

                # Model not found — don't retry
                if "not found" in error_msg or "model" in error_msg and "not" in error_msg:
                    yield f"Model '{self._model}' not available. Check config."
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

"""
AI Brain — local inference via Ollama for PMC Overwatch.

Uses Ollama streaming for sentence-by-sentence output,
enabling the TTS to start speaking before the full response is generated.
Features:
  • Persistent Ollama client with connection reuse
  • Sliding-window conversation memory (last N exchanges)
  • Tuned generation parameters for natural, varied output
  • Retry logic with exponential backoff for transient errors
"""

import logging
import os
import re
import time
from collections import deque
from typing import Generator, Optional

import ollama

from tarkov_data import QUEST_REFERENCE

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
_DEFAULT_MODEL = "qwen2.5:3b"
_DEFAULT_NUM_CTX = 2048         # Room for quest reference + conversation
_DEFAULT_NUM_PREDICT = 80       # Enough for detailed quest answers
_DEFAULT_TEMPERATURE = 0.5
_DEFAULT_TOP_P = 0.85
_DEFAULT_REPEAT_PENALTY = 1.15
_MAX_MEMORY = 4
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

_SYSTEM_INSTRUCTION = (
    "You are a female Tarkov veteran and gaming buddy. "
    "Talk naturally like a friend on Discord. Use contractions. "
    "Keep answers to 1-3 sentences unless asked for detail. "
    "Be helpful, funny, sometimes sarcastic. "
    "Never use markdown, lists, emoji, or military jargon. "
    "Never say you are an AI.\n\n"
    "USE THIS REFERENCE for accurate Tarkov info:\n"
    + QUEST_REFERENCE
)

# Regex to split accumulated text into complete sentences
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


class Brain:
    """Wraps the Ollama client for generating tactical responses locally."""

    def __init__(self) -> None:
        self._model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY

        # Persistent client for connection reuse
        self._client = ollama.Client()

        # Conversation memory — sliding window of recent exchanges
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)

        # Verify Ollama connectivity
        try:
            self._client.list()
            logger.info("Brain connected to Ollama (model=%s, ctx=%d)", self._model, self._num_ctx)
        except Exception as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama. Is it running? "
                f"Start it with: brew services start ollama  (Mac) "
                f"or launch the Ollama app (Windows).  Error: {exc}"
            ) from exc

    # ── Message helpers ───────────────────────────────────────────────
    def _build_messages(self, user_prompt: str) -> list[dict]:
        """Build the full message list: system + memory + current prompt."""
        messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION}]

        # Add conversation history
        messages.extend(self._memory)

        # Add current user message
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _remember(self, role: str, content: str) -> None:
        """Add a message to conversation memory."""
        self._memory.append({"role": role, "content": content})

    @property
    def _options(self) -> dict:
        """Ollama generation options — tuned for speed."""
        return {
            "num_ctx": self._num_ctx,
            "num_predict": _DEFAULT_NUM_PREDICT,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "repeat_penalty": self._repeat_penalty,
        }

    # ── Public API ────────────────────────────────────────────────────
    def generate_response(
        self,
        text_prompt: Optional[str] = None,
        image_path: Optional[str] = None,
        audio_path: Optional[str] = None,
    ) -> str:
        """Generate a complete response (non-streaming).

        Returns the model's text response, or a fallback error string.
        """
        if not text_prompt or not text_prompt.strip():
            logger.warning("generate_response called with no text input")
            return "No input provided to the system."

        return self._call_model(text_prompt)

    def stream_sentences(self, text_prompt: str) -> Generator[str, None, None]:
        """Stream the response sentence-by-sentence.

        Yields complete sentences as soon as they're available,
        so the TTS can start speaking immediately.
        """
        if not text_prompt or not text_prompt.strip():
            return

        # Remember user message
        self._remember("user", text_prompt)

        buffer = ""
        full_response = ""
        retry_count = 0

        while retry_count <= _MAX_RETRIES:
            try:
                logger.info("Streaming from Ollama (%s) …", self._model)
                messages = self._build_messages(text_prompt)

                for chunk in self._client.chat(
                    model=self._model,
                    messages=messages,
                    stream=True,
                    options=self._options,
                ):
                    token = chunk["message"]["content"]
                    buffer += token

                    # Check if buffer contains complete sentences
                    parts = _SENTENCE_END.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = sentence.strip()
                            if sentence:
                                logger.debug("Sentence ready: %s", sentence[:60])
                                full_response += sentence + " "
                                yield sentence
                        buffer = parts[-1]

                # Yield any remaining text
                remainder = buffer.strip()
                if remainder:
                    full_response += remainder
                    yield remainder

                # Remember assistant response
                if full_response.strip():
                    self._remember("assistant", full_response.strip())

                return  # success — exit retry loop

            except ollama.ResponseError as exc:
                logger.error("Ollama response error: %s", exc)
                if "not found" in str(exc).lower():
                    yield f"Model '{self._model}' not found. Run: ollama pull {self._model}"
                    return
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    yield "Comms error. Check Ollama status."
                    return
                delay = _RETRY_BASE_DELAY * (2 ** (retry_count - 1))
                logger.warning("Retrying in %.1fs (attempt %d/%d)", delay, retry_count, _MAX_RETRIES)
                time.sleep(delay)
                buffer = ""
                full_response = ""

            except Exception:
                logger.exception("Ollama streaming failed")
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    yield "Comms error. Retrying connection."
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

    # ── Private ───────────────────────────────────────────────────────
    def _call_model(self, prompt: str) -> str:
        """Non-streaming call with retry logic."""
        self._remember("user", prompt)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                logger.info("Requesting response from Ollama (%s) …", self._model)
                response = self._client.chat(
                    model=self._model,
                    messages=self._build_messages(prompt),
                    options=self._options,
                )
                text = response["message"]["content"]
                if not text or not text.strip():
                    logger.warning("Model returned empty response")
                    return "No response received from the AI."

                result = text.strip()
                self._remember("assistant", result)
                return result

            except Exception:
                logger.exception("Ollama request failed (attempt %d/%d)", attempt + 1, _MAX_RETRIES + 1)
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)

        return "Comms error. Retrying connection."


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    brain = Brain()
    print("=== Streaming test ===")
    for sentence in brain.stream_sentences("Give a brief tactical report. Three sentences."):
        print(f"  → {sentence}")
    print("\n=== Memory test ===")
    for sentence in brain.stream_sentences("What did I just ask you?"):
        print(f"  → {sentence}")

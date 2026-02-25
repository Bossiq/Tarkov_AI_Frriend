"""
AI Brain — local inference via Ollama for PMC Overwatch.

Uses Ollama streaming for sentence-by-sentence output,
enabling the TTS to start speaking before the full response is generated.
"""

import logging
import os
import re
from typing import Generator, Optional

import ollama

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
_DEFAULT_MODEL = "mistral"
_SYSTEM_INSTRUCTION = (
    "You are a highly serious, battle-hardened Veteran PMC Operator providing tactical overwatch. "
    "Speak ONLY in English. NEVER use Russian slang. "
    "Be concise, professional, and tactical. "
    "Keep responses under 3 sentences unless more detail is explicitly requested."
)

# Regex to split accumulated text into complete sentences
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


class Brain:
    """Wraps the Ollama client for generating tactical responses locally."""

    def __init__(self) -> None:
        self._model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)

        try:
            ollama.list()
            logger.info("Brain connected to Ollama (model=%s)", self._model)
        except Exception as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama. Is it running? "
                f"Start it with: brew services start ollama  (Mac) "
                f"or launch the Ollama app (Windows).  Error: {exc}"
            ) from exc

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

        buffer = ""
        try:
            logger.info("Streaming from Ollama (%s) …", self._model)
            for chunk in ollama.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {"role": "user", "content": text_prompt},
                ],
                stream=True,
            ):
                token = chunk["message"]["content"]
                buffer += token

                # Check if buffer contains complete sentences
                parts = _SENTENCE_END.split(buffer)
                if len(parts) > 1:
                    # Yield all complete sentences, keep the remainder
                    for sentence in parts[:-1]:
                        sentence = sentence.strip()
                        if sentence:
                            logger.debug("Sentence ready: %s", sentence[:60])
                            yield sentence
                    buffer = parts[-1]

            # Yield any remaining text
            remainder = buffer.strip()
            if remainder:
                yield remainder

        except ollama.ResponseError as exc:
            logger.error("Ollama response error: %s", exc)
            if "not found" in str(exc).lower():
                yield f"Model '{self._model}' not found. Run: ollama pull {self._model}"
            else:
                yield "Comms error. Check Ollama status."
        except Exception:
            logger.exception("Ollama streaming failed")
            yield "Comms error. Retrying connection."

    # ── Private ───────────────────────────────────────────────────────
    def _call_model(self, prompt: str) -> str:
        """Non-streaming call — used as fallback."""
        try:
            logger.info("Requesting response from Ollama (%s) …", self._model)
            response = ollama.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt},
                ],
            )
            text = response["message"]["content"]
            if not text or not text.strip():
                logger.warning("Model returned empty response")
                return "No response received from the AI."
            return text.strip()
        except Exception:
            logger.exception("Ollama request failed")
            return "Comms error. Retrying connection."


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    brain = Brain()
    print("=== Streaming test ===")
    for sentence in brain.stream_sentences("Give a brief tactical report. Three sentences."):
        print(f"  → {sentence}")

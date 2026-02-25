"""
AI Brain — local inference via Ollama for PMC Overwatch.

Sends chat requests to a locally running Ollama instance.
Fully offline — no API keys, no cloud dependencies.
"""

import logging
import os
from typing import Optional

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


class Brain:
    """Wraps the Ollama client for generating tactical responses locally."""

    def __init__(self) -> None:
        self._model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)

        # Verify Ollama is reachable
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
        """Generate a response from a text prompt.

        Audio must be pre-transcribed to text before calling this method.
        Image support is deferred to a future vision-model upgrade.

        Returns the model's text response, or a fallback error string.
        """
        if not text_prompt or not text_prompt.strip():
            logger.warning("generate_response called with no text input")
            return "No input provided to the system."

        return self._call_model(text_prompt)

    # ── Private ───────────────────────────────────────────────────────
    def _call_model(self, prompt: str) -> str:
        """Send a chat request to Ollama and return the text response."""
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
        except ollama.ResponseError as exc:
            logger.error("Ollama response error: %s", exc)
            if "not found" in str(exc).lower():
                return (
                    f"Model '{self._model}' not found. "
                    f"Run: ollama pull {self._model}"
                )
            return "Comms error. Check Ollama status."
        except Exception:
            logger.exception("Ollama request failed")
            return "Comms error. Retrying connection."


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    brain = Brain()
    print(brain.generate_response(text_prompt="Say hello like a true PMC operator."))

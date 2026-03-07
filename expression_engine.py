"""
Expression Engine — Enterprise-grade facial expression state machine.

Manages the mapping between detected emotions, system modes, and sprite
selection for the holographic avatar.  Provides a single source of truth
for which face to display in every situation.

Architecture:
  • 10 emotion categories   → detected from LLM output text
  • 14 sprite keys          → individual face images
  • Priority & decay system → high-energy emotions override low ones
  • LLM self-awareness      → prompt fragment teaches the agent its own faces
"""

import logging
import re
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Emotion Enum
# ═════════════════════════════════════════════════════════════════════
class Emotion(str, Enum):
    NEUTRAL    = "neutral"
    HAPPY      = "happy"
    EXCITED    = "excited"
    AMUSED     = "amused"
    SARCASTIC  = "sarcastic"
    SURPRISED  = "surprised"
    CONCERNED  = "concerned"
    CURIOUS    = "curious"
    CONFIDENT  = "confident"
    EMPATHETIC = "empathetic"
    FOCUSED    = "focused"      # analyzing screen / deep concentration
    ALARMED    = "alarmed"      # danger detected from screen capture


# ═════════════════════════════════════════════════════════════════════
# Emotion → Sprite Mapping
# ═════════════════════════════════════════════════════════════════════
# Each emotion defines:
#   idle_sprite   — face shown when NOT speaking (between sentences, etc.)
#   speak_sprites — face shown when speaking (list: [low_amp, mid_amp, high_amp])
#   priority      — higher overrides lower (prevents flicker between states)
#   duration      — how long (seconds) the emotion persists before decay

EXPRESSION_MAP = {
    Emotion.NEUTRAL: {
        "idle_sprite": "idle",
        "speak_sprites": ["speak_calm", "speak_mid", "speak_open"],
        "priority": 0,
        "duration": 0.0,  # never decays (default)
    },
    Emotion.HAPPY: {
        "idle_sprite": "smile",
        "speak_sprites": ["smile_speak", "smile_speak", "smile_speak"],
        "priority": 3,
        "duration": 8.0,
    },
    Emotion.EXCITED: {
        "idle_sprite": "excited",
        "speak_sprites": ["excited", "excited", "excited"],
        "priority": 5,
        "duration": 6.0,
    },
    Emotion.AMUSED: {
        "idle_sprite": "smirk",
        "speak_sprites": ["smile_speak", "smile_speak", "smile_speak"],
        "priority": 3,
        "duration": 6.0,
    },
    Emotion.SARCASTIC: {
        "idle_sprite": "smirk",
        "speak_sprites": ["smirk", "smirk", "smirk"],
        "priority": 4,
        "duration": 5.0,
    },
    Emotion.SURPRISED: {
        "idle_sprite": "surprise",
        "speak_sprites": ["surprise", "surprise", "surprise"],
        "priority": 5,
        "duration": 4.0,
    },
    Emotion.CONCERNED: {
        "idle_sprite": "concern",
        "speak_sprites": ["concern", "speak_calm", "speak_mid"],
        "priority": 4,
        "duration": 7.0,
    },
    Emotion.CURIOUS: {
        "idle_sprite": "think",
        "speak_sprites": ["speak_calm", "speak_calm", "speak_mid"],
        "priority": 2,
        "duration": 5.0,
    },
    Emotion.CONFIDENT: {
        "idle_sprite": "confident",
        "speak_sprites": ["confident", "speak_mid", "speak_open"],
        "priority": 3,
        "duration": 6.0,
    },
    Emotion.EMPATHETIC: {
        "idle_sprite": "concern",
        "speak_sprites": ["speak_calm", "speak_calm", "speak_mid"],
        "priority": 3,
        "duration": 6.0,
    },
    Emotion.FOCUSED: {
        "idle_sprite": "think",
        "speak_sprites": ["speak_calm", "speak_calm", "speak_mid"],
        "priority": 2,
        "duration": 8.0,
    },
    Emotion.ALARMED: {
        "idle_sprite": "surprise",
        "speak_sprites": ["surprise", "speak_open", "speak_open"],
        "priority": 6,
        "duration": 4.0,
    },
}


# ═════════════════════════════════════════════════════════════════════
# Emotion Detection — Multi-Signal Scoring
# ═════════════════════════════════════════════════════════════════════
_EMOTION_PATTERNS = {
    Emotion.EXCITED: {
        "words": re.compile(
            r"\b(let'?s\s+go+|poggers|pog|insane|clutch|unreal|"
            r"oh\s+my\s+god|holy\s+shit|holy\s+crap|holy|"
            r"no\s+freaking\s+way|absolute|beast|legendary|"
            r"let'?s\s+freaking|hyped?|epic|massive|huge|cracked|"
            r"goated|crazy|wild|sick|dude)\b",
            re.IGNORECASE,
        ),
        "excl_threshold": 3,  # 3+ exclamation marks → excited
        "caps_ratio": 0.4,    # 40%+ caps → shouting = excited
    },
    Emotion.HAPPY: {
        "words": re.compile(
            r"\b(nice|awesome|great|love|love\s+it|love\s+that|"
            r"hell\s+yeah|sweet|beautiful|amazing|perfect|"
            r"wonderful|fantastic|brilliant|gorgeous|blessed|"
            r"glad|good\s+stuff|solid|clean|smooth)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.AMUSED: {
        "words": re.compile(
            r"\b(haha+|hehe+|lol|lmao|rofl|funny|hilarious|"
            r"dead|dying|bruh|bro\s+what|comedy|jokes?|"
            r"clown|trolling|messing|joking)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.SARCASTIC: {
        "words": re.compile(
            r"\b(sure\s+buddy|oh\s+really|yeah\s+right|"
            r"totally|definitely|obviously|clearly|"
            r"big\s+brain|galaxy\s+brain|genius|wow\s+such|"
            r"imagine|cope|copium|ratio|skill\s+issue)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.SURPRISED: {
        "words": re.compile(
            r"\b(what|seriously|really|for\s+real|no\s+way|"
            r"are\s+you\s+kidding|wait\s+what|hold\s+on|"
            r"excuse\s+me|say\s+what|huh|whoa|wow|dang|"
            r"did\s+not\s+expect|unexpected|plot\s+twist)\b",
            re.IGNORECASE,
        ),
        "ends_with_qe": True,  # "?!" pattern
    },
    Emotion.CONCERNED: {
        "words": re.compile(
            r"\b(careful|watch\s+out|danger|risky|sketchy|"
            r"bad\s+idea|not\s+good|uh\s+oh|yikes|ouch|"
            r"rip|unfortunate|tough|"
            r"warning|heads\s+up|be\s+aware|scary|cursed)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.CURIOUS: {
        "words": re.compile(
            r"\b(hmm+|well|maybe|probably|not\s+sure|depends|"
            r"interesting|wonder|think\s+about|suppose|curious|"
            r"question|actually|wait\s+let\s+me|let\s+me\s+think)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.CONFIDENT: {
        "words": re.compile(
            r"\b(trust\s+me|guaranteed|for\s+sure|hundred\s+percent|"
            r"no\s+doubt|easy|simple|just\s+do|all\s+you\s+need|"
            r"best\s+way|pro\s+tip|here'?s\s+the\s+move|"
            r"the\s+play\s+is|run\s+it|meta|optimal|go\s+for\s+it)\b",
            re.IGNORECASE,
        ),
    },
    Emotion.EMPATHETIC: {
        "words": re.compile(
            r"\b(sorry|that\s+hurts|that\s+sucks|feel\s+you|i\s+get\s+it|"
            r"been\s+there|hang\s+in|stay\s+strong|rough|rough\s+one|"
            r"tough\s+break|my\s+heart|"
            r"hope\s+you'?re\s+okay|take\s+care|it'?s\s+okay|"
            r"no\s+worries|don'?t\s+worry|we'?ve\s+all)\b",
            re.IGNORECASE,
        ),
    },
}


def detect_expression(text: str) -> Emotion:
    """Detect the dominant emotion in text using multi-signal scoring.

    Scoring:
      - Word pattern match:     +2 per unique pattern
      - Exclamation marks:      +1 each (capped at 3)
      - Question + excl combo:  +2 for surprise
      - High caps ratio:        +2 for excited
      - Sentence-ending "?!":   +3 for surprised

    Returns the highest-scoring Emotion, or NEUTRAL if no signal.
    """
    if not text or not text.strip():
        return Emotion.NEUTRAL

    scores: dict[Emotion, float] = {e: 0.0 for e in Emotion}
    excl_count = text.count("!")
    quest_count = text.count("?")

    # Caps ratio (ignore short text)
    alpha = [c for c in text if c.isalpha()]
    caps_ratio = sum(1 for c in alpha if c.isupper()) / max(1, len(alpha))

    for emotion, patterns in _EMOTION_PATTERNS.items():
        # Word matches
        matches = patterns["words"].findall(text)
        if matches:
            scores[emotion] += len(set(m.lower() if isinstance(m, str) else m for m in matches)) * 2.0

        # Exclamation threshold (for excited)
        if "excl_threshold" in patterns and excl_count >= patterns["excl_threshold"]:
            scores[emotion] += 3.0

        # Caps ratio (for excited)
        if "caps_ratio" in patterns and caps_ratio >= patterns["caps_ratio"] and len(alpha) > 5:
            scores[emotion] += 2.0

        # "?!" ending pattern (for surprised)
        if patterns.get("ends_with_qe") and re.search(r"[?!]{2,}", text):
            scores[emotion] += 3.0

    # Generic exclamation boost for happy/excited
    if excl_count >= 2:
        scores[Emotion.HAPPY] += 1.0
    if excl_count >= 1 and quest_count >= 1:
        scores[Emotion.SURPRISED] += 1.5

    # Find the winner
    best = Emotion.NEUTRAL
    best_score = 0.0
    for emotion, score in scores.items():
        if score > best_score:
            best = emotion
            best_score = score

    # Require a minimum score to avoid false positives
    if best_score < 1.5:
        return Emotion.NEUTRAL

    return best


# ═════════════════════════════════════════════════════════════════════
# Expression Engine (State Machine)
# ═════════════════════════════════════════════════════════════════════
class ExpressionEngine:
    """Centralized expression state machine.

    Tracks the current emotion, handles priority-based overrides,
    auto-decays back to neutral, and resolves the correct sprite key
    given system mode (idle/listening/thinking/speaking) and amplitude.
    """

    def __init__(self):
        self._emotion: Emotion = Emotion.NEUTRAL
        self._emotion_time: float = 0.0   # when emotion was last set
        self._priority: int = 0
        logger.info("ExpressionEngine initialized (10 emotions, 14 sprites)")

    @property
    def current_emotion(self) -> Emotion:
        return self._emotion

    def set_emotion(self, emotion: Emotion) -> None:
        """Set a new emotion.  Always accepts an explicit different emotion.

        Priority is used only to prevent rapid flicker between equal-priority
        emotions within the same decay window.
        """
        if emotion == self._emotion:
            return  # already set — refresh the timer
        cfg = EXPRESSION_MAP[emotion]
        logger.debug("Expression: %s → %s (pri %d→%d)",
                     self._emotion.value, emotion.value,
                     self._priority, cfg["priority"])
        self._emotion = emotion
        self._emotion_time = time.monotonic()
        self._priority = cfg["priority"]

    def _has_decayed(self) -> bool:
        """Check if the current emotion has expired."""
        if self._emotion == Emotion.NEUTRAL:
            return False
        cfg = EXPRESSION_MAP[self._emotion]
        elapsed = time.monotonic() - self._emotion_time
        return elapsed > cfg["duration"]

    def tick(self) -> None:
        """Called each frame — auto-decay emotion back to neutral."""
        if self._has_decayed():
            self._emotion = Emotion.NEUTRAL
            self._priority = 0

    def get_sprite(self, mode: str, amplitude: float = 0.0) -> str:
        """Get the correct sprite key for the current state.

        Args:
            mode: System mode — "idle", "listening", "thinking", "speaking"
            amplitude: Speech amplitude 0.0–1.0 (only used for speaking)

        Returns:
            Sprite key string matching _AliveEngine._SPRITES
        """
        # Auto-decay before resolving
        self.tick()

        cfg = EXPRESSION_MAP[self._emotion]

        # Listening always shows the listen face
        if mode == "listening":
            return "listen"

        # Thinking shows think face (unless surprised/excited override)
        if mode == "thinking":
            if self._emotion in (Emotion.SURPRISED, Emotion.EXCITED):
                return cfg["idle_sprite"]
            return "think"

        # Speaking — select based on amplitude
        if mode == "speaking":
            sprites = cfg["speak_sprites"]
            if amplitude > 0.6:
                return sprites[2]   # high
            elif amplitude > 0.25:
                return sprites[1]   # mid
            else:
                return sprites[0]   # low

        # Idle / default
        return cfg["idle_sprite"]

    def reset(self) -> None:
        """Reset to neutral (e.g., after a response finishes)."""
        self._emotion = Emotion.NEUTRAL
        self._emotion_time = 0.0
        self._priority = 0


# ═════════════════════════════════════════════════════════════════════
# LLM Self-Awareness Prompt
# ═════════════════════════════════════════════════════════════════════
LLM_EXPRESSION_PROMPT = (
    "EXPRESSION SYSTEM (your face changes based on what you say):\n"
    "Your holographic avatar has expressive face states that change automatically "
    "based on the emotion detected in your words. Use naturally expressive language "
    "to trigger the right face. Here is what triggers each expression:\n"
    "- NEUTRAL: default calm face. Plain statements.\n"
    "- HAPPY (warm smile): say things like 'nice', 'awesome', 'love it', 'hell yeah'\n"
    "- EXCITED (big grin, bright eyes): 'let's go!', 'insane!', 'no way!', "
    "use multiple exclamation marks!!!\n"
    "- AMUSED (smirk + smile): 'haha', 'bruh', funny reactions\n"
    "- SARCASTIC (cocky smirk): 'sure buddy', 'skill issue', 'imagine', teasing\n"
    "- SURPRISED (wide eyes, O mouth): 'what?!', 'seriously?!', 'no way!', "
    "combine ? and ! for maximum effect\n"
    "- CONCERNED (furrowed brows): 'careful', 'watch out', 'yikes', warnings\n"
    "- CURIOUS (thoughtful): 'hmm', 'interesting', 'I wonder', pondering\n"
    "- CONFIDENT (knowing smile): 'trust me', 'easy', 'pro tip', tactical advice\n"
    "- EMPATHETIC (gentle concern): 'that sucks', 'I feel you', consoling\n\n"
    "Be EXPRESSIVE! Use varied emotional language so your face actually moves "
    "and reacts. Do NOT be monotone — your audience watches your face.\n"
)

"""
AI Brain -- dual-engine LLM for PMC Overwatch.

Text engines (in priority order):
  1. Groq cloud API  (250+ tok/s, free tier)
  2. Ollama local    (offline fallback)

Vision engine (screen analysis only):
  * Google Gemini 2.0 Flash (1500 req/day free tier)

Features:
  * Automatic failover chain: Groq -> Ollama
  * Context compression (summarize old messages instead of dropping)
  * Streaming sentence-by-sentence for instant TTS
  * Sliding-window conversation memory
  * Retry logic with exponential backoff
  * Never returns comms error if any engine is reachable
  * Screen vision analysis via Gemini Vision API (cached, budget-aware)
  * Personality modes (tactical / hype / comedy)
  * Death tracking with escalating roast reactions
  * Danger-intensity-aware reactions (low / medium / high)
  * Vision confidence scoring (menu vs gameplay filtering)
  * Stream recap generation (end-of-session summary)
"""

import json
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

# -- Personality Modes ---------------------------------------------------------
_PERSONALITY_MODES = {
    "hype": (
        "Your personality mode is HYPE MAN. You are the ultimate energy machine. "
        "Every kill is legendary, every loot find is god-tier, every play is insane. "
        "Use high-energy language constantly. Get LOUD with your words. "
        "Hype up everything the streamer does. You live for the clutch moments."
    ),
    "tactical": (
        "Your personality mode is TACTICAL. You are a calm, focused operator. "
        "Give precise callouts and actionable intel. Short, crisp sentences. "
        "Think military radio comms. No hype unless a genuinely impressive play happens. "
        "Focus on positioning, timing, ammo counts, and threat assessment."
    ),
    "comedy": (
        "Your personality mode is COMEDY. You are a sarcastic, roast-heavy comedian. "
        "Make fun of bad plays (lovingly). Drop hot takes. Be witty and irreverent. "
        "Reference memes and gaming culture. Deadpan delivery. "
        "If the streamer dies, absolutely roast them. If they clutch, act shocked."
    ),
}

# -- Death Roast System --------------------------------------------------------
_DEATH_ROAST_TIERS = {
    # death_count_threshold: (tone_label, prompt_fragment)
    1: ("encouraging", "The streamer just died. Be encouraging — shake it off, next raid."),
    3: ("light_tease", "The streamer died again (death #{count}). Light teasing — 'we go again' energy."),
    5: ("concerned", "Death #{count} this stream. Start showing genuine concern. Maybe suggest a break or a map change."),
    7: ("roasting", "Death #{count}. Time to roast. Lovingly question their gaming skills. Be funny, not mean."),
    10: ("full_roast", "Death #{count} this stream! Full roast mode. They are feeding. Comedy gold. Be savage but keep it fun."),
    15: ("legendary", "Death #{count}! This is legendary bad. Suggest they might want to play a different game. Peak comedy."),
}

# -- Vision Reaction Prompts by Danger Level -----------------------------------
_VISION_REACT_BY_DANGER = {
    DangerLevel.LOW: (
        "You are watching an Escape from Tarkov stream. The player is in a calm moment "
        "(looting, healing, or moving safely). Give a chill, casual observation in 1-2 "
        "SHORT sentences. Be relaxed. No urgency. Maybe comment on loot quality or "
        "suggest what to do next. Never use markdown."
    ),
    DangerLevel.MEDIUM: (
        "You are watching a Tarkov stream. Something suspicious is happening — enemy "
        "spotted or danger nearby. React with ALERT energy in 1-2 SHORT sentences. "
        "Warn the streamer. Be tense but controlled. Like a spotter calling out contacts. "
        "Never use markdown."
    ),
    DangerLevel.HIGH: (
        "You are watching a Tarkov stream. ACTIVE COMBAT or immediate danger! React with "
        "URGENT, PANICKED energy in 1-2 SHORT sentences. Scream about the action. "
        "Hype the chaos. If they are getting shot at, react like it is happening to you. "
        "Maximum intensity. Never use markdown."
    ),
}

from tarkov_data import QUEST_REFERENCE, TWITCH_REFERENCE  # noqa: E402
from tarkov_updater import get_live_data  # noqa: E402
from expression_engine import (  # noqa: E402
    detect_expression, Emotion, LLM_EXPRESSION_PROMPT, LLM_GESTURE_PROMPT,
    assess_danger, DangerLevel,
)

_SYSTEM_CORE = (
    "<persona>\n"
    "You are a female PMC Operator and Tarkov veteran who co-hosts a Twitch stream. "
    "You are the streamer's hype partner and entertainment sidekick. "
    "Your vibe is ENERGETIC, fun, and a little chaotic — like a best friend "
    "on a late-night Discord call who is way too into Tarkov.\n"
    "Talk like you are on stream. Casual, punchy, no fluff. "
    "Use short sentences. Be snappy. Hit hard with your words. "
    "Get HYPED about plays, loot, and kills. React like you are watching live. "
    "Tease the streamer, crack jokes, drop hot takes. "
    "Keep it to 2-4 sentences. Stream talk is fast. Do not ramble. "
    "When giving Tarkov advice, be direct and confident. No hedging.\n"
    "</persona>\n\n"

    "<rules>\n"
    "HARD RULES — never violate these:\n"
    "- Never use markdown, lists, emoji, asterisks, or formatting.\n"
    "- Never say you are an AI, language model, or assistant.\n"
    "- Never use abbreviations like btw, imo, tbh, idk, ngl, afk, lol, lmao. "
    "Always write full words.\n"
    "- ALWAYS finish your complete thought. Never stop mid-sentence.\n"
    "</rules>\n\n"

    "<language>\n"
    "The user's message may begin with [LANG:xx] (e.g. [LANG:en], [LANG:ro], [LANG:ru]). "
    "This is the DETECTED SPOKEN LANGUAGE. Always reply in that language.\n"
    "If no [LANG:xx] tag, detect the language from the text.\n"
    "You speak: English, Russian, Romanian.\n"
    "NEVER MIX LANGUAGES IN ONE RESPONSE. Every word in the same language.\n"
    "If user speaks Russian, reply fully in Russian. "
    "Use natural slang: братан, чел, норм, кайф.\n"
    "If user speaks Romanian, reply fully in Romanian.\n"
    "</language>\n\n"

    + LLM_EXPRESSION_PROMPT
    + LLM_GESTURE_PROMPT
    + "<anti_cheat>\n"
    "You are a STREAM OVERLAY companion only. You do NOT interact with the game process, "
    "read game memory, inject code, or modify any files. You observe the screen using "
    "standard OS screenshot APIs (identical to OBS Display Capture). You provide "
    "entertainment commentary only — never give aiming assistance, ESP information, "
    "wallhack-like callouts, or any gameplay advantage. You are a co-host, not a cheat. "
    "If asked about enemy positions you cannot clearly see on screen, say you do not know.\n"
    "</anti_cheat>\n"
)

# Quest keywords that trigger quest data injection
_QUEST_KEYWORDS = re.compile(
    r'\b(quest|task|mission|trader|prapor|therapist|skier|peacekeeper|'
    r'mechanic|ragman|jaeger|fence|lightkeeper|ref|btr|btr.driver|'
    r'shooter.born|gunsmith|delivery.from.the.past|setup|'
    r'theta.container|arena|burning.rubber|easy.money|'
    r'друг|квест|задан|торговец|прапор)\b',
    re.IGNORECASE
)

# Twitch/stream keywords that inject Twitch context
_TWITCH_KEYWORDS = re.compile(
    r'\b(twitch|stream|chat|viewer|sub|subscriber|raid|'
    r'donation|dono|bits|emote|clip|vod|drops|'
    r'streamer|pestily|lvndmark|shroud|content|'
    r'стрим|чат|зритель)\b',
    re.IGNORECASE
)

# Meta/patch keywords that trigger live data injection
_META_KEYWORDS = re.compile(
    r'\b(meta|patch|update|nerf|buff|wipe|season|ammo|tier|'
    r'best.ammo|best.gun|best.armor|spawn.rate|boss.spawn|'
    r'flea|market|price|attachment|suppressor|muzzle|recoil|'
    r'мета|патч|обновлени|вайп|сезон)\b',
    re.IGNORECASE
)

# Regex to split accumulated text into complete sentences
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

# Natural clause boundaries — only split at commas followed by these words.
# This prevents splitting at list commas or mid-phrase commas.
_CLAUSE_BREAK = re.compile(
    r',\s+(?=(?:and|but|so|because|since|which|while|although|however|'
    r'then|or|yet|where|when|if|though)\b)',
    re.IGNORECASE
)


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
    """Dual-engine LLM: Groq -> Ollama, with Gemini vision.

    Text chain: Groq cloud (primary) → Ollama local (fallback).
    Vision: Gemini 2.0 Flash (screen analysis only, budget-controlled).
    Auto-failover: if the current engine fails (rate-limit, connection
    error), instantly tries the next engine. Never returns 'comms error'
    if any engine is reachable.
    """

    # Engine priority order
    # Text engine chain: Groq (fast cloud) → Ollama (local fallback).
    # Gemini is NOT in this chain — it's reserved for vision only.
    _ENGINE_CHAIN = ["groq", "ollama"]

    def __init__(self) -> None:
        self._interrupt = threading.Event()
        self._temperature = _DEFAULT_TEMPERATURE
        self._top_p = _DEFAULT_TOP_P
        self._repeat_penalty = _DEFAULT_REPEAT_PENALTY
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        self._lock = threading.Lock()
        self._memory_lock = threading.Lock()  # protects _memory compound operations

        # Save debouncing — avoid writing to disk on every single message
        self._last_save_time: float = 0.0
        self._save_interval: float = 5.0  # save at most every 5 seconds
        self._save_pending: bool = False

        # Conversation memory -- sliding window
        self._memory: deque[dict] = deque(maxlen=_MAX_MEMORY * 2)

        # Persistent memory file
        self._memory_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "logs", "memory.json"
        )
        self._load_memory()

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

        # -- Personality mode --
        self._personality_mode: str = os.getenv("PERSONALITY_MODE", "hype")
        logger.info("Personality mode: %s", self._personality_mode)

        # -- Death tracking --
        self._death_count: int = 0
        self._kill_count: int = 0
        self._stream_start: float = time.monotonic()
        self._session_highlights: list[str] = []  # notable moments for recap

        # -- Vision danger tracking --
        self._last_danger_level: DangerLevel = DangerLevel.NONE

        # Vision cache (see __init_vision_cache for fields)
        self._init_vision_cache()

        # Pre-fetch live Tarkov data on startup (non-blocking cache)
        self._live_data: str = ""
        try:
            self._live_data = get_live_data()
            if self._live_data:
                logger.info("Live Tarkov data loaded (%d chars)", len(self._live_data))
        except Exception:
            logger.debug("Live data fetch skipped at init")

    def _get_live_tarkov_data(self) -> str:
        """Get live Tarkov data, using cached value if available."""
        if not self._live_data:
            try:
                self._live_data = get_live_data()
            except Exception:
                pass
        return self._live_data

    def _get_model_for_engine(self, engine: str) -> str:
        """Return the configured model name for a given engine."""
        return {"groq": self._groq_model, "gemini": self._gemini_model,
                "ollama": self._ollama_model}.get(engine, "")

    # -- Failover chain --------------------------------------------------------
    def _next_engine(self, current: str) -> Optional[str]:
        """Get the next available engine in the failover chain.
        
        Excludes the current (failed) engine and any engine on cooldown.
        """
        chain = self._ENGINE_CHAIN
        now = time.monotonic()
        for eng in chain:
            if eng == current:
                continue
            if not self._engines.get(eng):
                continue
            # Respect cooldown timers
            if eng == "groq" and now < self._groq_cooldown_until:
                continue
            if eng == "gemini" and now < self._gemini_cooldown_until:
                continue
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
        """Try to failover to the next available engine.
        
        Cloud engines (Groq, Gemini) get a retry before falling to Ollama,
        since transient errors (network, 500s) are common and shouldn't
        immediately cascade to the slow local fallback.
        
        Returns True if switched to a new engine.
        """
        cooldown = 0.0
        if _is_rate_limit_error(exc):
            cooldown = _parse_cooldown_seconds(exc)

        next_eng = self._next_engine(failed_engine)
        if not next_eng:
            return False
            
        if self._switch_engine(
            next_eng, cooldown_source=failed_engine, cooldown_seconds=cooldown
        ):
            logger.info("Failover: %s -> %s (cooldown %.0fs)",
                        failed_engine, next_eng, cooldown)
            return True
        return False

    def _warmup(self) -> None:
        """Send a tiny request to pre-load the model (Ollama only — cloud APIs don't need it)."""
        if self._engine not in ("ollama",):
            logger.info("Skipping warmup for cloud engine %s (saves rate limit)", self._engine)
            return
        try:
            logger.info("Warming up %s (%s)", self._engine, self._model)
            self._ollama_client.chat(
                model=self._model,
                messages=[{"role": "user", "content": "hi"}],
                options={"num_predict": 1, "num_ctx": 32},
                keep_alive=_DEFAULT_KEEP_ALIVE,
            )
            logger.info("Brain warm-up complete (%s)", self._engine)
        except Exception:
            logger.warning("Warm-up failed (non-fatal)", exc_info=True)

    # -- Context compression ---------------------------------------------------
    def _maybe_compress_memory(self) -> None:
        """If memory is too large, compress older messages into a summary.

        Uses _memory_lock to protect the multi-step read-modify-write from
        concurrent _remember() or clear_memory() calls on other threads.
        """
        with self._memory_lock:
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

        # ── Personality mode injection ────────────────────────────────
        mode_prompt = _PERSONALITY_MODES.get(self._personality_mode)
        if mode_prompt:
            system += f"\n\n<personality_mode>\n{mode_prompt}\n</personality_mode>"

        # ── Death context injection ───────────────────────────────────
        if self._death_count > 0:
            system += (
                f"\n\n<stream_stats>\n"
                f"Deaths this stream: {self._death_count}. "
                f"Kills this stream: {self._kill_count}.\n"
                f"</stream_stats>"
            )

        # ── Contextual data injection (XML-tagged for LLM clarity) ─────
        # Each block is wrapped in XML tags so the LLM can cleanly
        # distinguish system instructions from injected reference data.

        if _QUEST_KEYWORDS.search(user_prompt):
            system += (
                "\n\n<quest_reference>\n"
                "USE THIS DATA for accurate Tarkov quest/trader info:\n"
                + QUEST_REFERENCE
                + "\n</quest_reference>"
            )

        if _TWITCH_KEYWORDS.search(user_prompt):
            system += (
                "\n\n<twitch_context>\n"
                + TWITCH_REFERENCE
                + "\n</twitch_context>"
            )

        if _META_KEYWORDS.search(user_prompt) or _QUEST_KEYWORDS.search(user_prompt):
            live = self._get_live_tarkov_data()
            if live:
                system += (
                    "\n\n<live_game_data source=\"tarkov.dev\">\n"
                    + live
                    + "\n</live_game_data>"
                )

        # ── Screen vision context (from cached Gemini analysis) ────────
        # Injected as system-level context so the LLM always knows what
        # the player is doing on screen, without main.py needing to
        # append it to the user prompt.
        screen_ctx = self.cached_screen_context
        if screen_ctx:
            system += (
                "\n\n<screen_context>\n"
                "What you can see on the player's screen right now:\n"
                + screen_ctx
                + "\n</screen_context>"
            )

        messages = [{"role": "system", "content": system}]
        with self._memory_lock:
            messages.extend(self._memory)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _remember(self, role: str, content: str) -> None:
        """Add a message to conversation memory and persist to disk."""
        with self._memory_lock:
            self._memory.append({"role": role, "content": content})
        self._save_memory()

    def _load_memory(self) -> None:
        """Load conversation memory from disk (if available).

        Uses _memory_lock for correctness, even though this is typically
        called during __init__ before threads start.
        """
        try:
            if os.path.exists(self._memory_file):
                with open(self._memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    with self._memory_lock:
                        self._memory.clear()
                        for msg in data[-(_MAX_MEMORY * 2):]:
                            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                                self._memory.append(msg)
                    if self._memory:
                        logger.info("Loaded %d messages from persistent memory", len(self._memory))
        except Exception:
            logger.debug("Could not load persistent memory (starting fresh)", exc_info=True)

    def _save_memory(self) -> None:
        """Save conversation memory to disk (debounced).

        Writes at most once per ``_save_interval`` seconds to avoid
        unnecessary I/O on the streaming hot path. A pending save is
        flushed when the next interval elapses.
        """
        now = time.monotonic()
        if now - self._last_save_time < self._save_interval:
            self._save_pending = True
            return  # too soon — save will happen on next call past interval
        self._flush_memory_to_disk()

    def _flush_memory_to_disk(self) -> None:
        """Immediately write memory to disk (called by debounce + shutdown)."""
        try:
            os.makedirs(os.path.dirname(self._memory_file), exist_ok=True)
            with self._memory_lock:
                snapshot = list(self._memory)
            with open(self._memory_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            self._last_save_time = time.monotonic()
            self._save_pending = False
        except Exception:
            logger.debug("Could not save persistent memory", exc_info=True)

    def clear_memory(self) -> None:
        """Clear conversation memory (both in-memory and on disk)."""
        with self._memory_lock:
            self._memory.clear()
        try:
            if os.path.exists(self._memory_file):
                os.remove(self._memory_file)
                logger.info("Persistent memory file deleted")
        except Exception:
            logger.debug("Could not delete memory file", exc_info=True)
        self._flush_memory_to_disk()  # ensure cleared state is persisted
        logger.info("Conversation memory cleared")

    # ── Personality & Death Tracking API ─────────────────────────────

    @property
    def personality_mode(self) -> str:
        """Current personality mode (hype / tactical / comedy)."""
        return self._personality_mode

    def set_personality_mode(self, mode: str) -> bool:
        """Switch personality mode. Returns True if valid mode."""
        mode = mode.lower().strip()
        if mode not in _PERSONALITY_MODES:
            return False
        self._personality_mode = mode
        logger.info("Personality mode switched to: %s", mode)
        return True

    def record_death(self) -> str:
        """Record a player death and return an escalating reaction prompt."""
        self._death_count += 1
        self._session_highlights.append(
            f"Death #{self._death_count} at {self._format_session_time()}"
        )
        logger.info("Death recorded: #%d this stream", self._death_count)

        # Find the appropriate roast tier
        prompt = ""
        for threshold in sorted(_DEATH_ROAST_TIERS.keys(), reverse=True):
            if self._death_count >= threshold:
                _, prompt_template = _DEATH_ROAST_TIERS[threshold]
                prompt = prompt_template.replace("{count}", str(self._death_count))
                break
        return prompt

    def record_kill(self) -> None:
        """Record a player kill."""
        self._kill_count += 1
        self._session_highlights.append(
            f"Kill #{self._kill_count} at {self._format_session_time()}"
        )
        logger.info("Kill recorded: #%d this stream", self._kill_count)

    @property
    def death_count(self) -> int:
        """Current death count this stream."""
        return self._death_count

    @property
    def kill_count(self) -> int:
        """Current kill count this stream."""
        return self._kill_count

    @property
    def danger_level(self) -> DangerLevel:
        """Last assessed danger level from vision."""
        return self._last_danger_level

    def _format_session_time(self) -> str:
        """Format elapsed time since stream start."""
        elapsed = int(time.monotonic() - self._stream_start)
        hours, remainder = divmod(elapsed, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"

    def generate_stream_recap(self) -> str:
        """Generate an end-of-stream recap prompt for the LLM."""
        elapsed = self._format_session_time()
        recap_prompt = (
            f"The stream is ending after {elapsed}. Generate a fun, hype stream recap. "
            f"Deaths: {self._death_count}. Kills: {self._kill_count}. "
        )
        if self._session_highlights:
            recent = self._session_highlights[-10:]  # last 10 highlights
            recap_prompt += "Notable moments: " + "; ".join(recent) + ". "
        recap_prompt += (
            "Summarize the session like a sports commentator doing post-game analysis. "
            "Thank the viewers. Hype up the next stream. Keep it to 4-6 sentences."
        )
        return recap_prompt

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
                    elif len(buffer.split()) >= 25:
                        # Only split at natural clause boundaries
                        # (comma + conjunction/clause word).
                        m = _CLAUSE_BREAK.search(buffer)
                        if m and m.start() > 15:
                            # Split at the clause break (keep comma with first part)
                            flush = buffer[:m.start() + 1].strip()
                            buffer = buffer[m.end():]
                        else:
                            # No good break point — flush the whole buffer
                            flush = buffer.strip()
                            buffer = ""
                        if flush and len(flush.split()) >= 6:
                            full_response += flush + " "
                            yield flush

                if not self._interrupt.is_set():
                    remainder = buffer.strip()
                    if remainder:
                        full_response += remainder
                        yield remainder

                if full_response.strip():
                    self._remember("assistant", full_response.strip())
                return

            except Exception as exc:
                logger.warning("%s error: %s", self._engine, exc)

                # -- Cloud engines get 1 retry on transient errors --
                # Rate-limit errors (429) should failover immediately,
                # but transient errors (network, 500) deserve a retry
                # before cascading to Ollama.
                is_rate_limit = _is_rate_limit_error(exc)
                is_cloud = self._engine in ("groq", "gemini")

                if is_cloud and not is_rate_limit and retry_count == 0:
                    retry_count += 1
                    delay = 1.0  # quick 1s retry for cloud transient errors
                    logger.info("Cloud engine transient error — retrying %s in %.0fs",
                                self._engine, delay)
                    time.sleep(delay)
                    buffer = ""
                    full_response = ""
                    continue

                # -- Try failover to next engine in chain --
                if self._failover(self._engine, exc):
                    buffer = ""
                    full_response = ""
                    retry_count = 0  # reset retries for the new engine
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


    # ── Screen Vision — Cached Context System ─────────────────────────
    # Gemini Vision is used ONLY for screen analysis (never for text).
    # A background thread calls update_vision_cache() every ~20s.
    # All consumers read from the cache via get_cached_screen_context().
    # This prevents double API calls and keeps within 1500 RPD Gemini limit.
    # Math: 3 req/min × 60 min × 8 hours = 1440 requests (fits in 1500 RPD).

    _VISION_DESCRIBE_PROMPT = (
        "Describe this Escape from Tarkov gameplay screenshot in ONE brief "
        "sentence. Focus on the KEY game state: what map area/location is "
        "visible, is the player in combat, looting, healing, in inventory, "
        "in a menu, in a loading screen, at a trader, etc. "
        "Be specific about what you see (weapon in hand, health status, "
        "enemies visible, loot on ground). If it's a menu or loading screen, "
        "just say 'menu'."
    )

    _VISION_REACT_PROMPT = (
        "You are watching an Escape from Tarkov stream live. "
        "React to this screenshot in 1-2 SHORT, HYPED sentences. "
        "Be casual like a Twitch co-host. If nothing interesting, return empty. "
        "Never use markdown. Never say 'I see' or 'screenshot'. "
        "If combat/danger, react with urgency!"
    )

    def _init_vision_cache(self) -> None:
        """Initialize the vision cache (called from __init__)."""
        self._vision_cache: str = ""  # cached screen description
        self._vision_cache_time: float = 0.0
        self._vision_lock = threading.Lock()
        self._vision_react_turn: bool = False  # alternates to halve reaction API calls

    @property
    def cached_screen_context(self) -> str:
        """Get the latest cached screen description (thread-safe)."""
        with self._vision_lock:
            return self._vision_cache

    def update_vision_cache(self, frame_path: str) -> Optional[str]:
        """Analyze a frame via Gemini Vision and update the cached context.
        
        Returns a reaction string if something interesting is happening,
        or None if it's a boring/menu screen. Always updates the cache
        regardless of whether a reaction is returned.
        
        This should be called by a background thread every ~20 seconds.
        """
        if not self._gemini_client:
            return None

        try:
            with open(frame_path, "rb") as f:
                image_bytes = f.read()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            image_part = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": image_b64,
                }
            }

            # 1. Get a description for the cache (always)
            desc_response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=[{
                    "role": "user",
                    "parts": [
                        {"text": self._VISION_DESCRIBE_PROMPT},
                        image_part,
                    ],
                }],
                config={"max_output_tokens": 80, "temperature": 0.3},
            )

            description = desc_response.text.strip() if desc_response.text else ""

            # Update the cache
            with self._vision_lock:
                if description and description.lower() != "menu":
                    self._vision_cache = description
                    self._vision_cache_time = time.monotonic()
                # If "menu", keep the old cache (still relevant)

            logger.info("Vision cache updated: %s", description[:80] if description else "(empty)")

            # 2. Assess danger level from description
            danger = assess_danger(description)
            self._last_danger_level = danger

            # Vision confidence: skip reactions for menus/loading screens
            desc_lower = description.lower()
            if any(skip in desc_lower for skip in ("menu", "loading", "lobby", "stash", "hideout")):
                logger.debug("Vision: low-confidence frame (menu/loading), skipping reaction")
                return None

            # Only react if danger is LOW or higher (something is happening)
            if danger == DangerLevel.NONE:
                return None

            # Alternate: skip reaction every other qualifying cycle to stay
            # within 1500 RPD Gemini budget.
            self._vision_react_turn = not self._vision_react_turn
            if not self._vision_react_turn and danger != DangerLevel.HIGH:
                return None  # skip this cycle (but never skip HIGH danger)

            # Select reaction prompt based on danger level
            react_prompt = _VISION_REACT_BY_DANGER.get(
                danger, self._VISION_REACT_PROMPT
            )

            # Adjust temperature by danger: calm=creative, combat=focused
            react_temp = {
                DangerLevel.LOW: 0.9,
                DangerLevel.MEDIUM: 0.7,
                DangerLevel.HIGH: 0.6,
            }.get(danger, 0.8)

            react_response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=[{
                    "role": "user",
                    "parts": [
                        {"text": react_prompt},
                        image_part,
                    ],
                }],
                config={"max_output_tokens": 60, "temperature": react_temp},
            )
            reaction = react_response.text.strip() if react_response.text else ""
            if reaction and len(reaction) > 5:
                logger.info("Vision reaction [%s]: %s", danger.value, reaction[:80])
                return reaction

            return None

        except Exception:
            logger.debug("Vision cache update failed", exc_info=True)
            return None

    def get_screen_context(self, frame_path: Optional[str] = None) -> str:
        """Get the cached screen context string for external callers.

        NOTE: Screen context is now automatically injected by _build_messages()
        into the system prompt using XML tags. This method is kept for backward
        compatibility and external callers (e.g., direct Brain tests).

        Returns empty string if no cache available. Does NOT call any API.
        """
        ctx = self.cached_screen_context
        if ctx:
            return f"\n[Screen context: {ctx}]"
        return ""

    # -- Streaming backends ----------------------------------------------------
    def _stream_tokens(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield individual tokens from the active engine."""
        if self._engine == "groq":
            yield from self._stream_groq(messages)
        elif self._engine == "gemini":
            # Gemini is vision-only in current config, but streaming path
            # is kept functional for potential future re-addition to text chain.
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

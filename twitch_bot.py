"""
Twitch Bot — chat integration for PMC Overwatch.

Connects to Twitch IRC via TwitchIO and forwards relevant messages
to the main system for AI response generation.

Commands:
  !hello         — greeting
  !move <dir>    — move mascot (left, right, center, random)
  !dance         — mascot dances
  !wave          — mascot waves
  !clap          — mascot claps
  !think         — mascot does thinking pose
  !shrug         — mascot shrugs
  !salute        — mascot salutes
  !crouch        — mascot crouches
  !die           — mascot dies (dramatic)
  !win           — mascot victory pose
  !ask <text>    — ask the AI a question directly
  !status        — show AI engine status + uptime
  !personality   — switch personality mode (hype/tactical/comedy)
  !deaths        — show death/kill count this stream
  !celebrate     — trigger celebration macro (dance + clap + wave)
  !macro <list>  — play animation sequence (e.g., !macro dance,clap,wave)
"""

import asyncio
import logging
import os
import time
from typing import Callable, Dict, Optional

from twitchio.ext import commands

logger = logging.getLogger(__name__)

# Per-user command cooldown (seconds)
_COMMAND_COOLDOWN = 10.0


class TwitchBot(commands.Bot):
    """TwitchIO-based chat bot for handling viewer interactions."""

    def __init__(self) -> None:
        token = os.getenv("TWITCH_TOKEN", "")
        raw_channels = os.getenv("TWITCH_INITIAL_CHANNELS", "")
        channels = [c.strip() for c in raw_channels.split(",") if c.strip()]

        if not token:
            raise ValueError(
                "TWITCH_TOKEN is not set. "
                "Add it to your .env file (see .env.example)."
            )

        if not channels:
            raise ValueError(
                "TWITCH_INITIAL_CHANNELS is not set. "
                "Add at least one channel to your .env file."
            )

        super().__init__(token=token, prefix="!", initial_channels=channels)
        self._message_callback: Optional[Callable] = None
        self._system_ref: Optional[object] = None
        self._mascot_ref: Optional[object] = None  # MascotServer reference
        self._cooldowns: Dict[str, float] = {}  # "user:cmd" → last_time

    # ── Configuration ─────────────────────────────────────────────────
    def set_system_reference(self, system: object) -> None:
        """Store a reference to the main PMCOverwatch."""
        self._system_ref = system

    def set_mascot_reference(self, mascot: object) -> None:
        """Store a reference to the MascotServer for animation control."""
        self._mascot_ref = mascot

    def set_callback(self, callback: Callable) -> None:
        """Set the callback invoked for relevant chat messages."""
        self._message_callback = callback

    # ── Cooldown check ────────────────────────────────────────────────
    def _check_cooldown(self, user: str, cmd: str) -> bool:
        """Returns True if the command is allowed (not on cooldown)."""
        key = f"{user}:{cmd}"
        now = time.monotonic()
        last = self._cooldowns.get(key, 0.0)
        if now - last < _COMMAND_COOLDOWN:
            return False
        self._cooldowns[key] = now
        return True

    # ── Events ────────────────────────────────────────────────────────
    async def event_ready(self) -> None:
        logger.info("Twitch bot logged in as %s", self.nick)
        logger.info("Connected channels: %s", self.connected_channels)

    async def event_message(self, message) -> None:
        if message.echo:
            return

        # Forward to mascot for chat bubble display (always)
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_chat_event'):
            self._mascot_ref.send_chat_event(message.author.name, message.content)

        # Only forward to AI if it contains Tarkov keywords or bot mention,
        # AND is not a standard command (commands handle their own forwarding)
        content_lower = message.content.lower()
        if not content_lower.startswith("!"):
            tarkov_keywords = [
                "tarkov", "quest", "raid", "ammo", "gun", "boss", "flea", "wipe",
                "PMC", "scav", "loot", "extract", "prapor", "therapist", "skier",
                "peacekeeper", "mechanic", "ragman", "jaeger", "fence"
            ]
            
            is_relevant = any(k in content_lower for k in tarkov_keywords)
            # Also reply if someone asks a question out loud with "ai" or "bot"
            if " ai " in content_lower or " bot " in content_lower or "@" in content_lower:
                is_relevant = True

            if is_relevant:
                logger.info("Forwarding relevant Twitch chat from %s", message.author.name)
                if self._message_callback:
                    if asyncio.iscoroutinefunction(self._message_callback):
                        await self._message_callback(message.author.name, message.content)
                    else:
                        self._message_callback(message.author.name, message.content)
            else:
                logger.debug("Filtered irrelevant Twitch chat from %s", message.author.name)

        await self.handle_commands(message)

    # ── Commands ──────────────────────────────────────────────────────
    @commands.command()
    async def hello(self, ctx: commands.Context) -> None:
        await ctx.send(f"Hello {ctx.author.name}! 🎯")

    @commands.command()
    async def move(self, ctx: commands.Context) -> None:
        """Move the mascot: !move left/right/center/random"""
        if not self._check_cooldown(ctx.author.name, "move"):
            return
        parts = ctx.message.content.split(maxsplit=1)
        direction = parts[1].strip().lower() if len(parts) > 1 else "random"
        if direction not in ("left", "right", "center", "random"):
            await ctx.send(f"@{ctx.author.name} Use: !move left/right/center/random")
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_navigate'):
            self._mascot_ref.send_navigate(direction)
            logger.info("Twitch !move %s by %s", direction, ctx.author.name)

    @commands.command()
    async def dance(self, ctx: commands.Context) -> None:
        """Trigger dance animation."""
        if not self._check_cooldown(ctx.author.name, "dance"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("dance")
            logger.info("Twitch !dance by %s", ctx.author.name)

    @commands.command()
    async def wave(self, ctx: commands.Context) -> None:
        """Trigger wave animation."""
        if not self._check_cooldown(ctx.author.name, "wave"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("wave")

    @commands.command()
    async def clap(self, ctx: commands.Context) -> None:
        """Trigger clap animation."""
        if not self._check_cooldown(ctx.author.name, "clap"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("clap")

    @commands.command()
    async def think(self, ctx: commands.Context) -> None:
        """Trigger think animation."""
        if not self._check_cooldown(ctx.author.name, "think"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("think")

    @commands.command()
    async def shrug(self, ctx: commands.Context) -> None:
        """Trigger shrug animation."""
        if not self._check_cooldown(ctx.author.name, "shrug"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("shrug")

    @commands.command()
    async def salute(self, ctx: commands.Context) -> None:
        """Trigger salute animation."""
        if not self._check_cooldown(ctx.author.name, "salute"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("salute")

    @commands.command()
    async def crouch(self, ctx: commands.Context) -> None:
        """Trigger crouch animation."""
        if not self._check_cooldown(ctx.author.name, "crouch"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("crouch")

    @commands.command()
    async def die(self, ctx: commands.Context) -> None:
        """Trigger die animation."""
        if not self._check_cooldown(ctx.author.name, "die"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("die")

    @commands.command()
    async def win(self, ctx: commands.Context) -> None:
        """Trigger win/victory animation."""
        if not self._check_cooldown(ctx.author.name, "win"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_animation'):
            self._mascot_ref.send_animation("win")

    @commands.command()
    async def ask(self, ctx: commands.Context) -> None:
        """Ask the AI a question directly: !ask <your question>"""
        if not self._check_cooldown(ctx.author.name, "ask"):
            return
        parts = ctx.message.content.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await ctx.send(f"@{ctx.author.name} Usage: !ask <your question>")
            return
        question = parts[1].strip()
        if self._message_callback:
            if asyncio.iscoroutinefunction(self._message_callback):
                await self._message_callback(ctx.author.name, question)
            else:
                self._message_callback(ctx.author.name, question)
            logger.info("Twitch !ask by %s: %s", ctx.author.name, question[:60])

    @commands.command()
    async def personality(self, ctx: commands.Context) -> None:
        """Switch personality mode: !personality hype/tactical/comedy"""
        if not self._check_cooldown(ctx.author.name, "personality"):
            return
        parts = ctx.message.content.split(maxsplit=1)
        mode = parts[1].strip().lower() if len(parts) > 1 else ""
        if mode not in ("hype", "tactical", "comedy"):
            await ctx.send(f"@{ctx.author.name} Use: !personality hype/tactical/comedy")
            return
        if self._system_ref and hasattr(self._system_ref, '_brain'):
            brain = self._system_ref._brain
            if brain and brain.set_personality_mode(mode):
                if self._mascot_ref and hasattr(self._mascot_ref, 'send_personality'):
                    self._mascot_ref.send_personality(mode)
                await ctx.send(f"Personality switched to {mode.upper()} mode!")
                logger.info("Twitch !personality %s by %s", mode, ctx.author.name)

    @commands.command()
    async def deaths(self, ctx: commands.Context) -> None:
        """Show stream death/kill stats."""
        if not self._check_cooldown(ctx.author.name, "deaths"):
            return
        if self._system_ref and hasattr(self._system_ref, '_brain'):
            brain = self._system_ref._brain
            if brain:
                d, k = brain.death_count, brain.kill_count
                kd = f"{k/d:.1f}" if d > 0 else "perfect"
                await ctx.send(f"Stream stats: {k} kills / {d} deaths (K/D: {kd})")
                return
        await ctx.send("No stats tracked yet!")

    @commands.command()
    async def celebrate(self, ctx: commands.Context) -> None:
        """Trigger a celebration animation macro."""
        if not self._check_cooldown(ctx.author.name, "celebrate"):
            return
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_macro'):
            self._mascot_ref.send_macro(["dance", "clap", "wave"])
            logger.info("Twitch !celebrate by %s", ctx.author.name)

    @commands.command()
    async def macro(self, ctx: commands.Context) -> None:
        """Play an animation sequence: !macro dance,clap,wave"""
        if not self._check_cooldown(ctx.author.name, "macro"):
            return
        parts = ctx.message.content.split(maxsplit=1)
        if len(parts) < 2:
            await ctx.send(f"@{ctx.author.name} Use: !macro dance,clap,wave")
            return
        valid = {"wave", "think", "shrug", "clap", "dance", "salute", "win", "crouch", "die"}
        anims = [a.strip() for a in parts[1].split(",") if a.strip() in valid]
        if not anims:
            await ctx.send(f"@{ctx.author.name} No valid animations. Options: {', '.join(sorted(valid))}")
            return
        if len(anims) > 5:
            anims = anims[:5]  # cap at 5 to prevent spam
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_macro'):
            self._mascot_ref.send_macro(anims)
            logger.info("Twitch !macro %s by %s", anims, ctx.author.name)

    @commands.command()
    async def status(self, ctx: commands.Context) -> None:
        """Show AI companion status."""
        if not self._check_cooldown(ctx.author.name, "status"):
            return
        if self._system_ref and hasattr(self._system_ref, '_get_status'):
            try:
                info = self._system_ref._get_status()
                uptime = info.get("uptime_human", "?")
                engine = info.get("engine", "unknown")
                mode = info.get("mode", "idle")
                await ctx.send(
                    f"🤖 AI: {mode} | Engine: {engine} | Uptime: {uptime}"
                )
            except Exception:
                await ctx.send("🤖 AI companion is online!")
        else:
            await ctx.send("🤖 AI companion is online!")


if __name__ == "__main__":
    from dotenv import load_dotenv
    from logging_config import setup_logging

    setup_logging()
    load_dotenv()
    bot = TwitchBot()
    bot.run()


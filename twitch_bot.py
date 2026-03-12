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
  !status        — show AI engine status + uptime
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

        logger.debug(
            "[%s] %s: %s",
            message.channel.name,
            message.author.name,
            message.content,
        )

        # Forward to AI for potential response
        if self._message_callback:
            if asyncio.iscoroutinefunction(self._message_callback):
                await self._message_callback(message.author.name, message.content)
            else:
                self._message_callback(message.author.name, message.content)

        # Forward to mascot for chat bubble display
        if self._mascot_ref and hasattr(self._mascot_ref, 'send_chat_event'):
            self._mascot_ref.send_chat_event(message.author.name, message.content)

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


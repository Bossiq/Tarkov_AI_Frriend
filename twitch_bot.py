"""
Twitch Bot — chat integration for PMC Overwatch.

Connects to Twitch IRC via TwitchIO and forwards relevant messages
to the main system for AI response generation.
"""

import asyncio
import logging
import os
from typing import Callable, Optional

from twitchio.ext import commands

logger = logging.getLogger(__name__)


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

    # ── Configuration ─────────────────────────────────────────────────
    def set_system_reference(self, system: object) -> None:
        """Store a reference to the main SCAVESystem."""
        self._system_ref = system

    def set_callback(self, callback: Callable) -> None:
        """Set the callback invoked for relevant chat messages."""
        self._message_callback = callback

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

        if self._message_callback:
            if asyncio.iscoroutinefunction(self._message_callback):
                await self._message_callback(message.author.name, message.content)
            else:
                self._message_callback(message.author.name, message.content)

        await self.handle_commands(message)

    # ── Commands ──────────────────────────────────────────────────────
    @commands.command()
    async def hello(self, ctx: commands.Context) -> None:
        await ctx.send(f"Hello {ctx.author.name}!")


if __name__ == "__main__":
    from dotenv import load_dotenv
    from logging_config import setup_logging

    setup_logging()
    load_dotenv()
    bot = TwitchBot()
    bot.run()

import os
from twitchio.ext import commands

class TwitchBot(commands.Bot):
    def __init__(self, token_env="TWITCH_TOKEN", prefix="!"):
        token = os.getenv(token_env)
        channels = os.getenv("TWITCH_INITIAL_CHANNELS", "").split(',')
        # Remove empty strings
        channels = [c.strip() for c in channels if c.strip()]
        
        if not token:
            print(f"WARNING: {token_env} not found in environment variables.")
            token = "dummy_token"
            
        if not channels:
            print("WARNING: TWITCH_INITIAL_CHANNELS not found in environment variables.")
            channels = ["default_channel"]

        super().__init__(token=token, prefix=prefix, initial_channels=channels)
        self.message_callback = None

    def set_callback(self, callback):
        """Sets the callback function to handle incoming chat messages."""
        self.message_callback = callback

    async def event_ready(self):
        print(f'Twitch Bot logged in as | {self.nick}')
        print(f'Connected channels: {self.connected_channels}')

    async def event_message(self, message):
        if message.echo:
            return

        print(f"[Twitch {message.channel.name}] {message.author.name}: {message.content}")

        if self.message_callback:
            # Pass message to the main system
            # We must await the callback if it's async, or use thread if sync
            # To keep it simple, we assume the callback handles the orchestration
            self.message_callback(message.author.name, message.content)

        await self.handle_commands(message)

    @commands.command()
    async def hello(self, ctx: commands.Context):
        await ctx.send(f'Hello {ctx.author.name}!')

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    bot = TwitchBot()
    bot.run()

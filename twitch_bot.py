from twitchio.ext import commands
import os

class TwitchBot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=os.getenv('TWITCH_TOKEN'),
            prefix=os.getenv('TWITCH_PREFIX'),
            initial_channels=[os.getenv('TWITCH_INITIAL_CHANNELS')]
        )

    async def event_ready(self):
        print(f'Logged into Twitch as | {self.nick}')

    async def event_message(self, message):
        if message.echo:
            return
        # print(f"{message.author.name}: {message.content}")
        await self.handle_commands(message)

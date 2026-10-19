import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from db import init_db

load_dotenv()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=(), intents=intents, help_command=None)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Ready — {bot.user} ({bot.user.id})")


async def main():
    await init_db()
    async with bot:
        from commands import BotCommands
        await bot.add_cog(BotCommands(bot))
        await bot.start(os.getenv("DISCORD_TOKEN"))


asyncio.run(main())

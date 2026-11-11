import discord
from discord.ext import commands
import logging
import os

from astra.config import Settings

settings = Settings.from_env()

# Define intents and bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

logging.basicConfig(level=logging.INFO)

bot = commands.Bot(command_prefix="!", intents=intents)
bot.settings = settings  # cogs read config from here

@bot.event
async def on_ready():
    logging.info(f"{bot.user} is online.")

    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            cog_name = filename[:-3]
            try:
                await bot.load_extension(f"cogs.{cog_name}")
                logging.info(f"✅ Loaded cog: {cog_name}")
            except Exception as e:
                logging.error(f"❌ Failed to load cog: {cog_name} — {e}")

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=settings.guild_id))
        logging.info(f"🔁 Synced {len(synced)} slash command(s) to guild {settings.guild_id}")
    except Exception as e:
        logging.error(f"⚠️ Slash command sync failed: {e}")


# Example basic command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

bot.run(settings.discord_token)

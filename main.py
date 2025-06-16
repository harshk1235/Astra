import discord
from discord.ext import commands
import logging
#from dotenv import load_dotenv
import os

intents.guilds = True
intents.members = True

logging.basicConfig(level=logging.INFO)

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"{bot.user} is online.")
    # Load all cogs from the cogs folder
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            cog_name = filename[:-3]
            try:
                await bot.load_extension(f"cogs.{cog_name}")
                logging.info(f"✅ Loaded cog: {cog_name}")

            except Exception as e:              
                logging.error(f"❌ Failed to load cog: {cog_name} — {e}")

    # Sync slash commands globally

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=1228025729183383685))
        logging.info(f"🔁 Synced {len(synced)} slash command(s) to guild {1228025729183383685}")

    except Exception as e:
        logging.error(f"⚠️ Slash command sync failed: {e}")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")



bot.run(TOKEN)

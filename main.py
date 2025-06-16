import discord
from discord.ext import commands
import logging
import os

# Bot token and owner ID
TOKEN = os.environ.get("TOKEN")
OWNER_ID = 440506087326744576  # 🔁 Replace this with your real Discord user ID

# Intents and logging
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

logging.basicConfig(level=logging.INFO)

# Create bot
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Load cogs on startup
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

# Basic text command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Manual sync command (text-based, owner only)
@bot.command()
async def sync(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ You must be the owner to use this command.")
        return

    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} global slash command(s).")
        logging.info(f"✅ Synced {len(synced)} global slash command(s).")
    except Exception as e:
        logging.error(f"⚠️ Slash sync failed: {e}")
        await ctx.send("❌ Sync failed.")

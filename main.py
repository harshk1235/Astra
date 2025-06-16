import discord
from discord.ext import commands
from discord import app_commands
import logging
import os

# Bot token from environment
TOKEN = os.environ.get("TOKEN")
OWNER_ID = 440506087326744576  # 🔁 Replace with your Discord user ID

# Define intents and setup logging
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

logging.basicConfig(level=logging.INFO)

# Create the bot
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree  # for convenience

# Event: on_ready
@bot.event
async def on_ready():
    logging.info(f"{bot.user} is online.")

    # Load all cogs from /cogs
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            cog_name = filename[:-3]
            try:
                await bot.load_extension(f"cogs.{cog_name}")
                logging.info(f"✅ Loaded cog: {cog_name}")
            except Exception as e:
                logging.error(f"❌ Failed to load cog: {cog_name} — {e}")

# Text command for testing
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Slash command for testing
@tree.command(name="ping", description="Replies with pong")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong!", ephemeral=True)

# Owner-only sync command to register slash commands globally
@tree.command(name="sync", description="Owner only: Sync global slash commands")
async def sync_commands(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ You must be the owner to use this command!", ephemeral=True)
        return
    try:
        await tree.sync()
        await interaction.response.send_message("✅ Slash commands synced globally.", ephemeral=True)
        logging.info("✅ Slash commands synced globally.")
    except Exception as e:
        logging.error(f"⚠️ Sync failed: {e}")
        await interaction.response.send_message("❌ Sync failed.", ephemeral=True)

# Run the bot
bot.run(TOKEN)

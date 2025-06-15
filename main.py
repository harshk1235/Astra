import discord
from discord.ext import commands
#from dotenv import load_dotenv
import os

#load_dotenv()
TOKEN = os.environ.get("TOKEN")

# Define intents and bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} is online.")

    # Load all cogs from the cogs folder
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"Loaded cog: {filename}")
            except Exception as e:
                print(f"Failed to load cog {filename}: {e}")

    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global slash command(s).")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")


# Example basic command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

bot.run(TOKEN)

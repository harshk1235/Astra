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
    # Load all cogs from cogs folder
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")
    # Sync slash commands
    await bot.tree.sync()
   # await bot.tree.sync(guild=discord.Object(id=1228025729183383685))
    print("Slash commands synced.")

# Example basic command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

bot.run(TOKEN)

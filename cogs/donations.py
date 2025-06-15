import os
import discord
from discord.ext import commands
from discord import app_commands
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
from google.oauth2.service_account import Credentials


class Donations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
      

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file("/etc/secrets/credentials.json", scopes=scope)
        self.gc = gspread.authorize(creds)


        self.sheet_url = os.environ.get("GOOGLE_SHEET_URL")
        # Google Sheets setup
      #  with open("credentials.json", "r") as f:
     #       creds = json.load(f)
      #  self.gc = gspread.service_account_from_dict(creds)
      #  with open("config.json", "r") as file:
      #      config = json.load(file)
      #      self.sheet_url = config.get("google_sheet_url")
        self.sheet = self.gc.open_by_url(self.sheet_url).worksheet("Donations")

    @commands.command()
    async def hello(self,ctx):
        await ctx.send("Hello from donations cog")
    
    @commands.command(name="donos")
    @commands.has_permissions(administrator=True)
    async def donos_prefix(self, ctx):
        await self.send_donos_embed(ctx)

    @app_commands.command(name="donos", description="Displays donation leaderboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def donos_slash(self, interaction: discord.Interaction):
        await self.send_donos_embed(interaction)

    async def send_donos_embed(self, source):
        try:
            data = self.sheet.get_all_values()[1:]  # skip header row
            heading = "`Donated\tReceived`\n\n"
            text = ""

            for row in data:
                name = row[0]
                donated = row[1]
                received = row[2]
                text += f"`{donated:^7}\t{received:^8}`   {name}\n"

            embed = discord.Embed(
                title="📤 Donation Leaderboard 📥",
                description="\n\n💰 Here's a look at who’s fueling the clan with generosity this season! Check out the top donors and receivers below. \n\n" + heading + text,
                color=discord.Color.from_rgb(0, 0, 0)
            )
            embed.set_footer(
                text="Phoenix Reborn",
                icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png?ex=68500f97&is=684ebe17&hm=8dde095ff75caa82b9e8a2ded1b35682220e8a47ba0cf3026c64f491b89ea8d3&=&format=webp&quality=lossless"
            )

            if isinstance(source, commands.Context):
                await source.send(embed=embed)
            else:
                await source.response.send_message(embed=embed)

        except Exception as e:
            print(e)
            if isinstance(source, commands.Context):
                await source.send("Failed to fetch donation stats.")
            else:
                await source.response.send_message("Failed to fetch donation stats.")

async def setup(bot):
    cog = Donations(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.donos)
    

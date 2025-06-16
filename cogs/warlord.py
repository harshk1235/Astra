import discord
from discord.ext import commands
from discord import app_commands
from google.oauth2.service_account import Credentials
import gspread
import os
import json

class WarlordCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Google Sheets setup
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        #creds = Credentials.from_service_account_file("/etc/secrets/credentials.json", scopes=scope)
       
        cred_str = os.environ.get("CREDENTIALS_JSON")
        cred_dict = json.loads(cred_str)
        creds = Credentials.from_service_account_info(cred_dict, scopes=scope)

        self.gc = gspread.authorize(creds)

        # Get sheet URL from environment variable
        self.sheet_url = os.environ.get("GOOGLE_SHEET_URL")

        # Load emojis from file
        with open("emoji.json", "r", encoding="utf-8") as f:
            self.emojis = json.load(f)

    @commands.command(name="warstats", help="Displays the war leaderboard")
    @commands.has_permissions(administrator=True)
    async def warstats(self, ctx, channel: discord.TextChannel = None):
        try:
            if channel is None:
                channel = ctx.channel

            sheet = self.gc.open_by_url(self.sheet_url).worksheet("War Stats")
            data = sheet.get_all_values()

            MAX_LENGTH = 4096
            heading = "<:blank_space:1229767163221381173><:blank_space:1229767163221381173>`Atk\tTrp\tH/R%\t Dest`\n\n"
            body = ""

            embeds = []

            for row in data[1:]:
                th = f"th{row[1]}"
                emoji = self.emojis.get(th, "")
                line = f"{emoji}<:blank_space:1229767163221381173}`{row[2]:^3}\t{row[3]:^3}\t{row[4]:>3}%\t {row[5]:>4}` \t {row[0]}\n"

                if len(body) + len(line) >= MAX_LENGTH - len(heading):
                    embed = discord.Embed(
                        title="🏆 War Leaderboard 🏆" if not embeds else discord.Embed.Empty,
                        description=heading + body if not embeds else body,
                        color=0x000000
                    )
                    embed.set_footer(
                        text="Phoenix Reborn",
                        icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png"
                    )
                    embeds.append(embed)
                    body = ""

                body += line

            # Add final embed for leftover lines
            if body:
                embed = discord.Embed(
                    title="🏆 War Leaderboard 🏆" if not embeds else discord.Embed.Empty,
                    description=heading + body if not embeds else body,
                    color=0x000000
                )
                embed.set_footer(
                    text="Phoenix Reborn",
                    icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png"
                )
                embeds.append(embed)

            for embed in embeds:
                await channel.send(embed=embed)

        except Exception as e:
            print("Error in !warstats:", e)
            await ctx.send("❌ An error occurred while retrieving war stats.")


async def setup(bot: commands.Bot):
    await bot.add_cog(WarlordCog(bot))

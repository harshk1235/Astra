import discord
from discord.ext import commands
from discord import app_commands

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

class WarlordCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        cred_str = os.environ.get("CREDENTIALS_JSON")
        cred_dict = json.loads(cred_str)
        # ✅ Setup Google Sheets client
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)
        self.gc = gspread.authorize(creds)

        # ✅ Load sheet URL from config
       # with open("config.json", "r") as file:
        #    config = json.load(file)
        #    self.sheet_url = config.get("google_sheet_url")

        self.sheet_url = os.environ.get("GOOGLE_SHEET_URL")
        # ✅ Load emojis from emoji.json
        with open("emoji.json", "r", encoding="utf-8") as f:
            emoji_data = json.load(f)

        self.emojis = emoji_data  # Access like self.emojis["th15"]

        self.th17 = emoji_data.get("th17", "")
        self.th16 = emoji_data.get("th16", "")
        self.th15 = emoji_data.get("th15", "")
        self.th14 = emoji_data.get("th14", "")
        self.th13 = emoji_data.get("th13", "")
        self.th12 = emoji_data.get("th12", "")
        self.th11 = emoji_data.get("th11", "")
        self.th10 = emoji_data.get("th10", "")

    # [Command will be added later here]
    @app_commands.command(name="warstats", description="Displays the war leaderboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def warstats(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        try:
            if channel is None:
                channel = interaction.channel

            sheet = self.gc.open_by_url(self.sheet_url).worksheet("War Stats")
            data = sheet.get_all_values()

            heading = "<:blank_space:1229767163221381173><:blank_space:1229767163221381173>`Atk\tTrp\tH/R%\t Dest`\n\n"
            body = ""

            for row in data[1:]:
                th = f"th{row[1]}"
                emoji = self.emojis.get(th, "")
                body += f"{emoji}<:blank_space:1229767163221381173>`{row[2]:^3}\t{row[3]:^3}\t{row[4]:>3}%\t {row[5]:>4}` \t {row[0]}\n"

            embed = discord.Embed(
                title="🏆 War Leaderboard 🏆",
                description="\n**Greetings, Clashers!**\n\nJoin us in celebrating the noble feats of our champions as we unveil the monthly war leaderboard for Phoenix Reborn\n\n"
                            + heading + body,
                color=0x000000
            )
            embed.set_footer(text="Pheonix Reborn", icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png?ex=68500f97&is=684ebe17&hm=8dde095ff75caa82b9e8a2ded1b35682220e8a47ba0cf3026c64f491b89ea8d3&=&format=webp&quality=lossless")

            await interaction.response.send_message("Leaderboard sent!", ephemeral=True)
            await channel.send(embed=embed)

        except Exception as e:
            print("Error:", e)
            await interaction.response.send_message("An error occurred while retrieving stats info.", ephemeral=True)
async def setup(bot: commands.Bot):
    await bot.add_cog(WarlordCog(bot))

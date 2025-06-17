import discord
from discord.ext import commands
from discord.ui import View, Select, Button
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import os
import json
from collections import defaultdict



class CWLStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gc = self.authorize_gsheet()
        self.owner_accounts = {}  # owner_name: [account_name, ...]
        self.cwl_data = {}        # account_name: full CWL data row
        self.sheet_name = self.get_current_cwl_sheet_name()
        

    def authorize_gsheet(self):
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        cred_str = os.environ.get("CREDENTIALS_JSON")
        cred_dict = json.loads(cred_str)
        creds = Credentials.from_service_account_info(cred_dict, scopes=scope)

       
        return gspread.authorize(creds)

    def get_current_cwl_sheet_name(self):
        now = datetime.now()
        return f"Cwl {now.strftime('%B')}{now.year}"

    def load_data(self):
        try:
            # Load player tags
            tags_ws = self.gc.open_by_url(os.environ["GOOGLE_SHEET_URL"]).worksheet("Player Tags")
            tag_data = tags_ws.get_all_values()[1:]  # Skip header

            self.owner_accounts = defaultdict(list)
            tag_map = {}  # tag -> account name

            for row in tag_data:
                if len(row) >= 3:
                    tag, name, owner = row
                    self.owner_accounts[owner].append(name)
                    tag_map[name] = tag

            # Load CWL data
            cwl_ws = self.gc.open_by_url(os.environ["GOOGLE_SHEET_URL"]).worksheet(self.sheet_name)            
            cwl_rows = cwl_ws.get_all_values()
            headers = cwl_rows[0]
            data = cwl_rows[1:]

            for row in data:
                if row and row[0] in tag_map:  # account name matched
                    self.cwl_data[row[0]] = row

            return True
        except Exception as e:
            print(f"❌ Error loading CWL data: {e}")
            return False
    @commands.command(name="cwlstats", help="Displays the CWL leaderboard for this month")
    async def cwlstats(self, ctx):
        if not self.load_data():
            await ctx.send("❌ Failed to load CWL data.")
            return

        owners = list(self.owner_accounts.keys())
        owners.sort()

        pages = self.build_leaderboard_pages(owners=owners)
        current_page = 0

        view = CWLLeaderboardView(bot=self.bot, pages=pages, owner_list=owners, ctx=ctx)
        await ctx.send(embed=pages[current_page], view=view)

    def build_leaderboard_pages(self, owners, per_page=30):
        pages = []
        emoji_path = "emoji.json"
        with open(emoji_path, "r", encoding="utf-8") as f:
            emoji_data = json.load(f)

        blank = "<:blank_space:1229767163221381173>"

        # Step 1: Flatten all CWL rows in original sheet order
        # No sorting, no regrouping — we use CWL sheet order directly
        all_rows = list(self.cwl_data.values())

        # Step 2: Paginate by 30 entries per page
        for i in range(0, len(all_rows), per_page):
            chunk = all_rows[i:i + per_page]
            text = f"{blank}{blank}`⚔️   🎯   ⭐   ⬇️   ⬆️`\n\n"  # Header line

            for row in chunk:
                name = row[0]  # account name
                th = row[3]
                hits = int(row[4]) if row[4].isdigit() else 0
                triples = int(row[5]) if row[5].isdigit() else 0
                total = int(row[6]) if row[6].isdigit() else 0
                dips = int(row[21]) if len(row) > 21 and row[21].isdigit() else 0
                reaches = int(row[22]) if len(row) > 22 and row[22].isdigit() else 0

                th_emoji = emoji_data.get(f"th{th}", "")
                line = f"{th_emoji}{blank}`{hits:^3}   {triples:^2}   {total:^3}   {dips:^2}   {reaches:^2}`    {name}\n"
                text += line

            embed = discord.Embed(
                title="🏆 CWL Leaderboard",
                description="**Top performers in this month's CWL**\n\n" + text,
                color=0x000000
            )
            embed.set_footer(text=f"Phoenix Reborn – Page {i // per_page + 1}")
            pages.append(embed)

        return pages


class CWLLeaderboardView(View):
    def __init__(self, bot, pages, owner_list, ctx):
        super().__init__(timeout=100)
        self.bot = bot
        self.pages = pages
        self.current = 0
        self.owner_list = owner_list
        self.ctx = ctx

        self.add_item(CWLDropdown(owner_list, self.ctx))

        self.prev = Button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
        self.next = Button(label="Next ➡️", style=discord.ButtonStyle.secondary)

        self.prev.callback = self.go_prev
        self.next.callback = self.go_next

        self.add_item(self.prev)
        self.add_item(self.next)

    async def go_prev(self, interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You're not the original user.", ephemeral=True)

        if self.current > 0:
            self.current -= 1
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    async def go_next(self, interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You're not the original user.", ephemeral=True)

        if self.current < len(self.pages) - 1:
            self.current += 1
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)


class CWLDropdown(Select):
    def __init__(self, owner_list, ctx):
        options = [
            discord.SelectOption(label=owner, description="View detailed stats", value=owner)
            for owner in owner_list[:25]  # ⬅️ Show only the first 25 owners
        ]
        super().__init__(placeholder="Select a player to view stats", min_values=1, max_values=1, options=options)
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You're not the original user.", ephemeral=True)

        selected_owner = self.values[0]
        # await interaction.response.send_message(f"📊 Loading detailed stats for **{selected_owner}**...", ephemeral=True)

        # 👇 Later: This is where we'll call the detailed view setup
        # e.g., await show_detailed_view(selected_owner)
        # Step 1: Get bot + view that triggered this
        parent_view = self.view
        cog = parent_view.bot.get_cog("CWLStats")

        # Step 2: Gather all accounts for selected owner
        accounts = cog.owner_accounts.get(selected_owner, [])
        account_data = [cog.cwl_data.get(acc) for acc in accounts if acc in cog.cwl_data]

        if not account_data:
            return await interaction.followup.send("No CWL data found for that owner.", ephemeral=True)

        # Step 3: Build and show detailed view
        detail_view = CWLDetailView(bot=cog.bot, owner_name=selected_owner, accounts_data=account_data, ctx=self.ctx, back_embed=parent_view.pages[parent_view.current], back_view=parent_view)
        await interaction.response.edit_message(embed=detail_view.pages[0], view=detail_view)


class CWLDetailView(View):
    def __init__(self, bot, owner_name, accounts_data, ctx, back_embed, back_view):
        super().__init__(timeout=90)
        self.bot = bot
        self.owner = owner_name
        self.ctx = ctx
        self.accounts = accounts_data
        self.back_embed = back_embed
        self.back_view = back_view
        self.page = 0
        self.pages = self.build_pages()

        self.prev = Button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
        self.next = Button(label="Next ➡️", style=discord.ButtonStyle.secondary)
        self.back = Button(label="🔙 Back to Leaderboard", style=discord.ButtonStyle.red)

        self.prev.callback = self.go_prev
        self.next.callback = self.go_next
        self.back.callback = self.go_back

        if len(self.pages) > 1:
            self.add_item(self.prev)
            self.add_item(self.next)

        self.add_item(self.back)

    def build_pages(self):
        pages = []
        emoji_path = "emoji.json"
        emoji_numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
        with open(emoji_path, "r", encoding="utf-8") as f:
            emoji_data = json.load(f)

        for i in range(0, len(self.accounts), 3):
            chunk = self.accounts[i:i+3]
            description = f"**Detailed Stats ({datetime.now().strftime('%B %Y')})**\nFor **{self.owner}**\n\n"

            for row in chunk:
                name = row[0]
                tag = row[1] if row[1].startswith('#') else f"#{row[1]}"
                th = row[3]
                th_emoji = emoji_data.get(f"th{th}", "")
                description += f"{th_emoji}**{name}** ({tag})\n"

                total_hits = total_stars = total_destruction = 0
                for day in range(1, 8):
                    star_col = 7 + (day - 1) * 2
                    pct_col = star_col + 1

                    if star_col >= len(row): break
                    stars_raw = row[star_col].strip()
                    pct_raw = row[pct_col].strip()

                    if stars_raw and pct_raw:
                        try:
                            stars = int(stars_raw)
                            percent = float(pct_raw)
                            total_hits += 1
                            total_stars += stars
                            total_destruction += percent

                            stars_display = "★" * stars + "☆" * (3 - stars)
                            description += f"{emoji_numbers[day - 1]} {stars_display} ({int(percent)}%)\n"
                        except:
                            continue

                description += f"\n⚔️ {total_hits} | ⭐ {total_stars} | 💥 {round(total_destruction)}%\n\n"

            embed = discord.Embed(
                description=description.strip(),
                color=0x000000
            )
            embed.set_footer(text=f"Phoenix Reborn — {self.owner} • Page {i//3 + 1}")
            pages.append(embed)

        return pages

    async def go_prev(self, interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You didn't invoke the command.", ephemeral=True)
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    async def go_next(self, interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You didn't invoke the command.", ephemeral=True)
        if self.page < len(self.pages) - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    async def go_back(self, interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You didn't invoke the command.", ephemeral=True)
        await interaction.response.edit_message(embed=self.back_embed, view=self.back_view)


async def setup(bot):
    await bot.add_cog(CWLStats(bot))

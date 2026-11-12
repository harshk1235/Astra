import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button
from datetime import datetime, timezone
from typing import Optional
import json
import logging
from collections import defaultdict

from astra.branding import footer
from astra.clan_option import clan_autocomplete, resolve_filters
from astra.db.wars import WarsRepository
from astra.csv_export import CsvButton, safe_filename
from astra.exports import CWL_HEADER, cwl_rows
from astra.month_option import MonthError, month_autocomplete
from astra.months import cwl_sheet_name, month_label, next_month_start
from astra.timing import timed

log = logging.getLogger(__name__)


class CWLStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_current_cwl_sheet_name(self):
        return cwl_sheet_name(datetime.now(timezone.utc))

    async def load_data(self, start, clan_tag=None):
        """(owner -> [account keys], key -> CWL row). Rows use the CWL sheet's
        21 columns (name, tag, clan, TH, hits, triples, stars, then stars and %
        for days 1-7), plus the owner's Discord ID as a 22nd column when known.
        With a database, keys are account tags (names aren't unique); without
        one, the old sheets are read as before.

        Returned instead of stored on the cog: two people running /cwlstats for
        different months at the same time must not see each other's data."""
        if self.bot.db is not None:
            entries = await WarsRepository(self.bot.db).cwl_stats(start, next_month_start(start), clan_tag)
            owner_accounts, cwl_data = defaultdict(list), {}
            for entry, row in zip(entries, cwl_rows(entries)[1:]):
                owner_accounts[entry.owner].append(entry.tag)
                cwl_data[entry.tag] = [str(c) for c in row] + [str(entry.discord_id or "")]
            return owner_accounts, cwl_data

        tag_data = (await self.bot.sheets.get_values("Player Tags"))[1:]  # Skip header
        owner_accounts, names = defaultdict(list), set()
        for row in tag_data:
            if len(row) >= 3:
                tag, name, owner = row[:3]
                owner_accounts[owner].append(name)
                names.add(name)
        # Work out the month's sheet on every call: computing it once at
        # startup kept reading last month's sheet until a restart.
        cwl_rows_ = await self.bot.sheets.get_values(cwl_sheet_name(start))
        # The old sheets had Dips/Reaches in columns 22-23; drop them.
        cwl_data = {row[0]: row[:21] for row in cwl_rows_[1:] if row and row[0] in names}
        return owner_accounts, cwl_data

    # Hybrid: works as !cwlstats [month] [clan] and as /cwlstats.
    @commands.hybrid_command(name="cwlstats", description="CWL leaderboard for a month")
    @app_commands.describe(month="Which month's CWL (default: this month)",
                           clan="One clan only (default: all clans)")
    @app_commands.autocomplete(month=month_autocomplete, clan=clan_autocomplete)
    async def cwlstats(self, ctx, month: Optional[str] = None, clan: Optional[str] = None):
        await ctx.defer()   # slash commands must be acknowledged within 3 seconds
        try:
            start, clan_ = await resolve_filters(self.bot, month, clan)
        except MonthError as e:
            return await ctx.send(str(e), ephemeral=True)
        try:
            with timed("cwlstats.load"):
                owner_accounts, cwl_data = await self.load_data(start, clan_.tag if clan_ else None)
        except Exception:
            log.exception("Error loading CWL data")
            await ctx.send("❌ Failed to load CWL data.")
            return
        if not cwl_data:
            await ctx.send(f"No CWL stats for {month_label(start)} yet.")
            return

        # Only people who actually played this CWL go in the dropdown
        # (Discord allows at most 25 options).
        owners = sorted(o for o, accs in owner_accounts.items() if any(a in cwl_data for a in accs))
        title = f"{month_label(start)}" + (f" · {clan_.alias}" if clan_ else "")
        pages = self.build_leaderboard_pages(cwl_data, title)
        view = CWLLeaderboardView(bot=self.bot, pages=pages, owner_list=owners, ctx=ctx,
                                  owner_accounts=owner_accounts, cwl_data=cwl_data, month=month_label(start))
        await ctx.send(embed=pages[0], view=view)

    def build_leaderboard_pages(self, cwl_data, month, per_page=25):
        """Leaderboard pages in CWL sheet order (TH, triples, stars). A page
        holds up to `per_page` accounts and always stays under Discord's
        4096-character embed limit, now that lines carry a mention."""
        emoji_path = "emoji.json"
        with open(emoji_path, "r", encoding="utf-8") as f:
            emoji_data = json.load(f)

        blank = "<:blank_space:1229767163221381173>"
        header = f"{blank}{blank}`⚔️   🎯   ⭐`\n\n"
        intro = f"**Top performers in {month}'s CWL**\n\n"

        lines = []
        for row in cwl_data.values():
            name, th = row[0], row[3]
            hits = int(row[4]) if row[4].isdigit() else 0
            triples = int(row[5]) if row[5].isdigit() else 0
            total = int(row[6]) if row[6].isdigit() else 0
            mention = f" <@{row[21]}>" if len(row) > 21 and row[21].isdigit() else ""
            th_emoji = emoji_data.get(f"th{th}", "")
            lines.append(f"{th_emoji}{blank}`{hits:^3}   {triples:^2}   {total:^3}`    {name}{mention}\n")

        chunks, current = [], []
        for line in lines:
            size = len(intro) + len(header) + sum(map(len, current)) + len(line)
            if current and (len(current) >= per_page or size > 4000):
                chunks.append(current)
                current = []
            current.append(line)
        if current:
            chunks.append(current)

        pages = []
        for n, chunk in enumerate(chunks, 1):
            embed = discord.Embed(
                title="🏆 CWL Leaderboard",
                description=intro + header + "".join(chunk),
                color=0x000000
            )
            footer(embed, f"Page {n}/{len(chunks)}")
            pages.append(embed)
        return pages


class CWLLeaderboardView(View):
    def __init__(self, bot, pages, owner_list, ctx, owner_accounts, cwl_data, month):
        super().__init__(timeout=100)
        self.bot = bot
        self.pages = pages
        self.current = 0
        self.owner_list = owner_list
        self.ctx = ctx
        # This message's own data (see CWLStats.load_data).
        self.owner_accounts = owner_accounts
        self.cwl_data = cwl_data
        self.month = month

        # A dropdown holds at most 25 options; use up to 3 of them (75 players),
        # leaving a row for the buttons.
        chunks = [owner_list[i:i + 25] for i in range(0, len(owner_list), 25)][:3]
        for chunk in chunks:
            label = None if len(chunks) == 1 else f"Players {chunk[0][:12]} – {chunk[-1][:12]}"
            self.add_item(CWLDropdown(chunk, self.ctx, placeholder=label))

        self.prev = Button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
        self.next = Button(label="Next ➡️", style=discord.ButtonStyle.secondary)

        self.prev.callback = self.go_prev
        self.next.callback = self.go_next

        self.add_item(self.prev)
        self.add_item(self.next)
        # Download CSV: header + every row, with the owner's Discord ID.
        csv_rows = [CWL_HEADER + ["Discord ID"]] + [r + [""] * (len(CWL_HEADER) + 1 - len(r)) for r in cwl_data.values()]
        self.add_item(CsvButton(csv_rows, safe_filename("astra-cwl", month.replace(" ", "-"))))

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
    def __init__(self, owner_list, ctx, placeholder=None):
        options = [
            discord.SelectOption(label=owner[:100], description="View detailed stats", value=owner[:100])
            for owner in owner_list[:25]  # Discord's limit per dropdown
        ]
        super().__init__(placeholder=placeholder or "Select a player to view stats",
                         min_values=1, max_values=1, options=options)
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

        # Step 2: Gather all accounts for selected owner, from this message's data
        accounts = parent_view.owner_accounts.get(selected_owner, [])
        account_data = [parent_view.cwl_data[acc] for acc in accounts if acc in parent_view.cwl_data]

        if not account_data:
            # followup.send only works after a response; nothing has responded yet.
            return await interaction.response.send_message("No CWL data found for that owner.", ephemeral=True)

        # Step 3: Build and show detailed view
        detail_view = CWLDetailView(bot=parent_view.bot, owner_name=selected_owner, accounts_data=account_data, ctx=self.ctx, back_embed=parent_view.pages[parent_view.current], back_view=parent_view, month=parent_view.month)
        await interaction.response.edit_message(embed=detail_view.pages[0], view=detail_view)


class CWLDetailView(View):
    def __init__(self, bot, owner_name, accounts_data, ctx, back_embed, back_view, month):
        super().__init__(timeout=90)
        self.bot = bot
        self.month = month
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
            description = f"**Detailed Stats ({self.month})**\nFor **{self.owner}**\n\n"

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
            footer(embed, f"{self.owner} • Page {i//3 + 1}")
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

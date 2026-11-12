import logging

import discord
from discord import app_commands
from discord.ext import commands

from astra.branding import footer
from astra.clan_option import clan_autocomplete, resolve_filters
from astra.csv_export import CsvButton, safe_filename
from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.month_option import MonthError, month_autocomplete
from astra.months import month_key, month_label
from astra.timing import timed

log = logging.getLogger(__name__)

INTRO = ("\n\n💰 Here's a look at who's fueling the clan with generosity this season! "
         "Check out the top donors and receivers below. \n\n")
HEADING = "`Donated\tReceived`\n\n"
EMBED_LIMIT = 4000


def donation_pages(title: str, lines: list[str], updated_at, mode_label: str) -> list[discord.Embed]:
    """Split leaderboard lines into embeds under Discord's size limit."""
    chunks, body = [], ""
    for line in lines:
        if len(INTRO) + len(HEADING) + len(body) + len(line) > EMBED_LIMIT:
            chunks.append(body)
            body = ""
        body += line
    chunks.append(body)
    pages = []
    for n, chunk in enumerate(chunks, 1):
        embed = discord.Embed(title=title, description=INTRO + HEADING + chunk, color=discord.Color.from_rgb(0, 0, 0))
        footer(embed, f"{mode_label} • Page {n}/{len(chunks)}" if len(chunks) > 1 else mode_label)
        if updated_at is not None:
            embed.timestamp = updated_at  # Discord shows it as "Last updated" local time
        pages.append(embed)
    return pages


class DonosView(discord.ui.View):
    """Pages, a toggle between per-account and per-player (all of someone's
    accounts added up), and a CSV download of whichever view is showing."""

    def __init__(self, views: dict[str, tuple[list[discord.Embed], list[list], str]], mode: str = "accounts"):
        super().__init__(timeout=900)
        self.views, self.mode, self.page = views, mode, 0
        self.prev = discord.ui.Button(label="⬅️", style=discord.ButtonStyle.secondary)
        self.next = discord.ui.Button(label="➡️", style=discord.ButtonStyle.secondary)
        self.toggle = discord.ui.Button(style=discord.ButtonStyle.primary)
        self.prev.callback, self.next.callback, self.toggle.callback = self.go_prev, self.go_next, self.flip
        for item in (self.prev, self.next, self.toggle):
            self.add_item(item)
        self.csv: CsvButton | None = None
        self._refresh()

    @property
    def pages(self) -> list[discord.Embed]:
        return self.views[self.mode][0]

    def _refresh(self):
        self.toggle.label = "Per player" if self.mode == "accounts" else "Per account"
        self.toggle.emoji = "👥" if self.mode == "accounts" else "🏰"
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page >= len(self.pages) - 1
        if self.csv is not None:
            self.remove_item(self.csv)
        _, rows, filename = self.views[self.mode]
        self.csv = CsvButton(rows, filename)
        self.add_item(self.csv)

    async def _show(self, interaction: discord.Interaction):
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    async def go_prev(self, interaction):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    async def go_next(self, interaction):
        self.page = min(len(self.pages) - 1, self.page + 1)
        await self._show(interaction)

    async def flip(self, interaction):
        self.mode, self.page = ("players" if self.mode == "accounts" else "accounts"), 0
        await self._show(interaction)


class Donations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def hello(self, ctx):
        await ctx.send("Hello from donations cog")

    @commands.command(name="donos")
    @commands.has_permissions(administrator=True)
    async def donos_prefix(self, ctx, month: str = None, clan: str = None):
        await self.send_donos_embed(ctx, month, clan)

    @app_commands.command(name="donos", description="Displays donation leaderboard")
    @app_commands.describe(month="Which month (default: this month)", clan="One clan only (default: all clans)")
    @app_commands.autocomplete(month=month_autocomplete, clan=clan_autocomplete)
    async def donos_slash(self, interaction: discord.Interaction, month: str = None, clan: str = None):
        await self.send_donos_embed(interaction, month, clan)

    async def send_donos_embed(self, source, month=None, clan=None):
        is_slash = isinstance(source, discord.Interaction)
        if is_slash:
            # Discord fails a slash command that gets no response within 3 seconds.
            # The data load can take longer, so acknowledge first and reply after.
            await source.response.defer()
        send = source.followup.send if is_slash else source.send
        try:
            start, clan_ = await resolve_filters(self.bot, month, clan)
        except MonthError as e:
            return await send(str(e))
        try:
            title = (f"📤 Donation Leaderboard · {month_label(start)}"
                     + (f" · {clan_.alias}" if clan_ else "") + " 📥")
            if self.bot.db is None:
                # Old path: the Donations sheet, one combined view.
                data = (await self.bot.sheets.get_values("Donations"))[1:]
                lines = [f"`{r[1]:^7}\t{r[2]:^8}`   {r[0]}\n" for r in data]
                return await send(embed=donation_pages(title, lines, None, "Per player")[0])

            with timed("donos.load"):
                views = await self.load_views(start, clan_, title)
            if not views["accounts"][1][1:] and not views["players"][1][1:]:
                return await send(f"No donation data for {month_label(start)}"
                                  + (f" in {clan_.alias}." if clan_ else "."))
            view = DonosView(views)
            await send(embed=view.pages[0], view=view)
        except Exception:
            log.exception("Failed to build donation leaderboard")
            await send("Failed to fetch donation stats.")

    async def load_views(self, start, clan_, title):
        """{"accounts" | "players": (pages, csv rows, csv filename)}."""
        repo = DonationsRepository(self.bot.db)
        season, clan_tag = month_key(start), (clan_.tag if clan_ else None)
        accounts = await repo.account_leaderboard(season, clan_tag=clan_tag)
        people = await repo.leaderboard(season, clan_tag=clan_tag)
        updated_at = await JobRunsRepository(self.bot.db).last_success("donations")
        mention = lambda d: f" <@{d}>" if d else ""
        acc_lines = [f"`{a.donated:^7}\t{a.received:^8}`   {a.name}{mention(a.discord_id)}\n" for a in accounts]
        ppl_lines = [f"`{p.donated:^7}\t{p.received:^8}`   " + (f"<@{p.discord_id}>" if p.discord_id else p.name) + "\n"
                     for p in people]
        base = ["astra-donations", clan_.alias if clan_ else "", month_key(start)]
        return {
            "accounts": (donation_pages(title, acc_lines, updated_at, "Per account"),
                         [["Account", "Tag", "Donated", "Received", "Discord ID"]]
                         + [[a.name, a.tag, a.donated, a.received, a.discord_id or ""] for a in accounts],
                         safe_filename(*base, "accounts")),
            "players": (donation_pages(title, ppl_lines, updated_at, "Per player"),
                        [["Player", "Donated", "Received", "Discord ID"]]
                        + [[p.name, p.donated, p.received, p.discord_id or ""] for p in people],
                        safe_filename(*base, "players")),
        }


async def setup(bot):
    await bot.add_cog(Donations(bot))

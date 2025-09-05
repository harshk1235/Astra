import discord
from discord.ext import commands
from discord import app_commands
import logging
import os

TOKEN = os.environ.get("TOKEN")

# IDs you must configure
GUILD_ID = 1228025729183383685  # your server
STAFF_ROLE_ID = 123456789012345678  # staff role ID
REVIEW_CHANNEL_ID = 123456789012345678  # staff-only channel ID

# Intents + setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

logging.basicConfig(level=logging.INFO)

bot = commands.Bot(command_prefix="!", intents=intents)

# Simple in-memory tracking (will replace with Google Sheet later)
player_progress = {}


# ============================
# On ready
# ============================
@bot.event
async def on_ready():
    logging.info(f"{bot.user} is online.")

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        logging.info(f"🔁 Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
    except Exception as e:
        logging.error(f"⚠️ Slash command sync failed: {e}")


# ============================
# View for Approve/Reject buttons
# ============================
class SubmissionReview(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only staff can use the buttons"""
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
        if staff_role in interaction.user.roles:
            return True
        await interaction.response.send_message("⛔ You are not authorized.", ephemeral=True)
        return False

    @discord.ui.button(label="Approve ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.guild.get_member(self.user_id)
        if not user:
            await interaction.response.send_message("⚠️ User not found.", ephemeral=True)
            return

        # Update embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text=f"✅ Approved by {interaction.user.display_name}")
        await interaction.message.edit(embed=embed, view=None)

        # DM player
        next_round = player_progress.get(self.user_id, 0) + 1
        player_progress[self.user_id] = next_round
        try:
            await user.send(f"🎉 Your submission has been approved!\nHere is your decoration clue for **Round {next_round}**: (placeholder)")
        except:
            await interaction.channel.send(f"⚠️ Could not DM {user.mention}")

        await interaction.response.send_message("Submission approved ✅", ephemeral=True)

    @discord.ui.button(label="Reject ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.guild.get_member(self.user_id)
        if not user:
            await interaction.response.send_message("⚠️ User not found.", ephemeral=True)
            return

        # Update embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_footer(text=f"❌ Rejected by {interaction.user.display_name}")
        await interaction.message.edit(embed=embed, view=None)

        # DM player
        try:
            await user.send("❌ Your submission has been rejected. Please try again with a new attack.")
        except:
            await interaction.channel.send(f"⚠️ Could not DM {user.mention}")

        await interaction.response.send_message("Submission rejected ❌", ephemeral=True)


# ============================
# Slash Command: /submit
# ============================
@bot.tree.command(name="submit", description="Submit your attack and decoration screenshots")
@app_commands.describe(attack_image="Screenshot of your 3-star attack", decoration_image="Screenshot showing the decoration")
async def submit(interaction: discord.Interaction, attack_image: discord.Attachment, decoration_image: discord.Attachment):
    user = interaction.user

    # Decorative embed in public channel
    public_embed = discord.Embed(
        title="🎯 Submission Received!",
        description=f"{user.mention} has submitted their attack.\n\n⚖️ Waiting for staff verification...",
        color=discord.Color.yellow()
    )
    public_embed.set_footer(text="Staff will review this soon.")
    await interaction.response.send_message(embed=public_embed, view=SubmissionReview(user.id))

    # Send images to staff-only review channel
    review_channel = interaction.guild.get_channel(REVIEW_CHANNEL_ID)
    if review_channel:
        staff_embed = discord.Embed(
            title="🕵️ New Submission",
            description=f"From: {user.mention}\nRound: {player_progress.get(user.id, 0) + 1}",
            color=discord.Color.blurple()
        )
        staff_embed.add_field(name="Jump Link", value=f"[Go to submission]({interaction.channel.jump_url})", inline=False)
        staff_embed.set_footer(text="Review with Approve/Reject buttons in the submission channel.")

        await review_channel.send(
            content=f"<@&{STAFF_ROLE_ID}> New submission to review!",
            embeds=[staff_embed]
        )
        await review_channel.send(files=[await attack_image.to_file(), await decoration_image.to_file()])


# ============================
# Run bot
# ============================
bot.run(TOKEN)

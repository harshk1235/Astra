import os
import random
import discord
from discord import app_commands
from discord.ext import commands
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
from google.oauth2.service_account import Credentials
import asyncio

# ================== CONFIG ===================
TOKEN = os.environ.get("TOKEN")
GUILD_ID = 688449973553201335   # your guild id
STAFF_ROLE_ID = 1413549551075459254 # staff role id
STAFF_CHANNEL_ID = 1413551509454455016 # staff-only channel id
OWNER_ID = 440506087326744576  # your Discord ID for /reset_event

CLUES = [ "https://media.discordapp.net/attachments/1261931986772033596/1413438360948707428/IMG_5081.png?ex=68bbee8d&is=68ba9d0d&hm=dd3124fde1b254bb370f22eaac080acacb68e22ab97c0605f9691f6af51891fe&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438361305092162/IMG_5075.png?ex=68bbee8d&is=68ba9d0d&hm=d8f5c6d30cb79035cf28051eca5472bf176fd8393efe0ca6ede7217e97fc41b7&=&format=webp&quality=lossless",
"https://media.discordapp.net/attachments/1261931986772033596/1413438361636700161/IMG_5080.png?ex=68bbee8d&is=68ba9d0d&hm=645c99bc4e4b81b8db2af0255782d575b6fbf0dc1848c02367b9b8e4bfed6fd1&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438360621678612/IMG_5082.png?ex=68bbee8d&is=68ba9d0d&hm=549ad684c59c26fb6316afe5f1739c71a56b5afbfe7825904692d6ef0da4b4a5&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438361959534632/IMG_5074.png?ex=68bbee8d&is=68ba9d0d&hm=512dc6f444b550bb19f53b50c4db433391f80014e46d21421189528c615d67e3&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438362324308028/IMG_5077.png?ex=68bbee8d&is=68ba9d0d&hm=912386ddba294aa7e3ecd362d96381bd33c52119da7db7ac5d6b4ae1bc877a6e&=&format=webp&quality=lossless&width=376&height=696", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438362714636369/IMG_5073.png?ex=68bbee8e&is=68ba9d0e&hm=7aa684efc05ed66775dfea3a03d28b7d4ccfb698e6d907fbfec720774120a5e1&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438363112968265/IMG_5083.png?ex=68bbee8e&is=68ba9d0e&hm=4b26350ca176c1e1cbeb1485d867aa67123a8ae038da94c552c8cb23a689abcb&=&format=webp&quality=lossless", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438363435798579/IMG_5072.png?ex=68bbee8e&is=68ba9d0e&hm=0d5b6e7564db54d2f288c1f4cd718b330d01b697f42374d2dab0d274d0aab85b&=&format=webp&quality=lossless&width=608&height=697", 
"https://media.discordapp.net/attachments/1261931986772033596/1413438363779989545/IMG_5076.png?ex=68bbee8e&is=68ba9d0e&hm=8c2a0d1a7067c9f85e49c87057c19ae5e0a502e46ec4fe685f40fb750ca6f3d7&=&format=webp&quality=lossless" 
# ... add up to 10 clues 
] 

BONUS_CLUE = "https://media.discordapp.net/attachments/1261931986772033596/1413538517354807366/IMG_20250905_202712.jpg?ex=68bc4bd4&is=68bafa54&hm=fd87422abdee742c12ca3b747acb251298584e42ef8c9c5cdaeb2c9fe1bdfaa7&=&format=webp"



# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
cred_str = os.environ.get("CREDENTIALS_JSON")
cred_dict = json.loads(cred_str)
creds = Credentials.from_service_account_info(cred_dict, scopes=scope)
client = gspread.authorize(creds)

SHEET_URL = os.environ.get("SHEET_URL")
sheet = client.open_by_url(SHEET_URL).sheet1   # first sheet

# Ensure header exists
if sheet.row_values(1) == []:
    sheet.insert_row(["DiscordID", "Name", "Round", "UsedClues"], 1)

# ================== BOT SETUP ===================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ================== HELPERS ===================
def is_registered(user_id: int):
    users = sheet.col_values(1)
    return str(user_id) in users

def get_row(user_id: int):
    users = sheet.col_values(1)
    if str(user_id) in users:
        return users.index(str(user_id)) + 1
    return None

def clear_sheet():
    sheet.resize(rows=1)  # keeps header row only

# ================== CONFIG ===================
EVENT_ACTIVE = False
REGISTRATION_OPEN = True

def get_config(key: str) -> str:
    config_sheet = client.open_by_url(SHEET_URL).worksheet("Config")
    values = config_sheet.get_all_records()
    for row in values:
        if row["Key"] == key:
            return row["Value"]
    return None

def set_config(key: str, value: str):
    config_sheet = client.open_by_url(SHEET_URL).worksheet("Config")
    data = config_sheet.get_all_records()
    for i, row in enumerate(data, start=2):  # start=2 because row 1 is header
        if row["Key"] == key:
            config_sheet.update_cell(i, 2, value)
            return
    # if not found, append new row
    config_sheet.append_row([key, value])


def load_config():
    global EVENT_ACTIVE, REGISTRATION_OPEN
    ea = get_config("EVENT_ACTIVE")
    ro = get_config("REGISTRATION_OPEN")

    EVENT_ACTIVE = True if ea == "TRUE" else False
    REGISTRATION_OPEN = True if ro == "TRUE" else False


# ================== COMMANDS ===================
@bot.event
async def on_ready():
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Logged in as {bot.user}. Slash commands synced.")
    load_config()

# ---- REGISTER ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="register", description="Register for the scavenger hunt")
async def register(interaction: discord.Interaction):
    global REGISTRATION_OPEN
    if not REGISTRATION_OPEN:
        return await interaction.response.send_message("🚫 Registration is closed.")

    if is_registered(interaction.user.id):
        return await interaction.response.send_message("⚠️ You are already registered!")

    sheet.append_row([str(interaction.user.id), interaction.user.name, "0", ""])
    await interaction.response.send_message("✅ You are registered!")

# ---- FORCE REGISTER (staff only) ----
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="force_register",
    description="Staff: register a player even if registration is closed"
)
@app_commands.describe(member="Player to register")
async def force_register(interaction: discord.Interaction, member: discord.Member):
    # staff gate
    if STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message(
            "🚫 Only staff can use this command.", ephemeral=True
        )

    if is_registered(member.id):
        return await interaction.response.send_message(
            f"⚠️ {member.mention} is already registered."
        )

    # add row: DiscordID | Name | Round | UsedClues
    sheet.append_row([str(member.id), member.display_name, "0", ""])
    await interaction.response.send_message(
        f"✅ {member.mention} has been registered for the event."
    )

    # optional: let the player know via DM (ignore DM failures)
    try:
        await member.send("✅ You have been registered for the scavenger hunt by staff.")
    except Exception:
        pass

# ---- DELETE REGISTRATION ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="delete_registration", description="Remove a player from event")
async def delete_registration(interaction: discord.Interaction, member: discord.Member):
    if STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message("🚫 Only staff can remove players. Ask staff if you want to withdraw.")

    row = get_row(member.id)
    if row:
        sheet.delete_rows(row)
        await interaction.response.send_message(f"✅ {member.name} removed from event.")
    else:
        await interaction.response.send_message("⚠️ Player not registered.")

# ---- START EVENT ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="start_event", description="Start the scavenger hunt (staff only)")
async def start_event(interaction: discord.Interaction):
    global EVENT_ACTIVE, REGISTRATION_OPEN
    # staff check
    if STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message("🚫 Only staff can start the event.", ephemeral=True)

    # defer the interaction so token doesn't expire while we DM everyone
    await interaction.response.defer(ephemeral=True)

    EVENT_ACTIVE = True
    REGISTRATION_OPEN = False

    set_config("EVENT_ACTIVE", "TRUE")
    set_config("REGISTRATION_OPEN", "FALSE")

    players = sheet.get_all_records()
    undeliverable = []
    total = len(players)

    for i, player in enumerate(players, start=2):
        try:
            # choose a random first clue (non-persistent uniqueness not required across players)
            clue_index = random.randint(0, len(CLUES) - 1)
            sheet.update_cell(i, 3, "1")                 # Round = 1
            sheet.update_cell(i, 4, str(clue_index))    # UsedClues = index

            user = await bot.fetch_user(int(player["DiscordID"]))
            try:
                await user.send(f"🔎 Your first clue (Round 1):\n{CLUES[clue_index]}")
            except Exception:
                undeliverable.append(player["Name"])
        except Exception as e:
            # Log sheet/DM error but continue
            logging.exception(f"Error starting for player row {i}: {e}")

        # small sleep to avoid hammering rate limits if many users
        await asyncio.sleep(0.08)

    # send final followup after work is done
    msg = f"✅ Event started. Sent first clue to {total - len(undeliverable)} players."
    if undeliverable:
        msg += f"\n⚠️ Could not DM: {', '.join(undeliverable)} — check staff channel."
        chan = bot.get_channel(STAFF_CHANNEL_ID)
        if chan:
            await chan.send(f"⚠️ Could not DM these players their first clue: {', '.join(undeliverable)}")

    await interaction.followup.send(msg, ephemeral=True)

# ---- END EVENT ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="end_event", description="End the scavenger hunt (staff only)")
async def end_event(interaction: discord.Interaction):
    global EVENT_ACTIVE
    if STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message("🚫 Only staff can end the event.", ephemeral=True)

    EVENT_ACTIVE = False
    set_config("EVENT_ACTIVE", "FALSE")
    await interaction.response.send_message("✅ Event ended. No more submissions allowed.")

# ---- PAUSE EVENT ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="pause_event", description="Pause the scavenger hunt (staff only)")
async def pause_event(interaction: discord.Interaction):
    global EVENT_ACTIVE, EVENT_PAUSED
    if STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message("🚫 Only staff can pause the event.", ephemeral=True)

    EVENT_ACTIVE = False
    set_config("EVENT_ACTIVE", "FALSE")
    await interaction.response.send_message("⏸️ Event paused. Use /resume_event to resume it back.")

# ---- RESUME EVENT ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="resume_event", description="Resume the scavenger hunt event")
async def resume_event(interaction: discord.Interaction):
    global EVENT_ACTIVE
    if not any(role.id == STAFF_ROLE_ID for role in interaction.user.roles):
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    EVENT_ACTIVE = True
    set_config("EVENT_ACTIVE", "TRUE")
    await interaction.response.send_message("▶️ Event resumed!")

# ---- RESET EVENT ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="reset_event", description="Reset the scavenger hunt (owner only)")
async def reset_event(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("🚫 You cannot use this command.", ephemeral=True)

    clear_sheet()
    global EVENT_ACTIVE, EVENT_PAUSED, REGISTRATION_OPEN
    EVENT_ACTIVE = False
    EVENT_PAUSED = False
    REGISTRATION_OPEN = True

    await interaction.response.send_message("⚠️ Event has been reset. All data wiped.", ephemeral=True)

# ---- LEADERBOARD ----
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="leaderboard", description="Show current leaderboard")
async def leaderboard(interaction: discord.Interaction):
    players = sheet.get_all_records()
    if not players:
        return await interaction.response.send_message("No players registered yet.")

    desc = ""
    for p in players:
        desc += f"**{p['Name']}** — Round {p['Round']}\n"

    embed = discord.Embed(title="📊 Scavenger Hunt Leaderboard", description=desc, color=0x2ecc71)
    await interaction.response.send_message(embed=embed)

# ---- SUBMIT ----
class SubmissionView(discord.ui.View):
    def __init__(self, user_id: int, image_urls: list):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.image_urls = image_urls
        # These will be set by the submit handler after send:
        self.public_message_id = None
        self.staff_channel_id = None
        self.staff_message_id = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
        if staff_role and staff_role in interaction.user.roles:
            return True
        await interaction.response.send_message("⛔ You are not authorized to perform this action.", ephemeral=True)
        return False

    async def _disable_view_on_message(self, message: discord.Message):
        # remove buttons by editing message with view=None
        try:
            await message.edit(view=None)
        except Exception:
            pass

    @discord.ui.button(label="Approve ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # staff-only guard (extra)
        await interaction.response.defer(ephemeral=True)

        if STAFF_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.followup.send("⛔ Only staff can approve.", ephemeral=True)

        # get row for player
        row = get_row(self.user_id)
        if not row:
            await interaction.followup.send("⚠️ Player is not registered.")
            return

        # Read used clues
        used_raw = (sheet.cell(row, 4).value or "").strip()
        used = [int(x) for x in used_raw.split(",") if x.strip().isdigit()] if used_raw else []

        # pick remaining clue
        remaining = [i for i in range(len(CLUES)) if i not in used]
        user = await bot.fetch_user(self.user_id)

        if remaining:
            chosen = random.choice(remaining)
            used.append(chosen)
            round_num = len(used)
            # update sheet
            sheet.update_cell(row, 3, str(round_num))
            sheet.update_cell(row, 4, ",".join(map(str, used)))

            # DM the clue (URL as message so Discord embeds it)
            try:
                await user.send(f"🎉 Your submission was approved. Here is your clue (Round {round_num}):\n{CLUES[chosen]}")
            except Exception:
                # if DM fails, notify staff channel
                staff_chan = bot.get_channel(STAFF_CHANNEL_ID)
                if staff_chan:
                    await staff_chan.send(f"⚠️ Could not DM clue to {user} (Round {round_num}).")
        else:
            # no remaining -> player finished all 10
            # mark round as 10 (if not already) and send bonus
            sheet.update_cell(row, 3, str(len(CLUES)))
            sheet.update_cell(row, 4, ",".join(map(str, list(range(len(CLUES))))))
            try:
                await user.send("🎉 You have completed all 10 clues! Bonus clue unlocked:")
                await user.send(BONUS_CLUE)
            except Exception:
                staff_chan = bot.get_channel(STAFF_CHANNEL_ID)
                if staff_chan:
                    await staff_chan.send(f"⚠️ Could not DM bonus to {user}.")

        # Update / edit public message and staff message to show approval + who approved
        try:
            # public is the message where the button was clicked
            public_msg = interaction.message
            public_embed = public_msg.embeds[0] if public_msg.embeds else discord.Embed(title="Submission", description="")
            public_embed.color = discord.Color.green()
            public_embed.set_footer(text=f"✅ Approved by {interaction.user.display_name}")
            await public_msg.edit(embed=public_embed, view=None)
        except Exception:
            pass

        # edit staff message (if set)
        try:
            if self.staff_channel_id and self.staff_message_id:
                staff_chan = bot.get_channel(self.staff_channel_id)
                if staff_chan:
                    staff_msg = await staff_chan.fetch_message(self.staff_message_id)
                    staff_embed = staff_msg.embeds[0] if staff_msg.embeds else discord.Embed(title="Submission", description="")
                    staff_embed.color = discord.Color.green()
                    staff_embed.set_footer(text=f"✅ Approved by {interaction.user.display_name}")
                    await staff_msg.edit(embed=staff_embed)
        except Exception:
            pass

        await interaction.followup.send("✅ Submission approved and next clue delivered.")

    @discord.ui.button(label="Reject ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if STAFF_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.followup.send("⛔ Only staff can reject.", ephemeral=True)

        # Notify player
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send("❌ Your submission was rejected by staff. Please try again.")
        except Exception:
            staff_chan = bot.get_channel(STAFF_CHANNEL_ID)
            if staff_chan:
                await staff_chan.send(f"⚠️ Could not DM rejection notice to <@{self.user_id}>.")

        # Edit public embed to show rejection
        try:
            public_msg = interaction.message
            public_embed = public_msg.embeds[0] if public_msg.embeds else discord.Embed(title="Submission", description="")
            public_embed.color = discord.Color.red()
            public_embed.set_footer(text=f"❌ Rejected by {interaction.user.display_name}")
            await public_msg.edit(embed=public_embed, view=None)
        except Exception:
            pass

        # Edit staff message (if present)
        try:
            if self.staff_channel_id and self.staff_message_id:
                staff_chan = bot.get_channel(self.staff_channel_id)
                if staff_chan:
                    staff_msg = await staff_chan.fetch_message(self.staff_message_id)
                    staff_embed = staff_msg.embeds[0] if staff_msg.embeds else discord.Embed(title="Submission", description="")
                    staff_embed.color = discord.Color.red()
                    staff_embed.set_footer(text=f"❌ Rejected by {interaction.user.display_name}")
                    await staff_msg.edit(embed=staff_embed)
        except Exception:
            pass

        await interaction.followup.send("❌ Submission rejected.")


@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="submit", description="Submit your clue images")
@app_commands.describe(
    attack_image="Upload your attack screenshot",
    decoration_image="Upload your decoration screenshot"
)
async def submit(interaction: discord.Interaction, attack_image: discord.Attachment, decoration_image: discord.Attachment):
    # Checks
    if not EVENT_ACTIVE:
        return await interaction.response.send_message("🚫 Event not active.", ephemeral=True)
    if not is_registered(interaction.user.id):
        return await interaction.response.send_message("⚠️ You are not registered.")

    row = get_row(interaction.user.id)
    values = sheet.row_values(row)
    round_num = int(values[2])
    used_clues = values[3].split(",") if len(values) > 2 and values[3] else []
    current_clue_index = int(used_clues[-1])  # last assigned clue
    clue_url = CLUES[current_clue_index]

    # Create view with both images
    view = SubmissionView(interaction.user.id, [attack_image.url, decoration_image.url])

    # Public embed with buttons
    public_embed = discord.Embed(
        title="📤 Submission Received",
        description=f"{interaction.user.mention} has submitted their images!\n\n🔁 Waiting for staff verification...",
        color=discord.Color.gold()
    )
    public_embed.set_footer(text="Staff: click Approve or Reject below.")

    await interaction.response.send_message(embed=public_embed, view=view)
    public_msg = await interaction.original_response()
    view.public_message_id = public_msg.id

    # Staff review embed with both images
    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)
    if staff_channel:
        staff_embed = discord.Embed(
            title="🔍 New Submission (Staff Review)",
            description=f"From: {interaction.user.mention}\n\n[Jump to submission]({public_msg.jump_url})",
            color=discord.Color.blurple()
        )
        staff_embed.add_field(name="Attack Screenshot", value="⬇️", inline=False)
        staff_embed.set_image(url=attack_image.url)  # main image = attack
        staff_embed.add_field(name="Assigned Clue", value="⬇️", inline=False)
        staff_embed.set_thumbnail(url=clue_url)  # show the clue as thumbnail

        # send attack embed first
        staff_msg = await staff_channel.send(content=f"<@&{STAFF_ROLE_ID}> New submission to review", embed=staff_embed)
        view.staff_channel_id = staff_channel.id
        view.staff_message_id = staff_msg.id

        # send decoration image separately under it
        deco_embed = discord.Embed(color=discord.Color.blurple())
        deco_embed.add_field(name="Decoration Screenshot", value="⬇️", inline=False)
        deco_embed.set_image(url=decoration_image.url)
        await staff_channel.send(embed=deco_embed)

        
    else:
        logging.warning("Staff channel not found. Cannot post submission images.")


# ================== RUN ===================
bot.run(TOKEN)

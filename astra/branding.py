"""The footer every embed carries: the bot's icon and "A.S.T.R.A.".

The icon is FOOTER_ICON_URL if set, otherwise the bot's own avatar. Prefer
the avatar: Discord attachment links (media.discordapp.net/attachments/...)
carry an expiry (the ex= parameter) and stop working after a while, while
the bot's avatar URL stays valid. Set the image as the bot's avatar in the
Developer Portal and leave FOOTER_ICON_URL empty.
"""
from __future__ import annotations

import discord

BOT_NAME = "A.S.T.R.A."
_icon_url: str | None = None


def configure(icon_url: str | None) -> None:
    """Called once at startup, after login (when the bot's avatar is known)."""
    global _icon_url
    _icon_url = icon_url or None


def footer(embed: discord.Embed, extra: str | None = None) -> discord.Embed:
    """Set the standard footer, optionally with extra text: 'A.S.T.R.A. • Page 2/3'."""
    embed.set_footer(text=f"{BOT_NAME} • {extra}" if extra else BOT_NAME, icon_url=_icon_url)
    return embed


def emoji_image_url(code: str) -> str | None:
    """'<:logopr:930349746088976415>' -> that emoji's image URL (for embed author
    icons, which take a URL, not emoji text). None for Unicode emojis."""
    if code.startswith("<") and code.endswith(">") and code.count(":") == 2:
        emoji_id = code.rstrip(">").rsplit(":", 1)[1]
        ext = "gif" if code.startswith("<a:") else "png"
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
    return None

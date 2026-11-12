"""Emojis used in messages, configurable in emoji.json.

Every key below has a Unicode default. To use a custom server emoji instead,
add the same key to emoji.json with the emoji's code, e.g.
    "check": "<:tick:123456789012345678>"
(In Discord, type \\:tick: in a channel to get that code. Animated emojis
start with <a: instead of <: and don't show up without it.)
TH icons are "th<level>" keys, as before.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULTS = {
    "blank": "<:blank_space:1229767163221381173>",   # spacer used in leaderboards
    "check": "✅",
    "cross": "❌",
    "warning": "⚠️",
    "welcome": "🎉",
    "promote": "⬆️",
    "demote": "⬇️",
    "leave": "👋",
    "link": "🔗",
    "unlink": "✂️",
    "role_added": "✅",
    "role_removed": "➖",
    "member": "🔹",
    "elder": "🔸",
    "co_leader": "⚜️",
    "leader": "👑",
    "clan": "🛡️",
    "role": "⚔️",            # before "Co-Leader of Phoenix Reborn" in /profile
    "hero_bk": "👑", "hero_aq": "🏹", "hero_gw": "📖", "hero_rc": "🛡️", "hero_mp": "🦇", "hero_dd": "🐉",
}

EMOJI_FILE = Path(__file__).resolve().parent.parent / "emoji.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    try:
        return json.loads(EMOJI_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def emoji(key: str) -> str:
    return _load().get(key) or DEFAULTS.get(key, "")


def th(level: int | str | None) -> str:
    return _load().get(f"th{level}", "") if level else ""

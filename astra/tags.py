"""Clash of Clans tag helpers."""
from __future__ import annotations

import re
from urllib.parse import quote

_VALID = re.compile(r"^#[0289PYLQGRJCUV]{3,12}$")


class InvalidTag(ValueError):
    pass


def normalize_tag(raw: str) -> str:
    """'  2y0vpjuvj ' -> '#2Y0VPJUVJ'. Tags never contain the letter O, so a
    typed O is treated as zero (a common mistake when people copy tags)."""
    tag = raw.strip().upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag
    if not _VALID.match(tag):
        raise InvalidTag(f"{raw!r} is not a valid Clash of Clans tag")
    return tag


def url_tag(tag: str) -> str:
    """'#2Y0VPJUVJ' -> '%232Y0VPJUVJ' for use in a URL path."""
    return quote(normalize_tag(tag), safe="")

from astra.db.membership import Account, Clan
from astra.profile import account_block, from_database, from_official, mark_default, profile_embeds, ProfileAccount
from astra.roles.rules import Rank

PR = Clan("#28VUPJRPU", "Phoenix Reborn", "PR")
ACC = Account("#RC9PJJ0G", "Solo", 1, 42, "Solo", 17, PR.tag)

# Shaped like the official API's /players/{tag} response (trimmed).
PLAYER = {"tag": "#RC9PJJ0G", "name": "Solo", "townHallLevel": 18, "townHallWeaponLevel": 1,
          "role": "coLeader", "clan": {"tag": PR.tag, "name": "Phoenix Reborn"},
          "heroes": [{"name": "Archer Queen", "level": 110, "village": "home"},
                     {"name": "Barbarian King", "level": 110, "village": "home"},
                     {"name": "Battle Machine", "level": 35, "village": "builderBase"},
                     {"name": "Minion Prince", "level": 95, "village": "home"}]}


def test_from_official_reads_heroes_weapon_and_role():
    a = from_official(ACC, PLAYER)
    assert (a.th, a.weapon, a.role_line) == (18, 1, "Co-Leader of Phoenix Reborn")
    assert a.heroes == [("hero_bk", 110), ("hero_aq", 110), ("hero_mp", 95)]   # game order, no builder base


def test_from_database_uses_clan_membership_rank():
    a = from_database(ACC, {PR.tag: Rank.CO_LEADER}, {PR.tag: PR})
    assert (a.th, a.role_line, a.heroes) == (17, "Co-Leader of Phoenix Reborn", [])
    assert from_database(ACC, {}, {PR.tag: PR}).role_line is None


def test_default_is_highest_th_and_listed_first():
    accounts = mark_default([ProfileAccount("#A", "alt", 15), ProfileAccount("#B", "main", 18, weapon=1),
                             ProfileAccount("#C", "mid", 18)])
    assert [a.tag for a in accounts] == ["#B", "#C", "#A"] and accounts[0].default


def test_block_layout():
    a = from_official(ACC, PLAYER)
    a.default = True
    lines = account_block(a).splitlines()
    assert "18¹ • [Solo (#RC9PJJ0G)](https://link.clashofclans.com/en?action=OpenPlayerProfile&playerTag=%23RC9PJJ0G)" in lines[0]
    assert lines[0].endswith("**(Default)**")
    assert "110" in lines[1] and "95" in lines[1]
    assert lines[2].endswith("Co-Leader of Phoenix Reborn")


def test_embed_header_and_split():
    accounts = mark_default([from_official(ACC, PLAYER) for _ in range(40)])
    embeds = profile_embeds("Solo 🍍", 42, "pinesolo", None, accounts)
    assert embeds[0].author.name == "Solo 🍍 (42)" and "**Username**\npinesolo" in embeds[0].description
    assert "Player Accounts (40)" in embeds[0].description
    assert len(embeds) > 1 and all(len(e.description) <= 4096 for e in embeds)

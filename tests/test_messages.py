from astra.db.membership import Account, Clan, ClanPerson
from astra.jobs.roster import InGameMember
from astra.leaders import Result
from astra.messages import clan_linked_embeds, clan_members_embeds, render_result
from astra.roles.rules import Rank

PR = Clan("#28VUPJRPU", "Phoenix Reborn", "PR")


def test_joined_is_a_checklist_ending_in_a_welcome():
    r = Result("x", kind="joined", target_id=5, clan=PR, rank=Rank.MEMBER, account=("#P2P2P2P2", "Newbie"))
    text, embed = render_result(r, added=["PR Member", "[ArIX] Member"], notes=["heads up"])
    assert embed is None
    lines = text.splitlines()
    assert lines[0].endswith("Linked **Newbie** (`#P2P2P2P2`).")
    assert "Added **PR Member** role." in lines[1] and "Added **[ArIX] Member** role." in lines[2]
    assert "heads up" in lines[3]
    assert lines[-1].endswith("Welcome to <:logopr:930349746088976415> **Phoenix Reborn**, <@5>!")


def test_promotion_is_an_embed():
    r = Result("x", kind="promoted", target_id=5, clan=PR, rank=Rank.ELDER)
    text, embed = render_result(r, added=["PR Elder"], actor="Harsh")
    assert text is None and "Promotion" in embed.title
    assert "<@5> is now **Elder** in <:logopr:930349746088976415> **Phoenix Reborn**." in embed.description
    assert "Added **PR Elder** role." in embed.description and embed.footer.text == "A.S.T.R.A. • Promoted by Harsh"
    _, demo = render_result(Result("x", kind="demoted", target_id=5, clan=PR, rank=Rank.MEMBER), removed=["PR Elder"])
    assert "Demotion" in demo.title and "Removed **PR Elder** role." in demo.description


def test_clan_members_lists_unlinked_first():
    ingame = [InGameMember("#AAAAAAA0".replace("A", "P"), "Linked", 17, PR.tag),
              InGameMember("#QQQQQQQ0", "Loose", 18, PR.tag)]
    accounts = {"#PPPPPPP0": Account("#PPPPPPP0", "Linked", 1, 42, "Owner", 17, PR.tag)}
    [embed] = clan_members_embeds(PR, ingame, accounts)
    d = embed.description
    assert d.index("Not linked · 1") < d.index("Loose") < d.index("Linked · 1") < d.index("<@42>")


def test_clan_linked_flags_people_not_in_clan():
    here = Account("#PPPPPPP0", "Main", 1, 42, "A", 17, PR.tag)
    gone = Account("#QQQQQQQ0", "Old", 2, 43, "B", 16, None)
    people = [ClanPerson(1, "A", 42, Rank.ELDER, [here]), ClanPerson(2, "B", 43, Rank.MEMBER, [gone])]
    [embed] = clan_linked_embeds(PR, people, in_clan={"#PPPPPPP0"})
    d = embed.description
    assert d.index("Not in PR in-game · 1") < d.index("<@43>") < d.index("In the clan · 1 accounts") < d.index("<@42>")
    assert "`Eld   #PPPPPPP0 `  Main  <@42>" in d                 # ROLE / TAG / NAME table
    assert embed.author.name == "Phoenix Reborn (#28VUPJRPU)"
    assert embed.footer.text.startswith("A.S.T.R.A.")


def test_long_lists_split_under_discord_limit():
    ingame = [InGameMember(f"#Q{i:08d}".replace("1", "2").replace("3", "8").replace("4", "9").replace("5", "0")
                           .replace("6", "2").replace("7", "8"), "N" * 30, 15, PR.tag) for i in range(150)]
    embeds = clan_members_embeds(PR, ingame, {})
    assert len(embeds) > 1 and all(len(e.description) <= 4096 for e in embeds)
    assert embeds[0].author.name and embeds[1].author.name is None
    assert embeds[-1].footer.text.endswith(f"Page {len(embeds)}/{len(embeds)}")


def test_emoji_image_url_and_clan_logo_in_welcome():
    from astra.branding import emoji_image_url
    assert emoji_image_url("<:logopr:930349746088976415>") == "https://cdn.discordapp.com/emojis/930349746088976415.png"
    assert emoji_image_url("<a:spin:1>") == "https://cdn.discordapp.com/emojis/1.gif"
    assert emoji_image_url("✅") is None
    r = Result("x", kind="joined", target_id=5, clan=PR, rank=Rank.MEMBER, account=("#P2P2P2P2", "N"))
    text, _ = render_result(r)
    assert "Welcome to <:logopr:930349746088976415> **Phoenix Reborn**" in text

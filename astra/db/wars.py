"""Wars, attacks, and the war stats leaderboard query."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

# Leaderboard rules, carried over from the old Apps Script (Code.gs):
MIN_ATTACKER_TH = 13   # attacks by TH12 and below aren't counted
# ...and "dips" (hitting a lower TH than your own) aren't counted either.


@dataclass(frozen=True)
class WarRecord:
    clan_tag: str
    war_type: str              # regular | cwl | friendly
    cwl_war_tag: str | None
    opponent_tag: str
    opponent_name: str | None
    team_size: int | None
    state: str
    preparation_start: datetime
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class AttackRecord:
    attack_order: int
    attacker_tag: str
    attacker_name: str | None
    attacker_th: int
    defender_tag: str
    defender_th: int | None
    stars: int
    destruction: float
    duration_s: int | None


@dataclass(frozen=True)
class WarStatsEntry:
    tag: str
    name: str
    th: int
    attacks: int
    triples: int
    hit_rate: int        # % of counted attacks that were 3 stars, rounded down
    destruction: float   # sum of destruction % over counted attacks


class WarsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_war(self, war: WarRecord, attacks: list[AttackRecord]) -> int:
        """Insert or update one war and its attacks in a single transaction.
        Re-fetching an in-progress war just adds the new attacks."""
        async with self._pool.acquire() as conn, conn.transaction():
            war_id = await conn.fetchval(
                """INSERT INTO wars (clan_tag, war_type, cwl_war_tag, opponent_tag, opponent_name,
                                     team_size, state, preparation_start, start_time, end_time)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (clan_tag, preparation_start) DO UPDATE
                   SET state = EXCLUDED.state, war_type = EXCLUDED.war_type,
                       cwl_war_tag = EXCLUDED.cwl_war_tag, end_time = EXCLUDED.end_time,
                       opponent_name = EXCLUDED.opponent_name, fetched_at = now()
                   RETURNING id""",
                war.clan_tag, war.war_type, war.cwl_war_tag, war.opponent_tag, war.opponent_name,
                war.team_size, war.state, war.preparation_start, war.start_time, war.end_time,
            )
            if attacks:
                await conn.executemany(
                    """INSERT INTO war_attacks (war_id, attack_order, attacker_tag, attacker_name,
                                                attacker_th, defender_tag, defender_th, stars,
                                                destruction, duration_s)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                       ON CONFLICT (war_id, attack_order) DO UPDATE
                       SET stars = EXCLUDED.stars, destruction = EXCLUDED.destruction,
                           defender_th = EXCLUDED.defender_th""",
                    [(war_id, a.attack_order, a.attacker_tag, a.attacker_name, a.attacker_th,
                      a.defender_tag, a.defender_th, a.stars, a.destruction, a.duration_s)
                     for a in attacks],
                )
            return war_id

    async def war_stats(self, start: datetime, end: datetime) -> list[WarStatsEntry]:
        """Regular-war leaderboard for wars whose battle day starts in [start, end).

        Per linked account: attacks, 3-stars, hit rate and total destruction,
        counting only attacks by TH13+ on an equal or higher TH. TH is the
        account's TH in its most recent war. Sorted like the old sheet:
        TH, then 3-stars, hit rate, destruction (all descending).
        """
        rows = await self._pool.fetch(
            """WITH counted AS (
                   SELECT a.attacker_tag, a.attacker_th, a.stars, a.destruction, w.start_time,
                          (a.attacker_th >= $3
                           AND (a.defender_th IS NULL OR a.attacker_th <= a.defender_th)) AS counts
                   FROM war_attacks a
                   JOIN wars w ON w.id = a.war_id
                   WHERE w.war_type = 'regular' AND w.start_time >= $1 AND w.start_time < $2
               ),
               per_account AS (
                   SELECT attacker_tag AS tag,
                          (array_agg(attacker_th ORDER BY start_time DESC))[1] AS th,
                          count(*) FILTER (WHERE counts)                         AS attacks,
                          count(*) FILTER (WHERE counts AND stars = 3)           AS triples,
                          coalesce(sum(destruction) FILTER (WHERE counts), 0)    AS destruction
                   FROM counted
                   GROUP BY attacker_tag
               )
               SELECT p.tag, acc.name, p.th, p.attacks::int, p.triples::int,
                      (100 * p.triples / p.attacks)::int AS hit_rate,  -- integer division = floor
                      p.destruction::float8 AS destruction
               FROM per_account p
               JOIN accounts acc ON acc.tag = p.tag AND acc.member_id IS NOT NULL
               WHERE p.attacks > 0
               ORDER BY p.th DESC, p.triples DESC, hit_rate DESC, p.destruction DESC, acc.name""",
            start, end, MIN_ATTACKER_TH,
        )
        return [WarStatsEntry(r["tag"], r["name"], r["th"], r["attacks"], r["triples"],
                              r["hit_rate"], r["destruction"]) for r in rows]

    async def cwl_stats(self, start: datetime, end: datetime) -> list["CwlEntry"]:
        """CWL leaderboard for the CWL that starts in [start, end)."""
        return await cwl_stats(self._pool, start, end)


# --- CWL ---------------------------------------------------------------------

CWL_DAYS = 7


@dataclass(frozen=True)
class CwlAttack:
    tag: str
    name: str
    owner: str
    clan: str
    day: int
    attacker_th: int
    defender_th: int | None
    stars: int
    destruction: float


@dataclass
class CwlEntry:
    tag: str
    name: str
    owner: str
    clan: str
    th: int
    days: dict[int, tuple[int, float]]   # day -> (stars, destruction %)
    dips: int = 0                        # attacks on a lower TH
    reaches: int = 0                     # attacks on a higher TH

    @property
    def hits(self) -> int:
        return len(self.days)

    @property
    def triples(self) -> int:
        return sum(1 for stars, _ in self.days.values() if stars == 3)

    @property
    def stars(self) -> int:
        return sum(stars for stars, _ in self.days.values())


def build_cwl_entries(attacks: list[CwlAttack]) -> list[CwlEntry]:
    """Group attacks by account. Each attack goes under its round's day, so a
    missed Day 2 stays empty instead of shifting Day 3 into its place (the
    bug in the old script, which filled days in the order attacks came in).
    Sorted like the old sheet: TH, then triples, then stars (descending)."""
    entries: dict[str, CwlEntry] = {}
    for a in sorted(attacks, key=lambda a: (a.tag, a.day)):
        if not 1 <= a.day <= CWL_DAYS:
            continue
        e = entries.get(a.tag)
        if e is None:
            e = entries[a.tag] = CwlEntry(a.tag, a.name, a.owner, a.clan, a.attacker_th, {})
        e.th = a.attacker_th                  # latest round wins (sorted by day)
        if a.day in e.days:
            continue                          # one attack per round in CWL
        e.days[a.day] = (a.stars, a.destruction)
        if a.defender_th is not None:
            if a.attacker_th > a.defender_th:
                e.dips += 1
            elif a.attacker_th < a.defender_th:
                e.reaches += 1
    return sorted(entries.values(), key=lambda e: (-e.th, -e.triples, -e.stars, e.name.lower()))


async def _cwl_attacks(pool: asyncpg.Pool, start: datetime, end: datetime) -> list[CwlAttack]:
    """Our linked accounts' attacks in CWL rounds that start in [start, end).
    Day = whole days since the clan's first round that month, plus one, so a
    round missing from the API can't shift later days either."""
    rows = await pool.fetch(
        """WITH rounds AS (
               SELECT w.id, c.name AS clan,
                      1 + round(extract(epoch FROM w.start_time
                                - min(w.start_time) OVER (PARTITION BY w.clan_tag)) / 86400)::int AS day
               FROM wars w
               JOIN clans c ON c.tag = w.clan_tag
               WHERE w.war_type = 'cwl' AND w.start_time >= $1 AND w.start_time < $2
           )
           SELECT a.attacker_tag AS tag, acc.name, m.display_name AS owner, r.clan, r.day,
                  a.attacker_th, a.defender_th, a.stars, a.destruction
           FROM war_attacks a
           JOIN rounds r     ON r.id = a.war_id
           JOIN accounts acc ON acc.tag = a.attacker_tag
           JOIN members m    ON m.id = acc.member_id""",
        start, end,
    )
    return [CwlAttack(r["tag"], r["name"], r["owner"], r["clan"], r["day"], r["attacker_th"],
                      r["defender_th"], r["stars"], float(r["destruction"])) for r in rows]


async def cwl_stats(pool: asyncpg.Pool, start: datetime, end: datetime) -> list[CwlEntry]:
    return build_cwl_entries(await _cwl_attacks(pool, start, end))

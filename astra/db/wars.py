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

"""Wars job: ClashKing -> Postgres, for every tracked clan.

Source: GET /v2/clan/{tag}/wars?type=random (regular wars) or type=cwl.
Each item is a full war in the official API's shape (clan and opponent,
members, attacks). Defender TH isn't on the attack itself, so it is looked
up from the opponent's member list.

The old Apps Script only counted wars from the 9th of the month on, as a
way to skip CWL. Asking for type=random excludes CWL directly, so the whole
calendar month counts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.db.wars import AttackRecord, WarRecord, WarsRepository
from astra.jobs.donations import JobResult, JsonClient
from astra.months import month_start
from astra.tags import normalize_tag, url_tag

log = logging.getLogger(__name__)

JOB_NAME = "wars"
WARS_PER_REQUEST = 100        # a clan fits ~15 regular wars in a month
LOOKBACK = timedelta(days=3)  # also fetch wars that started prep just before the month


def parse_coc_time(value: str) -> datetime:
    """'20261107T195040.000Z' (official API format) -> aware datetime."""
    return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_war(war: dict[str, Any], clan_tag: str, war_type: str) -> tuple[WarRecord, list[AttackRecord]]:
    """Turn one war from the API into a WarRecord plus our side's attacks.
    `clan_tag` says which side is ours (ClashKing usually puts it in 'clan',
    but we don't rely on that)."""
    sides = (war.get("clan") or {}, war.get("opponent") or {})
    ours, theirs = sides if sides[0].get("tag") == clan_tag else sides[::-1]
    if ours.get("tag") != clan_tag:
        raise ValueError(f"war has no side with tag {clan_tag}")

    defender_th = {m["tag"]: m.get("townhallLevel") for m in theirs.get("members") or []}
    attacks = []
    for member in ours.get("members") or []:
        for a in member.get("attacks") or []:
            attacks.append(AttackRecord(
                attack_order=int(a["order"]),
                attacker_tag=normalize_tag(member["tag"]),
                attacker_name=member.get("name"),
                attacker_th=int(member["townhallLevel"]),
                defender_tag=normalize_tag(a["defenderTag"]),
                defender_th=defender_th.get(a["defenderTag"]),
                stars=int(a["stars"]),
                destruction=float(a["destructionPercentage"]),
                duration_s=a.get("duration"),
            ))

    record = WarRecord(
        clan_tag=clan_tag,
        war_type=war_type,
        cwl_war_tag=war.get("tag") if war_type == "cwl" else None,
        opponent_tag=theirs.get("tag") or "?",
        opponent_name=theirs.get("name"),
        team_size=war.get("teamSize"),
        state=war.get("state") or "unknown",
        preparation_start=parse_coc_time(war["preparationStartTime"]),
        start_time=parse_coc_time(war["startTime"]),
        end_time=parse_coc_time(war["endTime"]),
    )
    return record, attacks


async def fetch_clan_wars(client: JsonClient, clan_tag: str, war_type: str, after: datetime) -> list[dict]:
    api_type = {"regular": "random", "cwl": "cwl"}[war_type]
    data = await client.get_json(
        f"/v2/clan/{url_tag(clan_tag)}/wars",
        params={"type": api_type, "time[after]": _iso(after), "limit": WARS_PER_REQUEST},
    )
    items = (data or {}).get("items") or []
    if len(items) >= WARS_PER_REQUEST:
        log.warning("%s: %d %s wars returned; some may be missing", clan_tag, len(items), war_type)
    return items


async def run_wars_job(
    client: JsonClient,
    roster: RosterRepository,
    wars: WarsRepository,
    jobs: JobRunsRepository,
    *,
    war_type: str = "regular",
    now: datetime | None = None,
) -> JobResult:
    job_name = f"{JOB_NAME}:{war_type}"
    async with jobs.exclusive(job_name) as got_lock:
        if not got_lock:
            return JobResult("skipped")
        now = now or datetime.now(timezone.utc)
        clan_tags = sorted(await roster.list_tracked_clan_tags())
        if not clan_tags:
            return JobResult("skipped")
        run_id = await jobs.start(job_name)

        results = await asyncio.gather(
            *(fetch_clan_wars(client, t, war_type, month_start(now) - LOOKBACK) for t in clan_tags),
            return_exceptions=True,
        )
        ok = failed = 0
        errors: list[str] = []
        for clan_tag, result in zip(clan_tags, results):
            if isinstance(result, BaseException):
                failed += 1
                errors.append(f"{clan_tag}: {result}")
                continue
            for war in result:
                try:
                    record, attacks = parse_war(war, clan_tag, war_type)
                    await wars.upsert_war(record, attacks)
                    ok += 1
                except Exception as e:   # one malformed war shouldn't drop the others
                    failed += 1
                    errors.append(f"{clan_tag} war {war.get('preparationStartTime')}: {e!r}")

        status = "ok" if not failed else ("partial" if ok else "failed")
        await jobs.finish(run_id, status, items_ok=ok, items_failed=failed,
                          error="; ".join(errors[:5])[:500] or None)
        log.info("%s job %s: %d wars saved, %d failed", job_name, status, ok, failed)
        return JobResult(status, ok, failed)

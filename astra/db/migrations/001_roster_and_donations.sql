-- 001: roster (clans, people, accounts, memberships, roles), audit log,
-- donations, and job run tracking.
--
-- Design notes:
-- * Store raw facts, derive leaderboards with queries. Nothing here is a
--   pre-computed "leaderboard table" like the old sheets.
-- * A member is a person; an account is one Clash of Clans player tag.
--   One person can own several accounts (the "singular name" column in the
--   old Player Tags sheet).
-- * Rank lives on the membership (person + clan), because /promote acts on a
--   person in a clan, and the same person can be Elder in PR and Member in DOP.

-- Clash of Clans tags: '#' followed by characters from this alphabet.
CREATE DOMAIN coc_tag AS TEXT CHECK (VALUE ~ '^#[0289PYLQGRJCUV]{3,12}$');

CREATE TYPE clan_rank AS ENUM ('member', 'elder', 'co_leader', 'leader');

CREATE TABLE clans (
    tag         coc_tag PRIMARY KEY,
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL UNIQUE,      -- 'PR', 'DOP': used in role names and commands
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE members (
    id               BIGSERIAL PRIMARY KEY,
    display_name     TEXT NOT NULL,
    discord_user_id  BIGINT UNIQUE,        -- NULL until linked by a leader
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    tag              coc_tag PRIMARY KEY,
    member_id        BIGINT REFERENCES members(id) ON DELETE SET NULL,
    name             TEXT NOT NULL,
    th_level         SMALLINT CHECK (th_level BETWEEN 1 AND 30),
    ingame_clan_tag  TEXT,                 -- what the game says; any clan, so no FK
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX accounts_member_id_idx ON accounts (member_id);

-- Leader-approved membership: current state only. History is in membership_events.
CREATE TABLE clan_memberships (
    member_id    BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    clan_tag     coc_tag NOT NULL REFERENCES clans(tag),
    rank         clan_rank NOT NULL DEFAULT 'member',
    approved_by  BIGINT NOT NULL,          -- Discord user id of the leader who ran /joined
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (member_id, clan_tag)
);
CREATE INDEX clan_memberships_clan_idx ON clan_memberships (clan_tag);

-- Which Discord role each (clan, rank) maps to. Every clan's 'leader' row
-- points at the same shared Leader role id.
CREATE TABLE clan_roles (
    clan_tag         coc_tag NOT NULL REFERENCES clans(tag) ON DELETE CASCADE,
    rank             clan_rank NOT NULL,
    discord_role_id  BIGINT NOT NULL,
    PRIMARY KEY (clan_tag, rank)
);

-- Append-only audit log: who changed what, and when.
CREATE TABLE membership_events (
    id                BIGSERIAL PRIMARY KEY,
    member_id         BIGINT REFERENCES members(id) ON DELETE SET NULL,
    clan_tag          TEXT,
    account_tag       TEXT,
    action            TEXT NOT NULL CHECK (action IN
                        ('joined', 'left', 'promoted', 'demoted', 'linked', 'unlinked')),
    from_rank         clan_rank,
    to_rank           clan_rank,
    actor_discord_id  BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX membership_events_member_idx ON membership_events (member_id, created_at DESC);

-- One row per account per season. The fetcher upserts, so re-running a
-- fetch overwrites the season's numbers instead of duplicating them.
CREATE TABLE donation_snapshots (
    account_tag  coc_tag NOT NULL REFERENCES accounts(tag) ON DELETE CASCADE,
    season       TEXT NOT NULL CHECK (season ~ '^\d{4}-\d{2}$'),   -- 'YYYY-MM'
    donated      INTEGER NOT NULL CHECK (donated >= 0),
    received     INTEGER NOT NULL CHECK (received >= 0),
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_tag, season)
);
CREATE INDEX donation_snapshots_season_idx ON donation_snapshots (season);

-- Every scheduled job run, for "when did data last refresh?" and debugging.
CREATE TABLE job_runs (
    id            BIGSERIAL PRIMARY KEY,
    job           TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    items_ok      INTEGER NOT NULL DEFAULT 0,
    items_failed  INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);
CREATE INDEX job_runs_job_idx ON job_runs (job, started_at DESC);

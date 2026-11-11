-- 003: wars and attacks (raw facts). Leaderboards are queries over these.
-- Regular wars and CWL rounds share these tables, told apart by war_type.

CREATE TYPE war_type AS ENUM ('regular', 'cwl', 'friendly');

CREATE TABLE wars (
    id                 BIGSERIAL PRIMARY KEY,
    clan_tag           coc_tag NOT NULL REFERENCES clans(tag),
    war_type           war_type NOT NULL,
    cwl_war_tag        TEXT,                  -- set for CWL rounds only
    opponent_tag       TEXT NOT NULL,
    opponent_name      TEXT,
    team_size          SMALLINT,
    state              TEXT NOT NULL,         -- preparation | inWar | warEnded
    preparation_start  TIMESTAMPTZ NOT NULL,
    start_time         TIMESTAMPTZ NOT NULL,  -- battle day start; decides the war's month
    end_time           TIMESTAMPTZ NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A clan can't be in two wars that start preparation at the same moment,
    -- so this identifies a war and makes re-fetching an upsert.
    UNIQUE (clan_tag, preparation_start)
);
CREATE INDEX wars_type_start_idx ON wars (war_type, start_time);

-- Only attacks made by our clan's side are stored.
CREATE TABLE war_attacks (
    war_id        BIGINT NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    attack_order  INTEGER NOT NULL,           -- unique within a war
    attacker_tag  coc_tag NOT NULL,
    attacker_name TEXT,
    attacker_th   SMALLINT NOT NULL,
    defender_tag  coc_tag NOT NULL,
    defender_th   SMALLINT,                   -- NULL if the defender isn't in the war data
    stars         SMALLINT NOT NULL CHECK (stars BETWEEN 0 AND 3),
    destruction   REAL NOT NULL CHECK (destruction BETWEEN 0 AND 100),
    duration_s    INTEGER,
    PRIMARY KEY (war_id, attack_order)
);
CREATE INDEX war_attacks_attacker_idx ON war_attacks (attacker_tag);

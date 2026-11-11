-- 004: bot-wide settings (the common family role), and donations per clan.

-- Small key/value store for bot-wide settings, e.g. 'common_role_id': the
-- role everyone in any tracked clan gets (like "[ArIX] Member").
CREATE TABLE bot_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Donations were stored per account per month, summed over all tracked
-- clans. Store them per clan too, so leaderboards can be filtered by clan.
-- The existing rows can't be split after the fact; they're rebuilt by the
-- next donations run (ClashKing still has the history), so drop them.
DELETE FROM donation_snapshots;
ALTER TABLE donation_snapshots ADD COLUMN clan_tag coc_tag NOT NULL REFERENCES clans(tag) ON DELETE CASCADE;
ALTER TABLE donation_snapshots DROP CONSTRAINT donation_snapshots_pkey;
ALTER TABLE donation_snapshots ADD PRIMARY KEY (account_tag, season, clan_tag);

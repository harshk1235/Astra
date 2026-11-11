-- 002: support importing the existing members sheet.
--
-- Never edit a migration that may already have run somewhere; add a new one.

-- Memberships imported from the sheet weren't approved through /joined, so
-- there is no approving leader to record. NULL means "imported".
ALTER TABLE clan_memberships ALTER COLUMN approved_by DROP NOT NULL;

-- Whether the account is verified (via the in-game API token), as recorded in
-- the members sheet. Leader-approved links start unverified; token
-- verification can set this later.
ALTER TABLE accounts ADD COLUMN verified BOOLEAN NOT NULL DEFAULT FALSE;

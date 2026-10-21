-- Conversations.
--
-- **The first table with a row-level policy**, and the reason the policy
-- machinery was built before any table needed it. This is user-owned data in the
-- plain sense: a conversation belongs to the person who had it, and until now
-- that was guaranteed by which directory the file sat in (ADR 0003).
--
-- The index columns are the point of the table. Booting used to parse every
-- conversation's JSON to learn its name and timestamps; those are columns now,
-- so listing is a query over a few hundred bytes a row rather than a parse of a
-- quarter of a megabyte.
--
-- The conversation itself stays a document. Normalising messages into rows would
-- make search and retention queries rather than scans -- but they already are:
-- search is a JSONB query and retention is a DELETE on last_message. What
-- normalising would additionally buy is partial loading of one conversation,
-- which nothing asks for: the unit people open, edit and branch is the whole
-- conversation. The cost would be teaching the agent's history format to be a
-- schema, and that format changes far more often than this table should.

CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,
    -- NOT NULL, unlike scheduled_tasks. A conversation with no owner is
    -- unreachable under the policy below, so one would be a leak of storage
    -- rather than of data -- but it would also be invisible and undeletable,
    -- which is worse than refusing to write it.
    owner_id      TEXT NOT NULL,
    name          TEXT,
    type          TEXT NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ NOT NULL,
    last_message  TIMESTAMPTZ NOT NULL,
    agent_profile TEXT NOT NULL DEFAULT '',
    log_guid      TEXT NOT NULL DEFAULT '',
    log_length    INTEGER NOT NULL DEFAULT 0,
    output_data   JSONB NOT NULL DEFAULT '{}'::jsonb,
    document      JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- FORCE, so the table's owner is subject too. Without it the migration role
-- would be exempt, and anything that reached the database as the owner by
-- accident would see everybody's conversations.
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;

CREATE POLICY conversations_owner ON conversations
    USING (owner_id = app_user())
    WITH CHECK (owner_id = app_user());

-- The sidebar's query: one user's conversations, newest first.
CREATE INDEX conversations_owner_recent ON conversations (owner_id, last_message DESC);

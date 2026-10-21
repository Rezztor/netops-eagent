-- Agent memory, as vectors.
--
-- This removes the last thing in the original measurement that grew without
-- limit. `Memory.index` was a plain dict of fully-resident FAISS indexes with no
-- cap, no LRU and no TTL: N active users meant N indexes in RAM permanently,
-- each carrying every document's text. A query goes to the database now, so
-- there is nothing to hold.
--
-- **Scope is a column, not an inference.** Memory has two kinds and they are
-- genuinely different: a person's own memories, and a project's, which everyone
-- with access to the project shares -- that was a deliberate decision when
-- memory was files and it has not changed.
--
-- Writing that as "owner_id IS NULL means shared" would make a bug that forgot
-- to set an owner into a silent leak of somebody's private memories. So the
-- scope is stated, and the CHECK makes the unsafe combination impossible to
-- store rather than merely unlikely: a 'user' row without an owner is rejected.
--
-- What stays a file: the knowledge the memory was built from. This table is a
-- derived index -- it is rebuilt from those files whenever the embedding model
-- changes -- so a project directory remains portable without it.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memories (
    id            TEXT PRIMARY KEY,
    scope         TEXT NOT NULL CHECK (scope IN ('user', 'project')),
    owner_id      TEXT,
    -- Which index this belongs to: a user's main memory, a named project's, an
    -- agent profile's. Carried through from the directory name it used to be.
    area          TEXT NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- No dimension. Embedding models disagree -- 768, 1536, 3072 -- and pinning
    -- one here would make changing models a migration. Exact search needs no
    -- index; when volumes justify an approximate one, it can be added per
    -- deployment against whatever dimension that deployment actually uses.
    embedding     vector NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT memories_user_rows_have_an_owner
        CHECK (scope <> 'user' OR owner_id IS NOT NULL)
);

ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;

-- A project's memories are readable by anyone signed in, which is what they
-- were when they lived in a shared directory. A person's are readable by that
-- person. There is no third case, and `scope` is what tells them apart.
CREATE POLICY memories_scope ON memories
    USING (scope = 'project' OR owner_id = app_user())
    WITH CHECK (scope = 'project' OR owner_id = app_user());

CREATE INDEX memories_area ON memories (area);
CREATE INDEX memories_owner_area ON memories (owner_id, area);

-- A project's memories are shared to read, and each one is changed only by
-- whoever wrote it.
--
-- 0006 gave project memory one policy for reading and writing alike:
-- `scope = 'project'` let any signed-in account update or delete any project
-- row. Automatic memorisation consolidates as it goes -- a new fragment close
-- enough to an old one replaces it -- so text one person's conversation put in
-- front of the model could overwrite what a colleague's agent had learned, and
-- the colleague's recall would then repeat it. Reading stays as ADR 0034 has it:
-- everyone with access to the project shares what it remembers.
--
-- `written_by` defaults to the scope the row is written under, and the insert
-- policy refuses any other value, so the writer cannot be named by the caller.
-- A person's own rows are theirs whoever is recorded: `owner_id` stays the
-- authority there, so the backfill below is for the record and nothing depends
-- on it having reached every row.
--
-- A project row written before this migration has no writer. Nobody can change
-- or delete it through the application role; it stays readable. Re-embedding
-- and replacing knowledge imported from files are maintenance, not anyone's
-- edit, and go through the owner role (plugins/_memory/helpers/pgvector_store.py).

ALTER TABLE memories ADD COLUMN written_by TEXT DEFAULT app_user();

UPDATE memories SET written_by = owner_id WHERE scope = 'user';

DROP POLICY memories_scope ON memories;

CREATE POLICY memories_read ON memories FOR SELECT
    USING (scope = 'project' OR owner_id = app_user());

CREATE POLICY memories_write ON memories FOR INSERT
    WITH CHECK (written_by = app_user() AND (scope = 'project' OR owner_id = app_user()));

CREATE POLICY memories_change ON memories FOR UPDATE
    USING (owner_id = app_user() OR (scope = 'project' AND written_by = app_user()))
    WITH CHECK (owner_id = app_user() OR (scope = 'project' AND written_by = app_user()));

CREATE POLICY memories_remove ON memories FOR DELETE
    USING (owner_id = app_user() OR (scope = 'project' AND written_by = app_user()));

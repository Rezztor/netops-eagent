-- A memory's writer, scope, area and owner are fixed once it is written.
--
-- 0025's UPDATE policy decides who may change a row, but a WITH CHECK sees only
-- the new row: the owner of a private memory could rewrite `written_by` to a
-- colleague's name, or move the row into a project's area as `scope = 'project'`,
-- and the colleague's consolidation would then treat it as theirs to merge.
-- Nothing in the application changes these columns after the insert -- updates
-- set content, metadata and embedding -- so the rule is that they never change,
-- for every role. A future migration that needs to move rows can disable the
-- trigger inside its own transaction.

CREATE FUNCTION memories_keep_identity() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.written_by IS DISTINCT FROM OLD.written_by
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.area IS DISTINCT FROM OLD.area
       OR NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
        RAISE EXCEPTION 'a memory''s writer, scope, area and owner cannot be changed'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER memories_keep_identity BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_keep_identity();

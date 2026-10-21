-- kvp holds two different things, and could not tell them apart.
--
-- It was created as "generic persistent key/value" -- the file-per-key store
-- with a LIKE instead of a glob -- and for instance-wide values that is exactly
-- right. `memory_embedding_default` names the model this deployment embeds with;
-- there is one, and everyone uses it.
--
-- Then per-user state moved in beside it. `plugin_pin_to_top` is one row holding
-- every account's pinned chats and tasks, so `get_pins()` returned other
-- people's pins to whoever asked and `toggle_pin` could unpin someone else's.
-- `browser_open_tabs` is the same shape, holding the URL each user's agent has
-- open. Neither is dramatic on its own; together they are chat ids, task ids and
-- browsing history crossing accounts through a table with no owner.
--
-- It was also the only table in this schema with neither a policy nor a
-- paragraph saying why it did not need one. `users`, `password_resets`,
-- `scheduled_tasks` and `runner_slots` each explain their absence and the
-- explanations are convincing; `settings` is a single row by construction. This
-- one was an oversight wearing the same clothes, which is why it survived.
--
-- **Scope is stated, not inferred**, exactly as ADR 0034 and `memories` have it.
-- Writing "owner_id IS NULL means instance-wide" would turn a caller that forgot
-- to set an owner into a silent leak; the CHECK makes that row impossible to
-- store instead of merely unlikely.

ALTER TABLE kvp ADD COLUMN scope TEXT NOT NULL DEFAULT 'instance';
ALTER TABLE kvp ADD COLUMN owner_id TEXT;

ALTER TABLE kvp ADD CONSTRAINT kvp_scope_is_known
    CHECK (scope IN ('instance', 'user'));
ALTER TABLE kvp ADD CONSTRAINT kvp_user_rows_have_an_owner
    CHECK (scope <> 'user' OR owner_id IS NOT NULL);

-- The key alone is no longer unique: two accounts each hold their own
-- `plugin_pin_to_top`. NULLS NOT DISTINCT so the instance-wide rows, whose
-- owner_id is NULL, still collide with each other the way a primary key did --
-- without it every write of a global value would insert a new row instead of
-- updating the one that is there.
ALTER TABLE kvp DROP CONSTRAINT kvp_pkey;
CREATE UNIQUE INDEX kvp_key_owner ON kvp (key, owner_id) NULLS NOT DISTINCT;

-- The mixed rows cannot be split. `plugin_pin_to_top` holds every account's
-- pins under one key with nothing recording whose each was, so there is no
-- attribution to recover -- and keeping the row would mean keeping the leak,
-- since an instance-scoped row stays readable by everyone under the policy
-- below. Both are cosmetic interface state that the next click rebuilds.
DELETE FROM kvp WHERE key IN ('plugin_pin_to_top', 'browser_open_tabs');

ALTER TABLE kvp ENABLE ROW LEVEL SECURITY;
ALTER TABLE kvp FORCE ROW LEVEL SECURITY;

-- An instance value is readable by anyone signed in, which is what it was when
-- it was a file in a shared directory. A user's is readable by that user, and
-- by nobody without a scope: app_user() is NULL then and `owner_id = NULL` is
-- never true.
CREATE POLICY kvp_scope ON kvp
    USING (scope = 'instance' OR owner_id = app_user())
    WITH CHECK (scope = 'instance' OR owner_id = app_user());

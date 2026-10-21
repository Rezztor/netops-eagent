-- Foundations: the helper every user-owned table's policy calls, and the grants
-- the application role needs.
--
-- `schema_migrations` itself is created by the runner before this file, because
-- it has to exist to record that this file ran.

-- The current request's user, or NULL when nothing set one.
--
-- NULL is the load-bearing case. A policy comparing `owner_id = app_user()`
-- against NULL matches no row, so a query that reaches the database without a
-- scope returns nothing rather than everything. ADR 0003's directory layout
-- failed safe the same way; this is the replacement for it, not an addition.
--
-- STABLE, not IMMUTABLE: it changes between transactions, and marking it
-- immutable would let the planner cache one user's value into a plan reused for
-- another.
CREATE OR REPLACE FUNCTION app_user() RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')
$$;

-- Future tables are granted to the application role automatically, so a new
-- table cannot be reachable by the owner alone and unreachable by the app --
-- which presents as "the feature does not work" long after the migration that
-- caused it.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eagent_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO eagent_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eagent_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eagent_app;

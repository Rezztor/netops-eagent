-- Three small stores, moved together because they are the same shape: a
-- document or a handful of rows that every request touches and nobody queries
-- across.

-- One row, always. The boolean primary key with a CHECK is the standard way to
-- say "exactly one" in a schema rather than in a comment somebody has to find.
--
-- A document rather than a column per setting. The settings schema is large,
-- nested and still moving, and normalising it would mean a migration for every
-- new field -- which is a cost paid forever to make queries nobody writes
-- easier. What this table is for is the *lock*: set_settings_delta read the
-- whole document, merged into it and wrote it back with nothing held, so two
-- administrators saving different panels at the same time could each keep their
-- own change and lose the other's.
CREATE TABLE settings (
    id         BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    document   JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Password reset tokens. Only the SHA-256 digest is stored: a leaked table is
-- not a set of working links.
--
-- No row-level security, for the same reason the users table has none -- these
-- are consumed by someone who cannot sign in, so there is no scope to compare
-- against at the moment they are used. `user_id` is the account the token
-- belongs to, not an owner in the RLS sense.
CREATE TABLE password_resets (
    digest       TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    issued_at    DOUBLE PRECISION NOT NULL,
    expires_at   DOUBLE PRECISION NOT NULL,
    consumed_at  DOUBLE PRECISION
);

CREATE INDEX password_resets_user ON password_resets (user_id);
-- Pruning walks by expiry, and the rate limit asks "when did this account last
-- ask", which is the same index read backwards.
CREATE INDEX password_resets_expiry ON password_resets (expires_at);

-- Generic persistent key/value. The file version was one JSON file per key and
-- a glob for find(); this is the same thing with a LIKE, and without a
-- directory that grows a file per key forever.
CREATE TABLE kvp (
    key        TEXT PRIMARY KEY,
    value      JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

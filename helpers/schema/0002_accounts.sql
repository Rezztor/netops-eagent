-- Accounts.
--
-- **This table has no row-level security, and that is deliberate.**
--
-- Every other table that follows is user-owned data: a conversation, a memory, a
-- scheduled task, each belonging to somebody, each protected by a policy
-- comparing owner_id against the current scope. A user row is not data belonging
-- to a user. It *is* the user.
--
-- The decisive constraint is authentication. Signing in looks a user up by
-- username before there is any scope to apply -- that is what signing in means --
-- so a policy keyed on the current user would have to be satisfied by a user who
-- is not yet known, which is a policy that cannot be written. Access to this
-- table is gated where it has always been gated: `users.manage` in
-- helpers/api_policy.py, checked before a handler runs.
--
-- What does improve here is uniqueness. The file store checked for a duplicate
-- username or email in Python, under a thread lock, and then wrote -- a
-- check-then-write that two processes could interleave and that two threads
-- could too if either forgot the lock. These are constraints now, enforced by
-- the database, and a duplicate is an error rather than a second account.

CREATE TABLE users (
    id                   TEXT PRIMARY KEY,
    username             TEXT NOT NULL,
    -- Empty for a federated account, which has no password of ours.
    -- authenticate() refuses those outright before any comparison, so this is
    -- never a credential that could match; it is the absence of one.
    password_hash        TEXT NOT NULL,
    display_name         TEXT NOT NULL DEFAULT '',
    email                TEXT NOT NULL DEFAULT '',
    role                 TEXT NOT NULL,
    disabled             BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           TEXT NOT NULL DEFAULT 'system',
    last_login           TIMESTAMPTZ,
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    -- 'local' or the identity provider's name. A federated account has no
    -- password of ours, and authenticate() refuses one rather than comparing
    -- against a hash that can never match.
    provider             TEXT NOT NULL DEFAULT 'local',
    -- issuer|subject, for an account the identity provider owns. Keyed on this
    -- rather than on email or username, both of which are mutable at every
    -- provider and reassignable at some.
    federated_id         TEXT,
    -- Written by update_user and read by nobody today. Kept because "who last
    -- changed this account, and when" is the question an operator asks after an
    -- incident, and it is not recoverable once it has not been recorded.
    updated_at           TIMESTAMPTZ,
    updated_by           TEXT NOT NULL DEFAULT ''
);

-- Usernames are normalised to lower case before they get here, so a plain
-- unique constraint is enough and says so more clearly than an expression index.
CREATE UNIQUE INDEX users_username ON users (username);

-- Email is optional, and two accounts without one are not duplicates. Partial,
-- so an empty string does not collide with every other empty string.
CREATE UNIQUE INDEX users_email ON users (lower(email)) WHERE email <> '';

-- Sign-in reads by username; the session reads by id.
CREATE INDEX users_provider ON users (provider);

-- Partial: local accounts have no federated id, and NULLs do not collide in a
-- unique index anyway -- stated explicitly so the intent survives a reader who
-- does not know that.
CREATE UNIQUE INDEX users_federated ON users (federated_id) WHERE federated_id IS NOT NULL;

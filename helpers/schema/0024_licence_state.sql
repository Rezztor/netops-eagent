-- What this installation holds of its licence (ADR 0078, helpers/license_client.py is the only reader and writer).
--
-- One row for the whole installation: the signed licence, the certificates of the keys that signed what is
-- held, the newest lease, the installation token, the request that has not yet had a verified answer, the
-- latest time the product has reason to believe, and how the last call to the licence server went.
--
-- **No row-level security, because no row has an owner.** The licence belongs to the installation; the check
-- runs on the job loop and at every agent-run start, where there may be no user in scope, and a policy on
-- `app_user()` would hide the row from exactly the code that must read it. The application role gets its
-- grants from the default privileges set in 0001. What keeps the content honest is not the table: every
-- document in it is signed, and the verdict is worked out again from those signatures each time it is asked.
--
-- `token` and the activation key inside a pending activation are secrets. They leave this table only inside
-- a request to the licence server; no endpoint, log line or audit record carries them.
--
-- The row is created by the first write (an upsert), not seeded here: the test fixture truncates every table,
-- and a missing row has to read as "no licence" anyway.

CREATE TABLE licence_state (
    id              BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),   -- one row, by construction
    licence         JSONB,                       -- the licence envelope, as it was received
    certs           JSONB NOT NULL DEFAULT '[]', -- certificate envelopes accepted so far
    lease           JSONB,                       -- the newest lease envelope; NULL for an offline licence
    token           TEXT,                        -- the installation token of the last activation
    pending         JSONB,                       -- {"kind": "activate"|"checkin"|"deactivate", "body": {...}}, repeated unchanged
    mark            BIGINT NOT NULL DEFAULT 0,   -- "The clock" of spec/formats.md, epoch seconds
    last_attempt_at BIGINT NOT NULL DEFAULT 0,
    last_success_at BIGINT NOT NULL DEFAULT 0,
    last_result     TEXT NOT NULL DEFAULT '',    -- "ok", or one plain sentence
    failures        INTEGER NOT NULL DEFAULT 0,  -- failed calls in a row; picks the wait before the next
    next_attempt_at BIGINT NOT NULL DEFAULT 0
);

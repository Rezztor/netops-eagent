-- Azure operations from the chat (ADR 0061 to 0066): the cloud executor's state, the record of what it applied, and
-- the application's list of what it made. Instance-wide, no row-level security, every foreign key (there are none)
-- would be DEFERRABLE INITIALLY IMMEDIATE (ADR 0055 reasons). cloud_price already exists (0018) and is left alone.
--
-- `states` is OpenTofu's pg backend table, created here so the backend never creates a schema or table of its own
-- (backend settings: schema_name = "public", skip_schema_creation, skip_table_creation, skip_index_creation).
-- Checked on 3 October 2026 against internal/backend/remote-state/pg/{backend,client}.go on OpenTofu main: the
-- backend creates `id bigint DEFAULT nextval(seq) PRIMARY KEY, name text UNIQUE, data text`, inserts with
-- `ON CONFLICT (name)`, reads and deletes by name, and takes its advisory lock on `id`. BIGSERIAL gives the same
-- column and a `states_id_seq` sequence.
CREATE TABLE states (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,          -- the workspace: "<subscription guid>/<resource group>"
    data TEXT                           -- ciphertext (OpenTofu encryption, passphrase P)
);

CREATE TABLE cloud_state_version (     -- written by the executor before every apply: the record of what was applied
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace   TEXT NOT NULL,
    change_id   TEXT NOT NULL UNIQUE,   -- a change applies once; a second apply of the same id is refused here
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    state       TEXT NOT NULL,          -- ciphertext, as in states.data
    code        TEXT NOT NULL,          -- the applied code exactly as approved
    variables   JSONB NOT NULL DEFAULT '{}',
    plan_digest TEXT NOT NULL,
    reference   TEXT NOT NULL
);
CREATE INDEX cloud_state_version_ws ON cloud_state_version (workspace, created_at DESC);

CREATE TABLE cloud_managed (           -- written by the application from an apply result; drift and adoption read it
    native_id   TEXT PRIMARY KEY,       -- lower-cased Azure resource ID, the inventory's key
    workspace   TEXT NOT NULL,
    change_id   TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at  TIMESTAMPTZ             -- set only by an inventory pull that no longer finds it; the product never removes
);

-- The application role reads neither the state nor its versions. The default privileges set in 0001 gave it
-- both (and the sequence); this takes them back. The cloud role gets its grants in db.bootstrap(), which is the
-- only place that creates it.
REVOKE ALL ON states, cloud_state_version FROM eagent_app;
REVOKE ALL ON SEQUENCE states_id_seq FROM eagent_app;

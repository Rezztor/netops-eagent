-- The approval of a device or Azure change (ADR 0077): one row per change id, and the only thing a gate believes.
--
-- A change is a JSON file under the user's directory (`plugins/_ea_ansible/helpers/change_store.py`). The file
-- keeps the plan: commands, variables, digest, documents. It was also where the approval lived, signed by the
-- server so a rewritten file is not believed. A signature does not stop a copy: an approved file saved before the
-- apply and put back afterwards is still a validly signed approval until it expires, so a change could run twice,
-- or run after it was cancelled or superseded. A file can be copied; a row cannot be put back by a file tool.
--
-- So the status, the approver, the approved hash, the CR/SR number, the times and how far it has been used are
-- here, and the copy in the file is for display. An approval is written by the approve endpoints only, and taken
-- to run by one UPDATE that names the status, the hash and the step, so of two applies one proceeds.
--
-- **A record with no row is not approved.** That is every change approved before this migration and every file
-- put in the directory by hand: it reads as awaiting approval and the engineer approves it again. Nothing is
-- carried over from the files, because a file is what this stops trusting.
--
-- Additive: nothing existing is changed. User-owned data, so a row is visible only inside its owner's scope and a
-- query with no scope sees nothing, which is a refusal. The id is the key on its own: a file copied into another
-- user's directory meets a row it cannot see and cannot replace.

CREATE TABLE change_approval (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    approved_by    TEXT NOT NULL DEFAULT '',
    approved_hash  TEXT NOT NULL DEFAULT '',
    reference      TEXT NOT NULL DEFAULT '',      -- the CR or SR number typed at approval (Azure)
    approval_basis TEXT NOT NULL DEFAULT '',      -- '' = a person pressed Approve; 'standing:...' = ADR 0048
    -- Epoch seconds, as the record holds them, so the row and the file compare exactly.
    approved_at    DOUBLE PRECISION,
    expires_at     DOUBLE PRECISION,
    -- How far the approval has been used: 0 = not yet, 1 = taken to run, n = step n of a change set was taken.
    step           INTEGER NOT NULL DEFAULT 0,
    used_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE change_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_approval FORCE ROW LEVEL SECURITY;

CREATE POLICY change_approval_owner ON change_approval
    USING (owner_id = app_user())
    WITH CHECK (owner_id = app_user());

CREATE INDEX change_approval_owner ON change_approval (owner_id);

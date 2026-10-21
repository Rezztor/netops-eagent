-- Which runner account belongs to which user.
--
-- A shared runner carries a fixed pool of Linux accounts, `eagent-000` upward,
-- created in the image because its root filesystem is read-only at runtime and
-- `useradd` cannot even lock /etc/passwd there. Each has its own uid and a 0700
-- home, so two users are two users to the kernel rather than two directories
-- one uid can read either of. This table records who holds which.
--
-- It has to be durable, not derived. Hashing an owner onto a slot would be
-- tempting and wrong: 128 slots collide at about even odds by fourteen users,
-- and a collision here is two people sharing a uid and a home. It also has to
-- survive the runner being replaced, which rules out keeping it in the runner's
-- own volume.
--
-- **No row-level security, and like scheduled_tasks that is worth explaining,
-- because these rows do have owners.** `sweep()` runs on the job loop with no
-- user in scope, so `app_user()` is NULL there; under `owner_id = app_user()`
-- the sweeper would match no row and silently reclaim nothing. The claim below
-- is likewise a query about rows belonging to nobody yet. Ownership is enforced
-- where it is established -- one key per slot, and the kernel on the runner --
-- not by hiding rows from the process whose job is to read them all.

CREATE TABLE runner_slots (
    -- The account name on the runner, `eagent-000` and so on. Primary key
    -- rather than a serial: it is the name sshd resolves and the directory the
    -- session lands in, so it is the identity, and a surrogate would be a
    -- second one to keep in step.
    slot      TEXT PRIMARY KEY,

    -- NULL means free. UNIQUE so one user cannot hold two slots, which would
    -- leave a workspace with files in it that nothing points at.
    owner_id  TEXT UNIQUE,

    -- Refreshed on use. Only ever used to decide whose key has gone cold; the
    -- assignment itself does not expire, because the workspace persists and
    -- handing a slot to someone else would hand them its files.
    last_seen TIMESTAMPTZ,

    -- Set when a slot is given up and cleared once its home has been emptied. A
    -- slot awaiting a wipe is not offered to anybody: the alternative is a new
    -- user opening a session into the last one's files.
    needs_wipe BOOLEAN NOT NULL DEFAULT FALSE
);

-- The pool is fixed at the size the image was built with. Seeded here so the
-- claim below is an UPDATE over rows that already exist, which is what lets
-- Postgres do the mutual exclusion with FOR UPDATE SKIP LOCKED instead of the
-- application counting free slots and racing itself.
--
-- If the image is ever rebuilt with more, a later migration adds the rows. A
-- slot present here but absent from the image simply never authenticates, which
-- fails closed.
INSERT INTO runner_slots (slot)
SELECT format('eagent-%s', lpad(i::text, 3, '0'))
FROM generate_series(0, 127) AS i;

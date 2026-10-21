-- Scheduled, ad-hoc and planned tasks.
--
-- A row per task, with the task itself as a document. The three types are
-- pydantic models discriminated on `type`, and normalising them into columns
-- would mean a migration every time one gains a field -- for queries nobody
-- writes. What the rows buy is that adding a task no longer rewrites every
-- other task, which is what a single tasks.json did.
--
-- **No row-level security, and this one is worth explaining because these tasks
-- do have owners.** The job loop has to see every user's tasks in order to run
-- them; that is what a scheduler is. A policy would hide from the scheduler
-- exactly the rows it exists to process. Ownership is enforced where it already
-- is -- `assert_task_owner`, on the personal-schedule endpoints -- and the
-- columns below are there so that stays cheap.
--
-- The distinction to hold on to: this is framework access, like
-- `AgentContext.all()`, not a user reading their own data. The tables that are
-- a user reading their own data are conversations and memory, and those get
-- policies.

CREATE TABLE scheduled_tasks (
    uuid       TEXT PRIMARY KEY,
    -- NULL for a task created outside any user scope. Recording none is better
    -- than inventing one, and assert_task_owner refuses those from personal
    -- endpoints rather than letting them be claimed.
    owner_id   TEXT,
    type       TEXT NOT NULL,
    name       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT '',
    document   JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The personal-schedule endpoints filter by owner; the job loop does not filter
-- at all.
CREATE INDEX scheduled_tasks_owner ON scheduled_tasks (owner_id);
CREATE INDEX scheduled_tasks_state ON scheduled_tasks (state);

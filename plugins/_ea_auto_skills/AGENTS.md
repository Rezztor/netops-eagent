# Auto Skills Plugin DOX

## Purpose

- Own induction of reusable skills from completed tasks, and the path by which one
  becomes available beyond the user who produced it.

## Ownership

- `helpers/capture.py` owns the criteria for a task worth reviewing and the evidence sent,
  and separately which skills a finished turn read.
- `helpers/review.py` owns the utility-model call and parsing its reply.
- `helpers/validate.py` owns every check a generated skill must pass before it is written.
- `helpers/store.py` owns the write into the user's private skills directory and the ledger.
- `helpers/promote.py` owns the promotion queue and the write into shared `usr/skills`.
- `helpers/config.py` owns settings resolution and coercion.
- `evals/` owns the reviewer's measured behaviour: cases with a known right answer and
  a runner. Not pytest -- it calls the utility model.
- `api/auto_skills_promotions.py` and `api/auto_skills_config.py` own administrator
  review and settings; `api/auto_skills_forget.py` owns a user removing their own.
- `prompts/` owns the reviewer instructions.
- The interface lives in `frontend/src/routes/AutoSkillsSections.tsx`, not here.

## Local Contracts

- Induced skills are written per user, under `usr/users/<id>/skills/`. Nothing in this
  plugin writes to `usr/skills` outside `promote.promote`.
- `promote.promote` is reachable only from an endpoint requiring `plugins.manage`, and
  both endpoints are listed by full dispatch path in `helpers/api_policy.py`.
- A validation failure discards the draft. Never correct a draft to make it pass.
- Only skills recorded in the user's ledger may be overwritten, removed or counted.
- Recording usage runs on a turn's way out and must never raise into it: a counter that
  breaks the turn it is counting is worse than no counter. It is also tracked apart from
  capture -- a turn too small to review still read skills, and each is stamped once so a
  hook that fires per chain level does not count the same turn twice.
- Endpoints that act on one user's own skills take the owner from `user_scope`, never
  from the request body, and carry `chat.use` rather than `plugins.manage`. Anything
  that can reach `usr/skills` carries `plugins.manage`.
- Removing an induced skill must withdraw its pending proposals: the queue keeps its
  own copy, so otherwise a discarded skill could still be promoted.
- Staged proposals must never live under a path matching a skill root; skill discovery
  globs `usr/plugins/*/skills`.
- Completion is read from the flag recorded in `tool_execute_after`, never inferred from
  `process_chain_end` having fired: that hook does not fire when the monologue raised,
  and fires once per level of the superior chain when it did.
- The turn boundary is the last *user* message, identified by content shape, not the last
  non-AI message. `hist_add_tool_result` writes with `ai=False` exactly as a user message
  does, so the naive reading moves the boundary on every tool call and blanks the
  counters -- which makes the whole feature silently do nothing.
- Every outcome reaches the audit stream: applied, queued, promoted, rejected, withdrawn,
  forgotten, and also skipped, refused and unusable_reply. A refusal and a reviewer that
  declined are indistinguishable from outside without them.
- Refusal reasons must say what actually happened. A check that could not run is a
  refusal, but reporting it as "contains a secret" sends the reader after the wrong
  thing.
- Capture is stamped per turn on the context, because the hook fires more than once.
- The reviewer runs detached and must never raise into the turn or delay the reply.
- The owner is resolved once at the top of `review.run` and passed down explicitly;
  do not resolve the user from a context variable inside a write path.

## Work Guidance

- Reuse the existing gates rather than adding parallel ones: `helpers.skills` owns the
  SKILL.md standard, `helpers.secrets` owns what a secret is, `_audit` owns provenance.
- Adding a check belongs in `validate.check`, so the whole gate is readable in one place.
- Keep generated skills to instructions. Scripts would need an execution sandbox that
  is still absent (ADR 0006).

## Verification

- `pytest plugins/_ea_auto_skills tests/test_personal_skills_scope.py tests/test_users_rbac.py`
- After changing `prompts/auto_skills.review.sys.md`, re-run
  `python plugins/_ea_auto_skills/evals/run_eval.py 4` and read the per-case column. One
  pass is noise: the same prompt has scored 5/7 and 6/7 on consecutive runs. Broadening
  the criteria has repeatedly cost more on other cases than it gained, so measure before
  and after rather than reasoning about the wording.
- After changing an endpoint's reach, re-check `helpers/api_policy.py` and the RBAC tests
  that assert which roles reach it.
- After changing the interface: `npm run typecheck && npm run lint && npm test` in
  `frontend/`, plus `pytest tests/test_frontend_contract.py`.

## Child DOX Index

No child DOX files.

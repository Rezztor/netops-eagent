# Attachments Plugin DOX

## Purpose

- Own delivering a file from the agent to the user as a download.

## Ownership

- `helpers/runner.py` owns resolving where agent code ran and retrieving a file from it.
- `attach_file` needs `tools.code_execution` (`helpers/tool_permissions.py`): it claims the account a runner slot and runs commands there as that slot, which is using the runner.
- `helpers/store.py` owns writing it under the owner's directory, the manifest, and pruning.
- `tools/attach_file.py` owns the agent-facing tool.
- `api/attachment_get.py` owns serving one file to its owner.
- `prompts/agent.system.tool.attach_file.md` owns how the tool is described to the model.
- The interface lives in `frontend/src/api/attachments.ts` and `routes/Transcript.tsx`.

## Local Contracts

- The download endpoint takes an attachment id and never a user id. The owner comes
  from `user_scope`, so isolation is structural rather than a check that can be removed.
- Attachments are written under `usr/users/<id>/attachments/` and nowhere else.
- A filename from the model is reduced to `[A-Za-z0-9._-]` and basenamed before it
  reaches a path, and the resolved path is asserted to be inside the user's directory.
- Files are served `application/octet-stream` with an `attachment` disposition and
  `nosniff`. Never serve a guessed content type.
- Retrieval never falls back to reading the application's own filesystem when a
  runner is configured; a missing file is an error.
- Size is measured on the runner before the content is read, so an oversized file
  is refused rather than pulled into memory and then rejected.
- Attaching is an explicit tool call. Do not infer attachments from written files.
- An entry carrying an attachment must stand outside the step grouping in the
  transcript, or it folds away with the working.

- With no runner (development only) a local path goes through `helpers.workdir.confine_path` for the owning account.

## Work Guidance

- The endpoint resolution mirrors `code_execution_tool._open_runner_session`. If that
  changes, this changes with it, or attachments will be fetched from the wrong place.
- Keep the retrieval path free of `scp`/`sftp`: the application image has neither.

## Verification

- `pytest plugins/_ea_attachments tests/test_users_rbac.py`
- Frontend: `npm run typecheck && npm run lint && npm test`.
- End to end needs a runner: ask the agent to write a file and attach it, then
  download it as that user and confirm another account gets a 404.

## Child DOX Index

No child DOX files.

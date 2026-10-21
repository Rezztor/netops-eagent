# Error Retry Plugin DOX

## Purpose

- Own retry behavior for unexpected critical exceptions in agent loops.

## Ownership

- `extensions/` owns retry counter reset and critical-exception retry hooks.
- `default_config.yaml` owns retry count defaults.
- `webui/config.html` owns settings UI.
- `plugin.yaml` and `README.md` own metadata and behavior notes.

## Local Contracts

- Do not retry controlled flow exceptions such as `HandledException` and `RepairableException`.
- Keep retry counts scoped per monologue.
- Preserve clear agent-facing history injection when retrying a critical exception.
- The chat-facing warning is one plain sentence (`helpers.errors.user_message`); the traceback goes only into an administrator's chat, as `kvps.details` (`helpers.errors.details_for`). The agent-facing history gets the exception's class name and that same plain sentence, never the traceback: the model answers the chat's owner and would quote server paths to them. The traceback goes to the server log.

## Work Guidance

- Keep retry behavior conservative and observable through logs/UI.

## Verification

- Test or simulate critical, handled, and repairable exception paths after hook changes.

## Child DOX Index

No child DOX files.

# Error Retry

Retry after an unexpected critical exception so the agent has a chance to recover instead of failing immediately.

## What It Does

This plugin hooks into the agent lifecycle and automatically retries the current loop when an unhandled critical exception occurs.

It does **not** retry exceptions that are already treated as controlled agent flow, such as:

- `HandledException`
- `RepairableException`

## Main Behavior

- **Counter reset per monologue**
  - Clears the retry counter at the start of a new monologue.
- **Critical exception retry**
  - On an unexpected exception, logs a warning (one plain sentence; the traceback only for an administrator), waits briefly, injects an agent-facing critical error message into history (the exception's class and the plain sentence, not the traceback, which goes to the server log), and suppresses the original exception while retries remain.
- **Configurable retry count**
  - Uses the plugin's `retries` setting to control how many times recovery is attempted per monologue. The default is `1`.

## Key Files

- `extensions/python/_functions/agent/Agent/monologue/start/_10_reset_critical_exception_counter.py`
- `extensions/python/_functions/agent/Agent/handle_exception/end/_80_retry_critical_exception.py`

## Configuration Scope

- **Settings section**: `agent`
- **Per-project config**: `true`
- **Per-agent config**: `true`
- **Always enabled**: `false`

## Plugin Metadata

- **Name**: `_error_retry`
- **Title**: `Error Retry`
- **Description**: Retry on critical exceptions before failing.

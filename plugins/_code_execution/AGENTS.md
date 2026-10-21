# Code Execution Plugin DOX

## Purpose

- Own terminal, Python, and Node.js code execution through persistent local or SSH-backed sessions.

## Ownership

- `tools/` owns the code execution and input tools.
- `helpers/` owns local shell, SSH shell, and TTY session management.
- `prompts/` owns execution prompt and runtime response fragments.
- `default_config.yaml`, `plugin.yaml`, `extensions/`, and `webui/` own settings, metadata, hooks, and UI config.

## Local Contracts

- Keep session concurrency, timeout, streaming, and reset behavior predictable.
- Execute multi-line terminal input as one current-shell compound so intermediate prompts cannot mark queued work complete; preserve `cd`, exports, and other shell state.
- Treat local process exit and SSH channel termination as definitive command completion even when no final prompt is emitted; recreate terminated sessions before their next command.
- Terminal reset/close must not hang on foreground commands or shells that ignore SIGTERM.
- Local and SSH session wrappers must synchronously release their owned process or connection resources when discarded.
- What the web server holds of a session's output is bounded: the queue between the process and its reader holds `tty_session.MAX_PENDING_CHUNKS` chunks (about 8 MiB) and the pump waits when it is full, so a process nobody reads waits on its pipe; `shell_openssh` keeps the first and the last `OUTPUT_KEEP_BYTES` of a command's output, which is all the tool shows, and one `read_output` call returns after `_DRAIN_BYTES` even from a session that always has more. `tests/test_session_output_bounds.py` covers this.
- Explicitly target local versus SSH execution runtimes.
- Python and Node.js code never appears in a process's arguments: it carries resolved secrets, and every slot on the shared runner sees every argv. `_code_on_fd3` sends it base64, in lines of at most 3000 characters joined by backslash-newline (a busy shell's terminal is in canonical mode and drops what is past 4095 bytes of one line), and the interpreter reads it from descriptor 3 (`ipython -c 'get_ipython().run_cell(open(3...).read())...'`, `node /exe/node_eval.js` with no argument). Standard input stays the terminal. `docker/run/fs/exe/node_eval.js` reads descriptor 3 when given no argument, so the runner image must be rebuilt with an application that sends code this way. Terminal commands are sent as typed. `tests/test_code_never_in_argv.py` covers this.
- Do not hardcode secrets, SSH credentials, or local user paths.
- A working directory belongs to the filesystem it is used in. The runner shares no
  volume with the application container, so an application path is not resolvable there;
  remote sessions get `/workspace/<user_id>`, local sessions get the configured workdir.
- The runner working directory is per user even where the runner is shared. On Docker
  Compose one runner serves every account, so this is the only thing separating them.
- An unresolvable owner refuses execution rather than falling back to a shared directory.
- A session that cannot enter its working directory must fail, not continue in the login
  home.

- A slot is released by commands the application sends *as that slot*: `kill -9 -1` (every process of the account in one kernel pass), then the wipe. Both depend on the runner giving slot accounts `/usr/local/bin/slot-shell` as their shell, which runs a command in a bash that reads no file from the account's home; with bash itself as the shell, a `~/.bashrc` holding `exit 0` made both do nothing and answer success. A runner image from before that shell does not have this property.
- Releasing a slot also removes what the account left in the runner's shared directories (`/tmp`, `/home/eagent`, `/dev/shm`) and its System V IPC (`ipcrm --all` as the account, then checked with `ipcs`), and holds the slot back if anything of its uid remains, file or process: the wipe restores `u+rwx` on what the account owns before deleting, kills again, and passes only when no process of the uid survives outside its own session (`runner_pool._SURVIVORS`). A claim without an owner is refused.
- `release()` takes the row first: one `UPDATE` frees it from the owner and sets `needs_wipe`, so no acquire can reach the slot while it is emptied, and `needs_wipe` is cleared only after a wipe that succeeded. `acquire()` writes its key while holding the row and only if it is still the owner's, and the release commands use a key of their own that replaces the session key, so no session can open behind the wipe. The idle sweep also releases a slot whose account no longer exists, and retries the wipe of a held slot (`owner_id` NULL, `needs_wipe`) once per idle interval; on a held slot `last_seen` is when its wipe began, so a release still running is left alone. `tests/test_shared_runner_pool.py` holds the race with two operations at once against the database.

## Work Guidance

- Preserve long-running command output retrieval and busy-session guards when changing execution flow.

## Verification

- Smoke-test terminal, Python, Node.js, output polling, and reset paths after tool changes.

## Child DOX Index

No child DOX files.

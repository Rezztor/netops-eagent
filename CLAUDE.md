# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`AGENTS.md` at the repository root is a binding engineering contract, and each
subdirectory's `AGENTS.md` is the binding contract for its subtree. Before
editing a file, read the root `AGENTS.md` and every `AGENTS.md` on the path to
your target; the closest one controls local details without weakening the
parents. `CONTRIBUTING.md` holds the house rules (branch flow, the test
baseline, the prohibition on upstream dependencies).

This file summarises what those documents assume you already know.


## Commands

```bash
# Python framework (3.12, pinned in mise.toml)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements.dev.txt

pytest                                    # full suite
pytest tests/test_name.py                 # one file
pytest tests/test_name.py::test_case       # one test
pytest --override-ini=addopts= --continue-on-collection-errors   # without the ini's collection flags

# Frontend (frontend/, React 19 + TypeScript + Vite)
npm --prefix frontend ci
npm --prefix frontend run build      # tsc --noEmit && vite build -> frontend/dist
npm --prefix frontend run typecheck
npm --prefix frontend run lint       # eslint src --max-warnings 0
npm --prefix frontend run test       # vitest run

# Run it
export REZZTOR_DB_URL='postgresql://eagent:devpassword@127.0.0.1:55432/eagent'
export REZZTOR_DB_APP_PASSWORD=apppassword REZZTOR_ADMIN_USER=admin \
       REZZTOR_ADMIN_PASSWORD='choose-a-strong-password' FLASK_SECRET_KEY='stable-random-value'
python run_ui.py --host=127.0.0.1 --port=8080     # UI at /app
```

Or `dev/run.sh` (`stop`, `status`, `logs`, `licence`, `reset`): the same from this working tree on
127.0.0.1:8090 with its own PostgreSQL on 55433 and the runner in Compose project `eagent-dev`, licensed against
the licence server's DEV stack. `dev/README.md` says what it writes where.

There is no Python linter or formatter configured; do not introduce one
incidentally.

### Testing notes that matter

- **The suite needs PostgreSQL** (with `pgvector`). Without it ~150 tests skip
  silently — accounts, sign-in, password reset, licensing. `conftest.py` prints
  the `docker run` command that fixes it, and prints a red banner when a green
  run proved less than it looks. The `database` fixture lives in the root
  `conftest.py` (not `tests/`) because plugin tests need it too.
- **Two pytest runs cannot share one database.** The `database` fixture
  truncates every table, so a concurrent run in another worktree produces a
  scatter of unrelated failures.
- **The quarantine is empty.** `pytest.ini` once filtered out fifteen tests that
  were already failing at the fork point; all fifteen have been deleted or
  fixed, and `docs/internal/pytest-baseline.txt` records what each turned out to
  be. **Any failure is now a regression and blocks the merge.** Read that file
  before adding an entry back.
- `testpaths` covers `tests` and `plugins`; `--ignore-glob=*/api/*` exists because
  several API handlers are named `*_test.py` but are `ApiHandler` classes, not
  pytest modules. `--import-mode=importlib` with `pythonpath = .` is deliberate —
  see the long comment at the top of `pytest.ini` before changing any of it.
- Never require real API keys, network services, or `usr/` state from a test.

## Architecture

A hard fork of an upstream agent framework, rebuilt into a multi-tenant internal
platform: employees sign in, chat with an agent that executes code, browses,
remembers and delegates, and administrators control who may do what. It does not
track upstream and must never call upstream infrastructure or fetch code at
runtime.

### The agent loop

`agent.py` owns `Agent`, `AgentContext` and `LoopData` — do not edit it (or
`initialize.py`) without asking. `Agent.monologue()` is the message loop:
`prepare_prompt` assembles the system prompt from `prompts/` and the active
profile in `agents/`, the model is called through `models.py` (LiteLLM, both
Chat Completions and native Responses), and the result goes through
`process_llm_result_tools` / `process_tools` to a tool in `tools/` or a plugin's
`tools/`. Completion flows through the **response tool with `break_loop`**;
plain or malformed text enters repair, and native Responses output text is
normalised through the same response-tool path.

Import `AgentContext` and `AgentContextType` from `agent`, never from
`helpers.context`.

### Extensions and plugins

Two distinct mechanisms, both discovered from the filesystem:

- **Extensions** (`extensions/python/<hook point>/`, `extensions/webui/<point>/`)
  hook named points in the loop and UI — `message_loop_start`,
  `system_prompt`, `tool_execute_before`, `webui_ws_event` and the rest. The
  directory listing is the list of available hooks.
- **Plugins** (`plugins/_name/`) are the product's features: browser, code
  execution, memory, skills, editor, Ansible network automation, mail, SIEM
  forwarding, audit, chat compaction. Each owns a `plugin.yaml`, optional
  `hooks.py` (framework runtime), `execute.py` (manual, user-triggered work),
  `api/`, `tools/`, `prompts/`, `webui/` and extensions. Bundled plugin names
  start with `_`. Custom plugins are developed under untracked `usr/plugins/` and
  import as `usr.plugins.<name>...`; if you change a live core plugin, copy it
  back into tracked `plugins/`.

`plugins/AGENTS.md` is long and specific (settings resolution order, extension
folder layouts, banner contracts, icon rules). Read it before touching a plugin.

### HTTP API, WebSockets, RBAC

`helpers/api.py` discovers every `api/*.py`, plugin `api/*.py` and
`usr/.../api/*.py` file and registers its `ApiHandler` subclass as a route;
`ws_*.py` modules register `WsHandler` subclasses. Handlers implement
`async def process(self, input, request)`.

**Authentication fails closed and is on by default.** Permissions are declared
in `helpers/rbac.py`; the endpoint → permission mapping is one central table in
`helpers/api_policy.py` so a missing gate is visible by its absence. A handler's
own `required_permission()` wins, which is how plugins gate their own endpoints.
Roles are `admin`, `power`, `user`. Any new endpoint touching settings,
accounts, plugins or the scheduler must appear in `api_policy.py`.

### Storage and isolation

Accounts, settings, conversations, memories and scheduled tasks live in
**PostgreSQL** with no file fallback; `helpers/schema/*.sql` are the migrations
and `helpers/db.py` runs them at startup. Two roles exist: the owner role (in
`REZZTOR_DB_URL`) migrates and serves the few instance-wide operations
(`db.unscoped_transaction`), and the application role serves everything else and
cannot bypass row-level security. Isolation is structural on four tables,
`conversations`, `memories`, `kvp` and `change_approval` (`FORCE ROW LEVEL
SECURITY`: a query that arrives without a user scope set sees nothing); every
other table that carries an owner, `scheduled_tasks` among them, is kept apart by
the `WHERE` of its own code. Read the docstring at the top of `helpers/db.py`
before writing any query.

Per-user files (workdir, uploads, downloads, skills) live under
`usr/users/<user_id>/`. `helpers/user_scope.py` resolves the owner for a
request, for a background agent run (pushed explicitly with `user_scope()`), or
returns `None` at startup — callers must handle all three.

### Code execution

Agent code never runs in the web-server container. A separate unprivileged
**runner** container carries a fixed pool of Linux accounts
(`eagent-000` upward, own uid, `0700` home under `/workspace`), and
`plugins/_code_execution/helpers/runner_pool.py` claims one slot per user and
registers that user's generated public key against it over SSH
(`shell_openssh.py`, key auth only, host key pinned). Releasing a slot kills the
sessions, wipes the home and frees the row; a slot whose wipe failed is marked
`needs_wipe` and held back. `REZZTOR_REQUIRE_REMOTE_EXECUTION` makes the
application refuse to fall back into its own container. ADR 0038 records the
design and what it trades away: sessions share a PID namespace and there is no
per-user CPU or memory limit, because there is one container.

**RFC**, the development link that forwarded calls from a local instance to a
Dockerized one, was removed on 2026-10-11. `runtime.call_development_function`
keeps its name for its many callers and runs the function where it is.

### Frontend

`frontend/` is React + TypeScript, built to gitignored `frontend/dist` and
served **same origin** by the backend at `/app` (`helpers/spa.py`) so that the
session cookie and CSRF token work without a proxy splitting routes. The mount
path is stated in three places that must agree — `spa.MOUNT_PATH`, vite's `base`,
and the `BrowserRouter` basename — and `tests/test_spa_serving.py` checks that
they do. A missing build is a plain failure; there is no second interface.

### Deployment

One shape: **Docker Compose**. `deploy/customer/` holds the customer stack
(eagent, runner, ansible, searxng, postgres) plus the installer and the network
guard (`rezztor-guard`, ADR 0079); `docker/run/` is the development equivalent. The k3s appliance,
the installer ISO and the Helm chart were retired on 2026-09-18 and live in git
history. No code is fetched at runtime and nothing installs itself. A
hand-installed Compose deployment is upgraded by loading the new images and
setting `REZZTOR_VERSION`. Only an appliance installed from the ISO has an
update tool (ADR 0080): an administrator installs one signed file, uploaded or
downloaded. Its host tool asks `update.rezztor.com` once a day which packages
follow its version (instance setting `update_daily_check`, on by default), and
a paid plan downloads them on the administrator's button (ADR 0091); the web
application only writes requests into the spool (`helpers/update_spool.py`) and
never calls the service itself. Releases are a core ISO, then at most ten
packages or three months, with installed machines reaching a new core through a
bridge package, in one strict chain kept forever (ADR 0090). What a customer
receives is built from a pushed tag in a fresh clone, by
`scripts/build_release.sh` (ADR 0084); an ISO built from a working folder is a
development build.

`install.rezztor.com` went the same way on 2026-09-19, with the page it served
and the script that published to it. `scripts/build_offline_bundle.sh` assembles
the whole delivery instead, and `install.sh` / `installer.sh` read the files
beside them — `REZZTOR_INSTALL_URL` is an opt-in, never a default. How the
product reaches a customer is decided once the application is ready; do not
re-add a hosted install path in the meantime.

## Conventions

- **DOX file-level documentation.** `api/` and `helpers/` are file-documented
  profiles: every direct `*.py` needs a sibling `*.py.dox.md` (the full filename
  plus `.dox.md`) describing purpose, contracts, side effects and verification.
  Add, update or delete it in the same change as the module, and update the
  closest `AGENTS.md` when a subsystem's contract changes.
- **No upstream brand leak.** `tests/test_no_upstream_brand_leak.py` enforces it;
  The test file lists the few allowed places and the line-scoped
  `brand-leak-allowed:` opt-out.
- **Decisions are recorded.** `docs/internal/decisions/` holds the ADRs; check for
  a relevant one before re-litigating a design. `docs/internal/` is excluded from
  the shipped image.
- `usr/` and `tmp/` are untracked runtime state, outside tracked DOX. Do not
  document changes there unless asked.
- Use `RepairableException` for errors an agent may be able to fix itself.
- Commit subjects in this repository read as plain statements of intent
  ("Stop a released slot from carrying its last owner into the next").

## Permissions

Allowed without asking: reading repository files, updating files under `usr/`.
Ask before: installing dependencies, deleting core files outside `usr/` or
`tmp/`, modifying `agent.py` or `initialize.py`, creating commits or pushing
branches.

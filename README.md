# netops-eagent

**Network Operations Enterprise Agent.** A self-hosted agentic AI platform for network and infrastructure teams.
People sign in with an account and chat with an agent that runs code, browses, remembers, delegates to sub-agents and
works on the network through Ansible, SolarWinds, Azure and an inventory of your devices. Administrators decide who may
do what.

It is the open-source core of Rezztor EAgent, made by Rezztor Technologies. Every feature is in this repository.

## Free software, paid support

| | Community | Rezztor Enterprise |
|---|---|---|
| Source and every feature | Yes, AGPL-3.0 | Yes |
| Price | Free, no device or user limit | Subscription |
| Builds | Build it yourself | Signed images and installer ISO from Rezztor |
| Updates | Rebuild from a newer tag | Signed update channel, including the base OS's security updates |
| Support | Issues and discussions here | Support with an SLA, security response, long support life |
| Help with your network | | Customisation, integrations, training, installation |

Enterprise support: <https://rezztor.com>.

A source checkout and an image you build yourself are the Community build: no licence server is contacted, and there is no device or user limit. A release image from Rezztor is Enterprise and uses the licence.

---

## Running it locally

Requires Python 3.12 (pinned in `mise.toml`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements.dev.txt

export REZZTOR_ADMIN_USER=admin
export REZZTOR_ADMIN_PASSWORD='choose-a-strong-password'
export FLASK_SECRET_KEY='a-stable-random-value'

python run_ui.py --port=8080 --host=127.0.0.1
```

Open http://127.0.0.1:8080 and sign in. If you do not set `REZZTOR_ADMIN_PASSWORD`, a strong password is generated and printed once to the startup log.

Set `FLASK_SECRET_KEY` to a stable value. Without it a new key is generated on every boot, which signs every user out on restart.

## Running it in a container

The base image is built from this repository; nothing is pulled from a third-party registry.

```bash
docker build -t rezztor/eagent-base:latest docker/base/
docker build -t rezztor/eagent:latest --build-arg CACHE_DATE=$(date +%s) .
docker build -t rezztor/eagent-runner:latest -f docker/runner/Dockerfile .

cd docker/run && docker compose up -d      # http://localhost:50080/app
```

Use Compose rather than a bare `docker run`: the application deliberately
executes no agent code itself, so it needs the runner container beside it and
refuses to start code execution without one. `docker/run/docker-compose.yml`
wires the two together and documents what each variable is for.

`/eagent/usr` is a volume holding settings, accounts, chats, memory, knowledge
and workdirs. Nothing is fetched at runtime, and there is no in-place
self-update. A hand-installed Compose deployment is upgraded by loading the new
images and setting `REZZTOR_VERSION`. Only an appliance installed from the ISO
has an update tool.

---

---

## Accounts and roles

The first administrator is created at startup. Administrators create everyone else from the Users screen or the API.

| Role | Can |
|---|---|
| `admin` | Everything: accounts, settings, provider credentials, plugins, all tools |
| `power` | Chat, code execution, browser, subordinate agents, scheduled tasks |
| `user` | Chat, memory, knowledge and skills only |

Authentication is mandatory and fails closed. Permissions are declared in `helpers/rbac.py`; the mapping from API endpoint to required permission is in `helpers/api_policy.py`.

---

## Configuration

| Variable | Purpose |
|---|---|
| `REZZTOR_ADMIN_USER` / `REZZTOR_ADMIN_PASSWORD` | Seed the first administrator (first boot only) |
| `REZZTOR_ADMIN_MUST_CHANGE_PASSWORD` | Force the seed admin to change a published default password at first sign-in |
| `FLASK_SECRET_KEY` | Session signing key. Must be stable across restarts |
| `WEB_UI_HOST` / `WEB_UI_PORT` | Bind address and port (default `localhost:5000`) |
| `API_KEY_<PROVIDER>` | Model provider credentials |
| `REZZTOR_PLUGIN_INDEX_URL` | Internal plugin index. Unset means the Plugin Hub is disabled |
| `REZZTOR_SET_<setting>` | Override any setting at boot, for unattended deployment |
| `REZZTOR_RUNNER_HOST` / `_PORT` / `_USER` | Where agent code executes. Declaring these makes the application use the runner |
| `REZZTOR_RUNNER_IDENTITY_FILE` | SSH key for the runner. Key auth only — the runner refuses passwords |
| `REZZTOR_REQUIRE_REMOTE_EXECUTION` | Fail closed: refuse to execute in the application container if the runner is unreachable |

Model providers are declared in `conf/model_providers.yaml`. Azure OpenAI, AWS Bedrock, vLLM, Ollama, LM Studio and llama.cpp are all supported, so models can be kept on-premises.

---

---

## Layout

| Path | Contents |
|---|---|
| `agent.py`, `initialize.py`, `run_ui.py` | Agent loop, framework init, web entry point |
| `helpers/` | Framework services, including accounts, RBAC and API policy |
| `tools/` | Agent-callable tools |
| `plugins/` | Bundled plugins — browser, code execution, memory, scheduler, integrations |
| `prompts/`, `agents/` | System prompts and agent profiles |
| `knowledge/` | Built-in reference material, indexed for recall |
| `frontend/` | React + TypeScript interface, built to `frontend/dist` and served at `/app` |
| `deploy/` | The Compose stack and installer under `deploy/customer/`; pinned dev tooling under `deploy/dev/` |
| `usr/` | Runtime state: settings, accounts, chats, memory, workdirs. Not in version control |


## Development

```bash
pytest                      # full suite (needs PostgreSQL with pgvector; conftest.py prints the docker command)
pytest tests/test_name.py   # one file
npm --prefix frontend run test
```

Any test failure is a regression. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Copyright (C) 2026 Rezztor Technologies.

netops-eagent is free software under the GNU Affero General Public License version 3 ([LICENSE](LICENSE)). If you run a
modified version as a network service, you must offer its source to the people who use it.

It is a derivative work of Agent Zero, used under the MIT licence; that notice and the other third-party notices are in <!-- brand-leak-allowed: the attribution itself -->
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). "Rezztor" and its logo are trademarks of Rezztor Technologies and are
not licensed with the code: see [TRADEMARKS.md](TRADEMARKS.md).

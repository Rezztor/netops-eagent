# Contributing to netops-eagent

netops-eagent is open source, not open contribution. Rezztor Technologies writes all of its code, and pull requests
from outside are closed without review. That keeps the ownership of every line in one place.

You are free to use, change and run the code under the AGPL-3.0, including on your production network, and to keep your
own fork. What helps most:

- **Bug reports and feature ideas** as GitHub issues: what you did, what happened, what you expected.
- **Security problems** privately, as [SECURITY.md](SECURITY.md) says.

The rest of this page is for maintainers and for anyone working on their own fork.

## Workflow

Branch from `main` and merge back into `main`. The suite has to be green, and a behaviour change comes with its test.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements.dev.txt
docker run -d --name eagent-pg -e POSTGRES_USER=eagent -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=eagent \
  -p 55432:5432 pgvector/pgvector:pg17
pytest
npm --prefix frontend ci && npm --prefix frontend run lint && npm --prefix frontend run test
```

Without PostgreSQL about 150 tests skip and `conftest.py` says so in red. Two test runs cannot share one database.

`AGENTS.md` at the root and in each folder describes that folder's contract; read the ones on the path to the file you
change. Commit subjects read as plain statements of intent ("Stop a released slot from carrying its last owner into the
next"). Numbers like "ADR 0080" in comments refer to the maintainers' decision records.

## House rules

- **Nothing fetches code or configuration at run time**, and nothing installs or updates itself. No calls to third-party services the administrator did not configure.

- **Authentication fails closed.** Any new API handler is authenticated by default. If it touches settings, accounts, plugins, or the scheduler, add it to the permission table in `helpers/api_policy.py`.
- **Never commit secrets.** `usr/` holds runtime state and is not tracked.
- Add or update tests with behaviour changes, and update the closest `AGENTS.md` when you change a subsystem's contract.

## Plugins

Develop custom plugins under `usr/plugins/`, which is untracked and survives upgrades. Bundled plugins live in `plugins/` and are part of the product.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).

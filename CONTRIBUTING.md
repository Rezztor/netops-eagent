# Contributing to netops-eagent

Thank you for helping. Issues, ideas and pull requests are all welcome.

## Before your first pull request: the CLA

Every contributor signs the Contributor Licence Agreement in [CLA.md](CLA.md) once. A bot asks you on your first pull
request; signing is a comment. You keep the copyright in your work. The agreement lets Rezztor Technologies also offer the
code under a commercial licence to organisations that cannot use the AGPL, and that is what pays for the project's
maintenance.

**The promise that comes with it:** every contribution accepted here stays available under the AGPL-3.0 or another
licence approved by the Open Source Initiative, in this public repository, for as long as the project exists. The CLA
cannot be used to take the code closed.

## Workflow

Fork, branch from `main`, open a pull request against `main`. The suite has to be green, and a behaviour change comes
with its test.

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

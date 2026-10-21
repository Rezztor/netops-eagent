"""The executor's database connection, as the `eagent_cloud` role.

Two jobs: `prices.py` (copied from the read plugin at build, with `from helpers import db` rewritten to
`from cloud_executor import db`) needs `NO_SCOPE` and `transaction`, and `versions` writes `cloud_state_version`.
The role holds exactly the grants in `helpers/db.py` `_CLOUD_GRANTS` and nothing else (ADR 0064). The same role and
password reach OpenTofu's `pg` backend through `PG_CONN_STR`; `url()` is the one place that is built.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import quote

from executor import secret_env  # copied into the image beside credentials.py (executor/)

ENV_PASSWORD = "REZZTOR_DB_CLOUD_PASSWORD"
ENV_HOST = "REZZTOR_CLOUD_DB_HOST"
ROLE = "eagent_cloud"
NO_SCOPE = ""  # prices.py passes this; there is no row-level security on the tables this role reaches


def url() -> str:
    """The connection URL, or `secret_env.SecretConfigError` / `RuntimeError` when no password is set.

    `sslmode=disable`: the database is on `cloud_db`, an internal network of two containers, and OpenTofu's
    driver defaults to `require`, which the stock image cannot serve.
    """
    password = secret_env.get(ENV_PASSWORD)
    if not password:
        raise RuntimeError(f"{ENV_PASSWORD} is not set, so the executor cannot reach its database.")
    host = (os.getenv(ENV_HOST) or "postgres").strip()
    return f"postgresql://{ROLE}:{quote(password, safe='')}@{host}:5432/eagent?sslmode=disable"


@contextmanager
def transaction(_scope: Any = None) -> Iterator[Any]:
    """One connection, committed on a clean exit and rolled back on an exception."""
    import psycopg  # noqa: PLC0415 - the tests that do not touch a database need not have it

    with psycopg.connect(url()) as conn:
        yield conn

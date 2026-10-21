"""The search backend, and the three ways it can fail.

SearXNG runs in its own container, a sibling service in the compose stack, so
its address is a deployment fact and arrives as `REZZTOR_SEARXNG_URL`. It used
to be a hardcoded `localhost:55510`, which was true only while the search engine
lived inside the application container. Once it moved out, nothing was bound to
that port and every search failed.

Errors are typed rather than raw, for the same reason
`plugins/_ea_ansible/helpers/executor_client.py` types its own: the caller has to
tell "nobody configured this" from "it is down" from "it said no", because those
are three different sentences to show a user and only one of them is worth
retrying.
"""

from __future__ import annotations

import os

import aiohttp

from helpers import runtime

ENV_URL = "REZZTOR_SEARXNG_URL"

# A hung search engine used to hang the agent loop with it: there was no timeout
# of any kind here. Connect is short because the service is a DNS name on the
# same network; read is generous because a federated search waits on several
# upstream engines.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30


class SearchError(Exception):
    """Base for everything this module raises."""


class NotConfigured(SearchError):
    """No search engine address on this instance."""


class SearchUnreachable(SearchError):
    """The search engine did not answer."""


class SearchRefused(SearchError):
    """It answered, and said no. The message is safe to show a user."""


def base_url() -> str:
    """Where the search engine is, or empty.

    A base URL rather than a full endpoint, matching how the Ansible executor's
    address is passed -- the caller owns the path, so a deployment that puts the
    service behind a prefix does not need a code change.
    """
    return str(os.getenv(ENV_URL) or "").strip().rstrip("/")


def is_configured() -> bool:
    """Whether this deployment has a search engine at all.

    There is deliberately no default. While SearXNG lived in the application
    image, `localhost:55510` was a reasonable guess; now that it is a separate
    container there is nothing at that address on any deployment, and guessing
    would turn "no search engine was configured" into "the search engine is
    down" -- which sends whoever reads it looking for a container that was never
    meant to exist.
    """
    return bool(base_url())


async def search(query: str):
    return await runtime.call_development_function(_search, query=query)


async def _search(query: str):
    url = base_url()
    if not url:
        raise NotConfigured(
            "Web search is not configured on this instance. An administrator "
            "must set the search engine address."
        )

    timeout = aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, total=READ_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{url}/search", data={"q": query, "format": "json"}
            ) as response:
                if response.status != 200:
                    # Most often 403: SearXNG serves the JSON API only when
                    # `formats: [json]` is in its settings, and refuses
                    # otherwise. Saying so beats reporting a bare status code,
                    # because the fix is a config file and not a restart.
                    raise SearchRefused(
                        f"The search engine refused the query ({response.status}). "
                        "If this is a fresh deployment, check that its settings "
                        "enable the JSON format."
                    )
                return await response.json()
    except aiohttp.ClientError as error:
        raise SearchUnreachable(f"The search engine did not answer: {error}") from error
    except TimeoutError as error:
        raise SearchUnreachable(
            f"The search engine did not answer within {READ_TIMEOUT}s."
        ) from error

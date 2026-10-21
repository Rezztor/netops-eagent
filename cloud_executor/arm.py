"""The executor's own reads of Azure: a token, and GET. Used by the role check and by the planner's existence checks.

Two hosts, both constants; redirects are never followed; the only verb here is GET (the token request is the one
POST, to the login host). The change itself is made by OpenTofu through the providers, not by this module.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import requests

from cloud_executor import keys

LOGIN = "https://login.microsoftonline.com"
MANAGEMENT = "https://management.azure.com"
TIMEOUT = 20
MAX_ANSWER = 4 * 1024 * 1024


class ArmError(Exception):
    """Azure said no, or could not be reached. The message is safe to show: it never holds a token."""


def token(role: str) -> str:
    """A management-plane access token for `role`'s identity, signed in with its certificate."""
    who = keys.identity(role)
    if not who:
        raise ArmError(f"The {role} key has not been made yet.")
    tenant, client = who
    endpoint = f"{LOGIN}/{tenant}/oauth2/v2.0/token"
    try:
        reply = requests.post(endpoint, timeout=TIMEOUT, allow_redirects=False, data={
            "client_id": client, "scope": f"{MANAGEMENT}/.default", "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": keys.assertion(role, endpoint)})
        answer = reply.json()
    except (requests.RequestException, ValueError, keys.KeyRefused) as error:
        raise ArmError(f"Sign-in as the {role} identity failed: {type(error).__name__}.") from error
    if reply.status_code != 200 or not answer.get("access_token"):
        raise ArmError(f"Entra refused the {role} identity: {str(answer.get('error_description') or answer.get('error') or reply.status_code)[:200]}")
    return str(answer["access_token"])


def claims(access_token: str) -> dict[str, Any]:
    """The payload of a token we just received (not verified: only used to read our own `oid`)."""
    try:
        part = access_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def get(access_token: str, path_and_query: str) -> Any:
    """GET `https://management.azure.com` + a path that starts with `/`; the parsed JSON, or `ArmError`."""
    if not path_and_query.startswith("/"):
        raise ArmError("An Azure path starts with /.")
    try:
        reply = requests.get(MANAGEMENT + path_and_query, timeout=TIMEOUT, allow_redirects=False,
                             headers={"Authorization": f"Bearer {access_token}"})
    except requests.RequestException as error:
        raise ArmError(f"Azure could not be reached: {type(error).__name__}.") from error
    if reply.status_code == 404:
        return None
    if reply.status_code != 200 or len(reply.content) > MAX_ANSWER:
        raise ArmError(f"Azure answered {reply.status_code} for {path_and_query.split('?')[0]}.")
    try:
        return reply.json()
    except ValueError as error:
        raise ArmError("Azure answered with something that is not JSON.") from error

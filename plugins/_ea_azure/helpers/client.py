"""The only module that reaches the network, and it can only read.

Three calls, nothing else: sign in (`POST {login}/{tenant}/oauth2/v2.0/token`), list the subscriptions
(`GET {arm}/subscriptions`) and ask Resource Graph (`POST {arm}/providers/Microsoft.ResourceGraph/resources`).
No function takes a URL, a path or a method from a caller, and `graph` takes only a query constant from
`queries.py`. A write feature is a new, separately reviewed module with its own credential, never a branch here;
`tests/test_azure_client.py` scans this file's source for anything else.

Nothing raised here carries the credential, the token or Azure's own error text: a message is the plugin's
own sentence, plus an AADSTS number for a failed sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import os
import threading
import time
import uuid
from typing import Iterator, NamedTuple
from urllib.parse import urlsplit

import jwt
import requests
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from plugins._ea_azure.helpers import config, credential

log = logging.getLogger(__name__)


class Cloud(NamedTuple):
    login: str
    arm: str


CLOUDS = {"public": Cloud(login="https://login.microsoftonline.com", arm="https://management.azure.com")}

SUBSCRIPTIONS_API = "2022-12-01"
GRAPH_API = "2022-10-01"
_MAX_ANSWER = 16 * 1024 * 1024
_MIN_TOP = 100
_TOKEN_MARGIN = 300  # seconds before expiry at which the cached token is replaced


class AzureError(Exception):
    """Base of everything this module raises. Never contains the credential or the token."""


class NotConfigured(AzureError): ...


class SignInFailed(AzureError):
    """`.reason`: tenant | application | certificate | secret | expired | network | other. `.code`: the AADSTS number or 0."""

    def __init__(self, reason: str, message: str, code: int = 0):
        super().__init__(message)
        self.reason, self.code = reason, code


class ReadFailed(AzureError):
    def __init__(self, message: str, status: int | None = None, subscription: str | None = None):
        super().__init__(message)
        self.status, self.subscription = status, subscription


class Throttled(ReadFailed):
    """Still refused after the retries."""


# --- the stand-in -----------------------------------------------------------------------------------------------------

def _loopback_or_compose(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return "." not in host


def _test_endpoints(value: str) -> Cloud | None:
    """`login=<url>;arm=<url>`, accepted only when both hosts are loopback or a dot-less compose name."""
    if not value:
        return None
    try:
        parts = dict(p.split("=", 1) for p in value.split(";") if p.strip())
        cloud = Cloud(login=parts["login"].strip().rstrip("/"), arm=parts["arm"].strip().rstrip("/"))
    except (KeyError, ValueError):
        cloud = None
    if cloud is None or not all(_loopback_or_compose(u) for u in cloud):
        log.error("REZZTOR_AZURE_ENDPOINTS_FOR_TESTS ignored: it needs login= and arm= on loopback or a bare host name.")
        return None
    return cloud


# An environment variable, read once: nothing in the interface or the agent can set it.
TEST_ENDPOINTS = _test_endpoints(os.environ.get("REZZTOR_AZURE_ENDPOINTS_FOR_TESTS", ""))


def _cloud() -> Cloud:
    return TEST_ENDPOINTS or CLOUDS[config.get_config()["cloud"]]


# --- the two HTTP verbs this module has --------------------------------------------------------------------------------

_sleep = time.sleep
_clock = time.monotonic


def _post(url: str, **kw) -> requests.Response:
    return requests.post(url, timeout=config.get_config()["timeout_seconds"], allow_redirects=False, **kw)


def _get(url: str, **kw) -> requests.Response:
    return requests.get(url, timeout=config.get_config()["timeout_seconds"], allow_redirects=False, **kw)


# --- sign in ----------------------------------------------------------------------------------------------------------

# unverified: these AADSTS numbers are from Microsoft's documentation, not yet seen from a real tenant (phase 4).
_SIGN_IN_ERRORS = {
    90002: ("tenant", "Azure does not know this directory (tenant) ID."),
    900023: ("tenant", "This directory (tenant) ID is not valid."),
    700016: ("application", "This application (client) ID is not registered in that directory."),
    7000215: ("secret", "The client secret is wrong."),
    7000222: ("expired", "The client secret has expired. Create a new one in Azure and save it here."),
    700027: ("certificate", "Azure did not accept the certificate. Upload the public file to the app registration."),
    700024: ("certificate", "The sign-in proof was not valid at this time. Check the clock on this server."),
    700023: ("certificate", "Azure did not accept the certificate for this application."),
}

_cache: dict = {"key": None, "token": "", "until": 0.0}
_lock = threading.Lock()


def reset() -> None:
    """Forget the cached token, so the next `token()` signs in again."""
    with _lock:
        _cache.update(key=None, token="", until=0.0)


def _sign_in_failure(response: requests.Response) -> SignInFailed:
    try:
        codes = [int(c) for c in (response.json().get("error_codes") or [])]
    except (ValueError, TypeError, AttributeError):
        codes = []
    for code in codes:
        if code in _SIGN_IN_ERRORS:
            reason, sentence = _SIGN_IN_ERRORS[code]
            return SignInFailed(reason, f"{sentence} (AADSTS{code})", code)
    code = codes[0] if codes else 0
    return SignInFailed("other", "Azure refused the sign-in." + (f" (AADSTS{code})" if code else ""), code)


def _assertion(pem: str, url: str, client_id: str) -> str:
    """A client assertion signed with the certificate's key: PS256, `x5t#S256` in the header."""
    cert = x509.load_pem_x509_certificate(pem[pem.index("-----BEGIN CERTIFICATE-----"):].encode())
    key = serialization.load_pem_private_key(pem[:pem.index("-----BEGIN CERTIFICATE-----")].encode(), password=None)
    thumb = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()
    now = int(time.time())
    claims = {"aud": url, "iss": client_id, "sub": client_id, "jti": str(uuid.uuid4()), "nbf": now, "exp": now + 600}
    return jwt.encode(claims, key, algorithm="PS256", headers={"x5t#S256": base64.urlsafe_b64encode(thumb).rstrip(b"=").decode()})


def token() -> str:
    """A bearer token for Azure Resource Manager, cached in memory until five minutes before it expires."""
    cfg = config.get_config()
    cloud = _cloud()
    kind, material = credential._material()
    if not (cfg["tenant_id"] and cfg["client_id"] and kind):
        raise NotConfigured("Azure is not set up: it needs a tenant ID, an application ID and a credential.")
    url = f"{cloud.login}/{cfg['tenant_id']}/oauth2/v2.0/token"
    key = (url, cfg["client_id"], hash(material))
    with _lock:
        if _cache["key"] == key and _clock() < _cache["until"]:
            return _cache["token"]
        form = {"grant_type": "client_credentials", "client_id": cfg["client_id"], "scope": f"{cloud.arm}/.default"}
        if kind == "secret":
            form["client_secret"] = material
        else:
            form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            form["client_assertion"] = _assertion(material, url, cfg["client_id"])
        try:
            response = _post(url, data=form)
        except requests.RequestException:
            raise SignInFailed("network", "Microsoft sign-in could not be reached.") from None
        if response.status_code != 200:
            raise _sign_in_failure(response)
        try:
            body = response.json()
            issued, lifetime = str(body["access_token"]), int(body.get("expires_in") or 3600)
        except (ValueError, TypeError, KeyError):
            raise SignInFailed("other", "Azure answered the sign-in with something this plugin cannot read.") from None
        _cache.update(key=key, token=issued, until=_clock() + max(lifetime - _TOKEN_MARGIN, 0))
        return issued


# --- reading ----------------------------------------------------------------------------------------------------------

def _seconds(value: str | None) -> float | None:
    """`5`, `5.5` or `hh:mm:ss`, as the quota and Retry-After headers send it."""
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        return None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts))) if 1 <= len(parts) <= 3 else None


def _wait(response: requests.Response) -> float:
    for header in ("Retry-After", "x-ms-user-quota-resets-after"):
        seconds = _seconds(response.headers.get(header))
        if seconds is not None:
            return min(seconds, 120)
    return 5


def _arm(send, url: str, subscription: str | None = None, **kw) -> requests.Response:
    """One authorised call, retried when Azure says 'too many requests': three tries, then `Throttled`."""
    for attempt in range(3):
        try:
            response = send(url, headers={"Authorization": f"Bearer {token()}"}, **kw)
        except requests.RequestException:
            raise ReadFailed("Azure could not be reached.", subscription=subscription) from None
        if response.status_code == 401:
            reset()
        if response.status_code != 429:
            return response
        if attempt < 2:
            _sleep(_wait(response))
    raise Throttled("Azure is limiting requests and still refused after three tries.", 429, subscription)


def _json(response: requests.Response, subscription: str | None = None) -> dict:
    if response.status_code != 200:
        raise ReadFailed(f"Azure refused the read (HTTP {response.status_code}).", response.status_code, subscription)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ReadFailed("Azure answered with something this plugin cannot read.", response.status_code, subscription)
    return body


def subscriptions() -> list[dict]:
    """`[{"id", "name", "state", "tenant_id"}]`: what the application can read."""
    arm = _cloud().arm
    url, found = f"{arm}/subscriptions?api-version={SUBSCRIPTIONS_API}", []
    while url:
        body = _json(_arm(_get, url))
        for row in body.get("value") or []:
            found.append({
                "id": str(row.get("subscriptionId") or "").lower(), "name": str(row.get("displayName") or ""),
                "state": str(row.get("state") or ""), "tenant_id": str(row.get("tenantId") or "").lower(),
            })
        url = body.get("nextLink") or ""
        if url and not url.startswith(f"{arm}/"):
            raise ReadFailed("Azure pointed the subscription list at another address; it was not followed.")
    return found


def graph(query: str, subscription_ids: list[str] | None) -> Iterator[list[dict]]:
    """Every page of one Resource Graph query. `query` is a constant from `queries.py`, scope is `subscription_ids`."""
    if subscription_ids is not None and not subscription_ids:
        return
    scope = subscription_ids[0] if subscription_ids and len(subscription_ids) == 1 else None
    url = f"{_cloud().arm}/providers/Microsoft.ResourceGraph/resources?api-version={GRAPH_API}"
    top, skip = config.get_config()["page_size"], None
    while True:
        options = {"$top": top, "resultFormat": "objectArray"}
        if skip:
            options["$skipToken"] = skip
        body = {"query": query, "options": options}
        if subscription_ids is not None:
            body["subscriptions"] = list(subscription_ids)
        response = _arm(_post, url, scope, json=body)
        too_big = response.status_code == 413 or len(response.content) > _MAX_ANSWER
        if not too_big:
            answer = _json(response, scope)
            too_big = str(answer.get("resultTruncated")).lower() == "true"
        if too_big:
            if top <= _MIN_TOP:
                raise ReadFailed("Azure's answer is too large even at the smallest page size.", response.status_code, scope)
            top = max(top // 2, _MIN_TOP)
            continue
        rows = answer.get("data")
        if not isinstance(rows, list):
            raise ReadFailed("Azure answered with something this plugin cannot read.", response.status_code, scope)
        yield rows
        skip = answer.get("$skipToken")
        if not skip:
            return
        if _seconds(response.headers.get("x-ms-user-quota-remaining")) == 0:
            _sleep(_seconds(response.headers.get("x-ms-user-quota-resets-after")) or 5)

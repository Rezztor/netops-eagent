"""Corporate sign-in over OpenID Connect.

Phase 3. The account store in `helpers/users.py` is sound, but it is a second
list of who works here, and the failure that matters is offboarding: when
someone leaves, IT disables their corporate account and their EAgent account
keeps working until a human remembers it separately. This makes the identity
provider the authority.

**Authorization Code with PKCE.** The user authenticates at the IdP, so no
password ever reaches this process -- and whatever MFA, lockout and conditional
access IT enforces is inherited rather than reimplemented.

**Users are provisioned just in time into the existing store.** `login_session`,
`current_user`, per-user directories and the runner pool all key off a record in
`usr/users.json`, so an OIDC login upserts one and everything downstream is
unchanged. The alternative -- a parallel identity path through the whole
codebase -- would have doubled the number of places a permission check could be
wrong.

**Identity is the `sub` claim, never the email address.** Email is mutable and
at some providers reassignable; `sub` is stable and unique for the life of the
account. Keying users on email is how one employee inherits another's history.

**Roles come from group membership on every login.** A user removed from
`eagent-admins` in the directory loses admin here at their next sign-in, without
anyone touching this system. A user in no mapped group is refused rather than
given a default: silently admitting unknown identities at the lowest privilege
is still admitting them.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from jwt import PyJWKClient

from helpers import rbac
from helpers.print_style import PrintStyle

ENV_ISSUER = "REZZTOR_OIDC_ISSUER"
ENV_CLIENT_ID = "REZZTOR_OIDC_CLIENT_ID"
ENV_CLIENT_SECRET = "REZZTOR_OIDC_CLIENT_SECRET"
ENV_REDIRECT_URI = "REZZTOR_OIDC_REDIRECT_URI"
ENV_SCOPES = "REZZTOR_OIDC_SCOPES"
ENV_GROUPS_CLAIM = "REZZTOR_OIDC_GROUPS_CLAIM"
ENV_ROLE_MAP = "REZZTOR_OIDC_ROLE_MAP"
ENV_VERIFY_TLS = "REZZTOR_OIDC_VERIFY_TLS"

DEFAULT_SCOPES = "openid profile email"
DEFAULT_GROUPS_CLAIM = "groups"

# Session keys holding the in-flight handshake. Cleared as soon as it completes,
# so a stale state cannot be replayed against a later login.
SESSION_STATE = "oidc_state"
SESSION_NONCE = "oidc_nonce"
SESSION_VERIFIER = "oidc_verifier"
SESSION_NEXT = "oidc_next"

# An authorization request older than this is abandoned. Long enough for a real
# person to complete an MFA prompt, short enough that a leaked state parameter
# is not useful later.
HANDSHAKE_TIMEOUT_SECONDS = 600
SESSION_STARTED = "oidc_started"

_discovery_cache: dict[str, Any] = {}
_jwk_clients: dict[str, PyJWKClient] = {}


class OidcError(RuntimeError):
    """Sign-in could not be completed.

    The message reaches the login page, so it says what went wrong without
    naming which account exists or which group was missing.
    """


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    username: str
    email: str
    display_name: str
    groups: tuple[str, ...]
    role: str


def is_configured() -> bool:
    """True when a deployment has declared an identity provider.

    Local accounts remain the path when it has not -- development, and the
    break-glass account for an IdP outage.
    """
    return bool(_env(ENV_ISSUER) and _env(ENV_CLIENT_ID))


def _env(name: str, default: str = "") -> str:
    """A variable that is set but empty counts as unset.

    Deployments declare the whole block and fill in what they use --
    `REZZTOR_OIDC_SCOPES: "${REZZTOR_OIDC_SCOPES-}"` in a compose file reaches
    the process as an empty string, not as an absent name. Without this, an
    operator who configured an issuer and nothing else would get an empty
    scope and an empty groups claim: sign-in would work and every identity
    would then be refused for belonging to no mapped group, which reads like a
    directory problem and is not one.
    """
    value = str(os.getenv(name, "")).strip()
    return value or default


def _verify_tls() -> bool:
    # Only ever disabled for a development IdP served over plain HTTP.
    return _env(ENV_VERIFY_TLS, "true").lower() not in ("0", "false", "no", "off")


def discovery() -> dict[str, Any]:
    """The provider's advertised endpoints, fetched once and cached.

    Read from the well-known document rather than configured by hand: providers
    differ in path layout, and a hand-written token endpoint is a support ticket
    waiting to happen.
    """
    issuer = _env(ENV_ISSUER).rstrip("/")
    if not issuer:
        raise OidcError("No identity provider is configured.")
    if issuer in _discovery_cache:
        return _discovery_cache[issuer]

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        response = requests.get(url, timeout=15, verify=_verify_tls())
        response.raise_for_status()
        document = response.json()
    except Exception as exc:  # noqa: BLE001 - reported to the operator as-is
        raise OidcError(f"Could not reach the identity provider at {issuer}.") from exc

    # The issuer in the document is authoritative and must match what we were
    # configured with, or a redirect could point us at someone else's provider.
    if document.get("issuer", "").rstrip("/") != issuer:
        raise OidcError("The identity provider's issuer does not match its configuration.")

    _discovery_cache[issuer] = document
    return document


def _jwk_client() -> PyJWKClient:
    uri = discovery()["jwks_uri"]
    if uri not in _jwk_clients:
        # Caches keys and refetches when it sees an unknown key id, which is
        # what makes provider key rotation a non-event.
        _jwk_clients[uri] = PyJWKClient(uri, cache_keys=True)
    return _jwk_clients[uri]


# ── the handshake ────────────────────────────────────────────────────


def begin_login(session: dict[str, Any], next_url: str = "") -> str:
    """Start sign-in. Returns the URL to send the browser to.

    Three one-time values are minted and kept server-side in the session:

    - `state` ties the callback to this browser, so an attacker cannot feed us
      their own authorization code and have us adopt their identity.
    - `nonce` is echoed inside the ID token, which stops a token issued for a
      different login from being replayed into this one.
    - `code_verifier` is PKCE: the authorization request carries only its hash,
      so a code intercepted in transit cannot be exchanged without it.
    """
    document = discovery()
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    session[SESSION_STATE] = secrets.token_urlsafe(32)
    session[SESSION_NONCE] = secrets.token_urlsafe(32)
    session[SESSION_VERIFIER] = verifier
    session[SESSION_STARTED] = int(time.time())
    session[SESSION_NEXT] = next_url or ""

    query = {
        "response_type": "code",
        "client_id": _env(ENV_CLIENT_ID),
        "redirect_uri": _redirect_uri(),
        "scope": _env(ENV_SCOPES, DEFAULT_SCOPES),
        "state": session[SESSION_STATE],
        "nonce": session[SESSION_NONCE],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{document['authorization_endpoint']}?{urlencode(query)}"


def complete_login(session: dict[str, Any], code: str, state: str) -> OidcIdentity:
    """Finish sign-in and return the verified identity.

    Every check here is load-bearing; none of them is defence in depth.
    """
    expected_state = session.pop(SESSION_STATE, None)
    nonce = session.pop(SESSION_NONCE, None)
    verifier = session.pop(SESSION_VERIFIER, None)
    started = session.pop(SESSION_STARTED, 0)

    if not expected_state or not verifier:
        raise OidcError("This sign-in did not start here. Please try again.")
    # secrets.compare_digest, not ==, because the comparison is against a value
    # an attacker supplies.
    if not state or not secrets.compare_digest(str(state), str(expected_state)):
        raise OidcError("This sign-in could not be verified. Please try again.")
    if int(time.time()) - int(started or 0) > HANDSHAKE_TIMEOUT_SECONDS:
        raise OidcError("This sign-in took too long. Please try again.")
    if not code:
        raise OidcError("The identity provider did not return an authorization code.")

    claims = _exchange_and_verify(code, verifier, str(nonce or ""))
    return _identity_from_claims(claims)


def _exchange_and_verify(code: str, verifier: str, nonce: str) -> dict[str, Any]:
    document = discovery()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
        "client_id": _env(ENV_CLIENT_ID),
        "code_verifier": verifier,
    }
    secret = _env(ENV_CLIENT_SECRET)
    if secret:
        data["client_secret"] = secret

    try:
        response = requests.post(
            document["token_endpoint"], data=data, timeout=20, verify=_verify_tls()
        )
    except Exception as exc:  # noqa: BLE001
        raise OidcError("Could not reach the identity provider.") from exc

    if response.status_code != 200:
        # The provider's own error text can name the client or the redirect URI,
        # so it is logged for the operator and not shown to the browser.
        PrintStyle.error(f"OIDC token exchange failed: {response.status_code} {response.text}")
        raise OidcError("The identity provider rejected this sign-in.")

    id_token = response.json().get("id_token")
    if not id_token:
        raise OidcError("The identity provider returned no identity token.")

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            # Only what the provider advertises. Never "none", and never a
            # symmetric algorithm, which a caller could forge with the client
            # secret.
            algorithms=[
                alg
                for alg in discovery().get("id_token_signing_alg_values_supported", ["RS256"])
                if alg.startswith(("RS", "ES", "PS"))
            ],
            audience=_env(ENV_CLIENT_ID),
            issuer=discovery()["issuer"],
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        PrintStyle.error(f"OIDC identity token rejected: {exc}")
        raise OidcError("The identity token could not be verified.") from exc

    if nonce and claims.get("nonce") != nonce:
        raise OidcError("The identity token does not match this sign-in.")
    return claims


def _redirect_uri() -> str:
    uri = _env(ENV_REDIRECT_URI)
    if not uri:
        raise OidcError(f"{ENV_REDIRECT_URI} is not configured.")
    return uri


# ── claims to identity ───────────────────────────────────────────────


def role_map() -> dict[str, str]:
    """Group name to role, as `group=role,group=role`.

    Group names are compared case-insensitively and by their last path segment,
    because providers disagree about whether a group is `eagent-admins`,
    `/eagent-admins` or a full LDAP distinguished name.
    """
    mapping: dict[str, str] = {}
    for pair in _env(ENV_ROLE_MAP).split(","):
        if "=" not in pair:
            continue
        group, _, role = pair.partition("=")
        group, role = group.strip(), role.strip()
        if group and role:
            mapping[_normalize_group(group)] = rbac.normalize_role(role)
    return mapping


def _normalize_group(group: str) -> str:
    return group.strip().strip("/").split("/")[-1].lower()


def resolve_role(groups: list[str]) -> str:
    """The strongest role any of the user's groups grants.

    Strongest rather than first, so group ordering -- which no provider
    guarantees -- cannot decide someone's privileges. Empty when nothing maps,
    which the caller must treat as a refusal.
    """
    mapping = role_map()
    matched = {mapping[_normalize_group(g)] for g in groups if _normalize_group(g) in mapping}
    for role in (rbac.ROLE_ADMIN, rbac.ROLE_POWER, rbac.ROLE_USER):
        if role in matched:
            return role
    return ""


def _identity_from_claims(claims: dict[str, Any]) -> OidcIdentity:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise OidcError("The identity token carries no subject.")

    raw_groups = claims.get(_env(ENV_GROUPS_CLAIM, DEFAULT_GROUPS_CLAIM)) or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups = tuple(str(g) for g in raw_groups)

    role = resolve_role(list(groups))
    if not role:
        # Deliberately not a default role. An identity the directory has not
        # placed in a mapped group is one this deployment has no instruction
        # about, and admitting it at low privilege is still admitting it.
        raise OidcError(
            "Your account is not a member of a group that grants access to "
            "Rezztor EAgent. Contact your administrator."
        )

    email = str(claims.get("email") or "")
    username = str(
        claims.get("preferred_username") or claims.get("email") or subject
    ).strip()
    display = str(claims.get("name") or username)

    return OidcIdentity(
        subject=subject,
        username=username,
        email=email,
        display_name=display,
        groups=groups,
        role=role,
    )


def reset_caches() -> None:
    """Drop discovery and key caches. For tests and for a provider rotation."""
    _discovery_cache.clear()
    _jwk_clients.clear()

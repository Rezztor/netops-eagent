# oidc.py DOX

## Purpose

- Own the `oidc.py` helper module.
- This module implements corporate sign-in via OpenID Connect Authorization Code with PKCE, provisioning users just-in-time into the existing local account store and mapping identity-provider group membership to a role on every login.
- Keep this file-level DOX profile synchronized with `oidc.py` because this directory is intentionally flat.

## Ownership

- `oidc.py` owns the runtime implementation.
- `oidc.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `OidcError` (`RuntimeError`) — sign-in could not be completed; the message reaches the login page and never names which account exists or which group was missing.
- Classes:
- `OidcIdentity` (`@dataclass(frozen=True)`) — `subject`, `username`, `email`, `display_name`, `groups`, `role`.
- Top-level functions:
  - `is_configured() -> bool`: true when issuer and client id are set; local accounts remain the path otherwise.
  - `discovery() -> dict[str, Any]`: fetch and cache the provider's `.well-known/openid-configuration`; verifies the returned `issuer` matches configuration.
  - `begin_login(session: dict, next_url: str = "") -> str`: mint `state`/`nonce`/PKCE `code_verifier`, store them server-side in the session, and return the authorization URL.
  - `complete_login(session: dict, code: str, state: str) -> OidcIdentity`: verify the callback and return the resolved identity.
  - `_exchange_and_verify(code, verifier, nonce) -> dict[str, Any]`: token exchange plus ID-token signature/claims verification.
  - `role_map() -> dict[str, str]`: parse `REZZTOR_OIDC_ROLE_MAP` (`group=role,...`) into a normalized group-name -> role mapping.
  - `resolve_role(groups: list[str]) -> str`: the **strongest** role any of the user's groups grants (not the first match, since provider group ordering is not guaranteed); empty string when nothing maps.
  - `_identity_from_claims(claims) -> OidcIdentity`: build the verified identity, raising `OidcError` if no group maps to a role.
  - `reset_caches() -> None`: clear discovery/JWKS caches, for tests and provider rotation.
- Constants: `ENV_ISSUER`, `ENV_CLIENT_ID`, `ENV_CLIENT_SECRET`, `ENV_REDIRECT_URI`, `ENV_SCOPES`, `ENV_GROUPS_CLAIM`, `ENV_ROLE_MAP`, `ENV_VERIFY_TLS`, `DEFAULT_SCOPES`, `DEFAULT_GROUPS_CLAIM`, `SESSION_STATE`, `SESSION_NONCE`, `SESSION_VERIFIER`, `SESSION_NEXT`, `SESSION_STARTED`, `HANDSHAKE_TIMEOUT_SECONDS`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Identity is the `sub` claim, never the email address — email is mutable and reassignable at some providers, so keying users on it is how one employee could inherit another's history. `helpers/users.py:upsert_federated_user` matches on `(issuer, subject)`.
- **A variable that is set but empty counts as unset.** `_env` falls back to its default on a blank value, because a compose file declares the whole `REZZTOR_OIDC_*` block and passes the unused half through as empty strings. Without it, `is_configured()` would be true for a deployment that set only the block, `REZZTOR_OIDC_GROUPS_CLAIM` would read a claim named `""`, and every identity would be refused for belonging to no mapped group — which reads like a directory misconfiguration and is not one.
- Roles are recomputed from group membership on **every** login and are never left at a stale value; a user removed from a mapped group is refused at their next sign-in rather than silently kept at their old role.
- A user in no mapped group is refused (`OidcError`), never admitted at a default/lowest role — silently admitting an unmapped identity at low privilege is still admitting it.
- `complete_login` checks, in order: a `state`/`verifier` pair actually exists in the session, `state` matches via `secrets.compare_digest` (constant-time, since it is compared against attacker-supplied input), the handshake has not exceeded `HANDSHAKE_TIMEOUT_SECONDS` (600s), and a `code` was returned — every check is load-bearing, not defence in depth.
- ID-token verification restricts `algorithms` to what the provider itself advertises and only asymmetric families (`RS`/`ES`/`PS` prefixes) — never `none` and never a symmetric algorithm a caller could forge with the client secret.
- The discovery document's own `issuer` field is checked against the configured issuer; a mismatch raises rather than trusting the fetched document, which would otherwise let a redirect point the app at a different provider.
- Observed side-effect areas: network calls (discovery, token exchange, JWKS fetch), settings/state persistence (session keys), secret handling (client secret, PKCE verifier).
- Imported dependency areas include: `base64`, `hashlib`, `os`, `secrets`, `time`, `dataclasses`, `typing`, `urllib.parse`, `jwt`, `requests`, `jwt.PyJWKClient`, `helpers.rbac`, `helpers.print_style`.

## Key Concepts

- Important called helpers/classes observed in the source: `requests.get`, `requests.post`, `PyJWKClient.get_signing_key_from_jwt`, `jwt.decode`, `secrets.token_urlsafe`, `secrets.compare_digest`, `hashlib.sha256`, `rbac.normalize_role`.
- `resolve_role` iterates roles strongest-to-weakest (`ROLE_ADMIN`, `ROLE_POWER`, `ROLE_USER`) and returns the first that appears in the matched set, so group ordering from the provider cannot decide privilege level.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Any change to claim verification (algorithms, audience, issuer, required claims) needs security review; do not loosen `options={"require": [...]}` without understanding what stops being checked.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_oidc.py`

## Child DOX Index

No child DOX files.

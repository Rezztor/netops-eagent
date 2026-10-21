"""Corporate sign-in over OpenID Connect.

Verified end to end against a real Keycloak in the kind cluster before these
were written: four users in three groups signed in through the provider's own
login form, their roles resolved from group membership, a user in no mapped
group was refused, a promotion in the directory took effect at the next sign-in,
and a forged callback was rejected. ADR 0021 records the runs.

What follows covers the parts a pull request can break silently. The security
checks in the handshake are the point -- each one is load-bearing, none is
defence in depth -- so each is asserted separately rather than through one happy
path that would still pass with any single one removed.
"""

from __future__ import annotations

import time

import pytest

from helpers import oidc


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    oidc.reset_caches()
    for name in (
        oidc.ENV_ISSUER, oidc.ENV_CLIENT_ID, oidc.ENV_CLIENT_SECRET,
        oidc.ENV_REDIRECT_URI, oidc.ENV_ROLE_MAP, oidc.ENV_GROUPS_CLAIM,
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    oidc.reset_caches()


def _configure(monkeypatch, **extra):
    monkeypatch.setenv(oidc.ENV_ISSUER, "https://idp.example/realms/r")
    monkeypatch.setenv(oidc.ENV_CLIENT_ID, "eagent")
    monkeypatch.setenv(oidc.ENV_REDIRECT_URI, "https://eagent.example/auth/callback")
    monkeypatch.setenv(
        oidc.ENV_ROLE_MAP, "eagent-admins=admin,eagent-power=power,eagent-users=user"
    )
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


# ── configuration ────────────────────────────────────────────────────


def test_local_accounts_remain_the_path_when_no_provider_is_configured():
    """Development, and the break-glass account during an IdP outage."""
    assert oidc.is_configured() is False


def test_a_provider_is_only_configured_when_both_halves_are_present(monkeypatch):
    monkeypatch.setenv(oidc.ENV_ISSUER, "https://idp.example")
    assert oidc.is_configured() is False, "an issuer with no client id is not a provider"


def test_a_variable_declared_empty_counts_as_unconfigured(monkeypatch):
    """A compose file passes the whole block through, so the unused half of it
    arrives as an empty string rather than as an absent name."""
    monkeypatch.setenv(oidc.ENV_ISSUER, "")
    monkeypatch.setenv(oidc.ENV_CLIENT_ID, "  ")
    assert oidc.is_configured() is False


def test_an_empty_variable_falls_back_to_its_default(monkeypatch):
    """The one that bites: an empty groups claim reads no claim at all, so
    every identity belongs to no mapped group and every sign-in is refused --
    which looks exactly like a directory misconfiguration and is not one."""
    _configure(monkeypatch, **{oidc.ENV_GROUPS_CLAIM: "", oidc.ENV_SCOPES: ""})
    identity = oidc._identity_from_claims({"sub": "s", "groups": ["eagent-admins"]})
    assert identity.role == "admin"
    assert oidc._env(oidc.ENV_SCOPES, oidc.DEFAULT_SCOPES) == oidc.DEFAULT_SCOPES


# ── group to role ────────────────────────────────────────────────────


def test_roles_come_from_group_membership(monkeypatch):
    _configure(monkeypatch)
    assert oidc.resolve_role(["eagent-admins"]) == "admin"
    assert oidc.resolve_role(["eagent-power"]) == "power"
    assert oidc.resolve_role(["eagent-users"]) == "user"


def test_group_names_are_compared_by_their_last_segment(monkeypatch):
    """Providers disagree about the shape of a group name.

    Keycloak sends `/eagent-admins`, others send a bare name, and some send a
    full LDAP distinguished name. Matching the last segment case-insensitively
    means the same role map works across providers instead of needing a
    different one per vendor.
    """
    _configure(monkeypatch)
    for form in ("/eagent-admins", "eagent-admins", "EAgent-Admins", "/parent/eagent-admins"):
        assert oidc.resolve_role([form]) == "admin", form


def test_the_strongest_matching_role_wins(monkeypatch):
    """Not the first match.

    No provider guarantees the ordering of a groups claim, so resolving by
    position would make someone's privileges depend on an implementation detail
    that can change between releases.
    """
    _configure(monkeypatch)
    assert oidc.resolve_role(["eagent-users", "eagent-admins"]) == "admin"
    assert oidc.resolve_role(["eagent-admins", "eagent-users"]) == "admin"


def test_an_unmapped_identity_is_refused_rather_than_given_a_default(monkeypatch):
    """The deployment has no instruction about this person.

    Admitting them at the lowest privilege is still admitting them, and `user`
    can read and write conversations and memory. Verified against Keycloak with
    a real account in no mapped group.
    """
    _configure(monkeypatch)
    assert oidc.resolve_role(["some-other-team"]) == ""

    with pytest.raises(oidc.OidcError, match="not a member of a group"):
        oidc._identity_from_claims({"sub": "abc", "groups": ["some-other-team"]})


# ── claims ───────────────────────────────────────────────────────────


def test_identity_is_the_subject_claim_never_the_email(monkeypatch):
    """Email is mutable and, at some providers, reassignable to a new employee.

    Keying on it is how one person silently inherits another's conversations,
    files and memory.
    """
    _configure(monkeypatch)
    identity = oidc._identity_from_claims(
        {"sub": "stable-id-1", "email": "a@x.example", "groups": ["eagent-users"]}
    )
    assert identity.subject == "stable-id-1"


def test_a_token_with_no_subject_is_refused(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(oidc.OidcError, match="no subject"):
        oidc._identity_from_claims({"groups": ["eagent-admins"]})


def test_a_single_group_sent_as_a_string_is_handled(monkeypatch):
    """Some providers send one group as a bare string rather than a list. Left
    unhandled, it iterates as characters and matches nothing."""
    _configure(monkeypatch)
    identity = oidc._identity_from_claims({"sub": "s", "groups": "eagent-admins"})
    assert identity.role == "admin"


def test_the_groups_claim_name_is_configurable(monkeypatch):
    """Entra ID, Okta and Keycloak do not agree on where groups live."""
    _configure(monkeypatch, **{oidc.ENV_GROUPS_CLAIM: "roles"})
    identity = oidc._identity_from_claims({"sub": "s", "roles": ["eagent-power"]})
    assert identity.role == "power"


# ── the handshake ────────────────────────────────────────────────────


def _begin(monkeypatch, session):
    _configure(monkeypatch)
    monkeypatch.setattr(
        oidc,
        "discovery",
        lambda: {
            "issuer": "https://idp.example/realms/r",
            "authorization_endpoint": "https://idp.example/auth",
            "token_endpoint": "https://idp.example/token",
            "jwks_uri": "https://idp.example/certs",
        },
    )
    return oidc.begin_login(session, "/app/chat")


def test_the_authorization_request_carries_state_nonce_and_pkce(monkeypatch):
    session: dict = {}
    url = _begin(monkeypatch, session)
    for parameter in ("state=", "nonce=", "code_challenge=", "code_challenge_method=S256"):
        assert parameter in url, parameter
    # The verifier stays here and never goes on the wire.
    assert session[oidc.SESSION_VERIFIER] not in url


def test_a_callback_that_did_not_start_here_is_refused(monkeypatch):
    """The defence against an attacker feeding us their own authorization code
    and having the session adopt their identity."""
    _configure(monkeypatch)
    with pytest.raises(oidc.OidcError, match="did not start here"):
        oidc.complete_login({}, "some-code", "some-state")


def test_a_mismatched_state_is_refused(monkeypatch):
    session: dict = {}
    _begin(monkeypatch, session)
    with pytest.raises(oidc.OidcError, match="could not be verified"):
        oidc.complete_login(session, "code", "not-the-state")


def test_an_abandoned_handshake_expires(monkeypatch):
    session: dict = {}
    _begin(monkeypatch, session)
    session[oidc.SESSION_STARTED] = int(time.time()) - oidc.HANDSHAKE_TIMEOUT_SECONDS - 1
    with pytest.raises(oidc.OidcError, match="took too long"):
        oidc.complete_login(session, "code", session[oidc.SESSION_STATE])


def test_the_handshake_is_single_use(monkeypatch):
    """State, nonce and verifier are consumed on the first attempt.

    Without this a captured callback URL could be replayed against the same
    still-valid session.
    """
    session: dict = {}
    _begin(monkeypatch, session)
    state = session[oidc.SESSION_STATE]
    with pytest.raises(oidc.OidcError):
        oidc.complete_login(session, "", state)  # fails at the empty code
    assert oidc.SESSION_STATE not in session
    assert oidc.SESSION_VERIFIER not in session


def test_a_provider_whose_issuer_disagrees_with_its_url_is_refused(monkeypatch):
    """A discovery document is fetched over the network. If the issuer inside it
    does not match where it came from, the document is not authoritative for
    that issuer and following its endpoints would send credentials elsewhere."""
    _configure(monkeypatch)

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"issuer": "https://attacker.example"}

    monkeypatch.setattr(oidc.requests, "get", lambda *a, **k: _Response())
    with pytest.raises(oidc.OidcError, match="issuer does not match"):
        oidc.discovery()


# ── provisioning ─────────────────────────────────────────────────────


def test_a_federated_account_cannot_be_reached_with_a_password(database, tmp_path, monkeypatch):
    """It has no password of its own, and must not acquire one.

    If it could be signed into locally, the identity provider would stop being
    the authority -- which is the entire point of federating.
    """
    from helpers import files, users

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)

    record = users.upsert_federated_user(
        subject="sub-1", username="alice", email="alice@x.example",
        display_name="Alice", role="admin", issuer="https://idp.example",
    )
    assert record["provider"] == users.PROVIDER_OIDC
    assert users.authenticate("alice", "") is None
    assert users.authenticate("alice", "anything") is None


def test_signing_in_twice_does_not_create_a_second_account(database, tmp_path, monkeypatch):
    from helpers import files, users

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    first = users.upsert_federated_user(
        subject="sub-1", username="alice", email="a@x", display_name="A",
        role="user", issuer="https://idp.example",
    )
    second = users.upsert_federated_user(
        subject="sub-1", username="alice-renamed", email="new@x", display_name="A",
        role="user", issuer="https://idp.example",
    )
    assert first["id"] == second["id"], "a rename created a second account"
    assert len(users.list_users()) == 1


def test_the_role_is_rewritten_from_the_directory_on_every_sign_in(database, tmp_path, monkeypatch):
    """Verified against Keycloak: promoting a user into eagent-admins made them
    an admin at their next sign-in, and removing them demoted them again,
    without anyone touching this system."""
    from helpers import files, users

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    args = dict(subject="s", username="carol", email="c@x", display_name="C",
                issuer="https://idp.example")
    assert users.upsert_federated_user(role="user", **args)["role"] == "user"
    assert users.upsert_federated_user(role="admin", **args)["role"] == "admin"
    assert users.upsert_federated_user(role="user", **args)["role"] == "user"


def test_a_local_suspension_survives_a_successful_sign_in(database, tmp_path, monkeypatch):
    """An administrator who disables a federated account here has made a
    decision the provider does not know about. A successful sign-in must not
    quietly undo it."""
    from helpers import files, users

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    args = dict(subject="s", username="mallory", email="m@x", display_name="M",
                role="user", issuer="https://idp.example")
    record = users.upsert_federated_user(**args)
    users.update_user(record["username"], disabled=True)
    assert users.upsert_federated_user(**args)["disabled"] is True


def test_two_providers_issuing_the_same_subject_are_different_people(database, tmp_path, monkeypatch):
    """`sub` is unique per issuer, not globally. Keying on it alone would let a
    second, less trusted provider mint an identity that collides with a real
    one."""
    from helpers import files, users

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    common = dict(subject="1", username="same", email="", display_name="", role="user")
    a = users.upsert_federated_user(issuer="https://idp-a.example", **common)
    b = users.upsert_federated_user(issuer="https://idp-b.example", **common)
    assert a["id"] != b["id"]
    assert a["username"] != b["username"], "the second account shadowed the first"

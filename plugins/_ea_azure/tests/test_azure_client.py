"""The client against the stand-in: sign-in both ways, the token cache, paging, throttling, and the proof that
this module can only read: three calls, no other address, no other verb. Nothing here reaches Microsoft."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from helpers import dotenv
from plugins._ea_azure.helpers import client, config, credential, queries
from plugins._ea_azure.tests.fake_azure import CLIENT_ID, TENANT_ID, FakeAzure, generated_tenant, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install

PLUGIN = Path(__file__).resolve().parents[1]
SECRET = "fake-client-secret"


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """One RSA 3072 certificate for the whole module: (stored blob, public PEM)."""
    from _pytest.monkeypatch import MonkeyPatch

    with MonkeyPatch.context() as mp:
        env = install(mp, tmp_path_factory.mktemp("cert"))
        config.save({"tenant_id": TENANT_ID})
        credential.generate_certificate()
        return env[dotenv.KEY_AZURE_CLIENT_CERT], credential.public_certificate()


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID, "page_size": 100})
    credential.set_secret(SECRET, "")
    return store


@pytest.fixture
def azure(env, monkeypatch):
    sleeps = []
    monkeypatch.setattr(client, "_sleep", sleeps.append)
    client.reset()
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        fake.sleeps = sleeps
        yield fake
    client.reset()


def _use_certificate(env, azure, certificate, register=True):
    env[dotenv.KEY_AZURE_CLIENT_CERT], env[dotenv.KEY_AZURE_CLIENT_SECRET] = certificate[0], ""
    if register:
        azure.register_certificate(certificate[1])


def _pages(query, ids=None):
    return list(client.graph(query, ids))


# --- sign in -------------------------------------------------------------------------------------------------------

def test_a_secret_signs_in_and_the_token_works(azure):
    assert client.token().startswith("fake-token-")
    form = azure.received[0]["form"]
    assert form["grant_type"] == "client_credentials" and form["scope"] == f"{azure.url}/.default"
    assert len(client.subscriptions()) == 3


def test_a_certificate_signs_in_with_a_ps256_assertion(azure, env, certificate):
    _use_certificate(env, azure, certificate)
    assert client.token()
    form = azure.received[0]["form"]
    assert "client_secret" not in form and form["client_assertion_type"].endswith("jwt-bearer")


def test_a_certificate_that_was_never_uploaded_is_refused(azure, env, certificate):
    _use_certificate(env, azure, certificate, register=False)
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "certificate" and "AADSTS700027" in str(raised.value)


def test_the_token_is_cached_then_refreshed_before_it_expires(azure, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(client, "_clock", lambda: now[0])
    first = client.token()
    now[0] += 3000  # one hour is 3599 s; five minutes are kept back
    assert client.token() == first and azure._token_n == 1
    now[0] += 400
    assert client.token() != first and azure._token_n == 2


def test_changing_the_credential_signs_in_again(azure, env):
    client.token()
    credential.set_secret("a-different-secret", "")
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "secret"


@pytest.mark.parametrize("switch", ["tenant", "application", "secret", "expired", "certificate"])
def test_each_sign_in_failure_gives_its_reason_and_leaks_nothing(azure, switch):
    azure.sign_in_error = switch
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    text = str(raised.value)
    assert raised.value.reason == switch and raised.value.code and f"AADSTS{raised.value.code}" in text
    assert SECRET not in text and "simulated" not in text  # Azure's own words are never passed on


def test_unknown_tenant_and_application_are_told_apart(azure):
    config.save({"tenant_id": "00000000-0000-4000-8000-000000000000"})
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "tenant"
    config.save({"tenant_id": TENANT_ID, "client_id": "someone-else"})
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "application"


def test_an_unreachable_microsoft_is_a_network_failure(azure, monkeypatch):
    monkeypatch.setattr(client, "TEST_ENDPOINTS", client.Cloud("http://127.0.0.1:9", "http://127.0.0.1:9"))
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "network"


def test_a_garbled_answer_is_a_plain_failure(azure):
    azure.malformed = True
    with pytest.raises(client.SignInFailed) as raised:
        client.token()
    assert raised.value.reason == "other"


def test_without_ids_or_a_credential_nothing_is_sent(azure):
    credential.clear()
    with pytest.raises(client.NotConfigured):
        client.token()
    assert azure.received == []


# --- reading -------------------------------------------------------------------------------------------------------

def test_the_subscription_list_has_only_what_is_readable(azure):
    assert {s["name"] for s in client.subscriptions()} == {"Production", "Non-production", "Shared services"}
    azure.hide_subscription = {"22222222-2222-4222-8222-222222222222"}
    listed = client.subscriptions()
    assert len(listed) == 2 and set(listed[0]) == {"id", "name", "state", "tenant_id"}


def test_a_next_link_on_another_host_is_not_followed(azure):
    azure.evil_next_link = True
    with pytest.raises(client.ReadFailed):
        client.subscriptions()
    assert all(r["path"] in ("/subscriptions", f"/{TENANT_ID}/oauth2/v2.0/token") for r in azure.received)


def test_graph_pages_to_the_end_and_scopes_to_the_subscriptions(azure):
    azure.tenant = generated_tenant(250, 2)
    pages = _pages(queries.RESOURCES)
    assert [len(p) for p in pages] == [100, 100, 50]
    assert len({r["id"] for p in pages for r in p}) == 250
    one = azure.tenant["subscriptions"][0]["subscriptionId"]
    assert sum(len(p) for p in _pages(queries.RESOURCES, [one])) == 125
    body = azure.received[-1]["raw"]
    assert b'"subscriptions": ["' + one.encode() in body and b"resultFormat" in body and queries.RESOURCES.encode() in body
    assert _pages(queries.RESOURCES, []) == []  # an empty scope reads nothing, it does not mean "everything"


def test_the_count_query_answers_a_number(azure):
    rows = [r for p in _pages(queries.COUNT) for r in p]
    assert rows == [{"total": len(azure.tenant["resources"])}]


def test_a_zero_quota_makes_it_wait_for_the_reset_but_not_after_the_last_page(azure):
    azure.tenant = generated_tenant(250, 1)
    azure.quota = (0, "00:00:02")
    _pages(queries.RESOURCES)
    assert azure.sleeps == [2, 2]


def test_a_429_is_waited_out_and_retried(azure):
    azure.throttle_after, azure.throttle_count = 0, 1
    assert _pages(queries.COUNT)
    assert azure.sleeps == [1] and azure._graph_n == 2


def test_a_429_that_will_not_stop_ends_in_throttled_after_three_tries(azure):
    azure.throttle_after, azure.throttle_count = 0, 99
    with pytest.raises(client.Throttled) as raised:
        _pages(queries.COUNT)
    assert raised.value.status == 429 and isinstance(raised.value, client.ReadFailed)
    assert azure._graph_n == 3 and len(azure.sleeps) == 2


def test_a_truncated_answer_halves_the_page_size_and_loses_no_row(azure):
    azure.tenant = generated_tenant(250, 1)
    config.save({"page_size": 400})
    azure.truncate = 150
    pages = _pages(queries.RESOURCES)
    tops = [json.loads(r["raw"])["options"]["$top"] for r in azure.received if r["path"].endswith("/resources")]
    assert tops[:3] == [400, 200, 100] and set(tops[2:]) == {100}
    assert len({r["id"] for p in pages for r in p}) == 250


def test_a_page_that_cannot_be_made_small_enough_fails_the_read(azure):
    azure.truncate = 50
    with pytest.raises(client.ReadFailed):
        _pages(queries.RESOURCES)


def test_a_failed_page_names_its_subscription(azure):
    sub = azure.tenant["subscriptions"][0]["subscriptionId"]
    azure.fail_page = (sub, 1, 500)
    with pytest.raises(client.ReadFailed) as raised:
        _pages(queries.RESOURCES, [sub])
    assert raised.value.status == 500 and raised.value.subscription == sub


def test_a_garbled_graph_answer_is_a_read_failure(azure):
    client.token()
    azure.malformed = True
    with pytest.raises(client.ReadFailed):
        _pages(queries.COUNT)


# --- the stand-in switch --------------------------------------------------------------------------------------------

def test_test_endpoints_are_accepted_only_on_loopback_or_a_bare_host_name():
    ok = client._test_endpoints("login=http://127.0.0.1:8099;arm=http://127.0.0.1:8099")
    assert ok == client.Cloud("http://127.0.0.1:8099", "http://127.0.0.1:8099")
    assert client._test_endpoints("login=http://fake-azure:8099/;arm=http://fake-azure:8099") .login == "http://fake-azure:8099"
    for bad in ("login=https://login.microsoftonline.com;arm=http://127.0.0.1:1", "login=http://127.0.0.1:1;arm=https://evil.example.com",
                "login=http://127.0.0.1:1", "garbage", "login=http://10.0.0.5:1;arm=http://10.0.0.5:1", ""):
        assert client._test_endpoints(bad) is None


def test_the_real_hosts_are_what_a_normal_run_uses(env):
    assert client.TEST_ENDPOINTS is None
    assert client._cloud() == client.CLOUDS["public"]
    assert client.CLOUDS["public"] == client.Cloud("https://login.microsoftonline.com", "https://management.azure.com")


# --- read-only by construction -------------------------------------------------------------------------------------

def test_the_module_makes_only_a_get_and_a_post():
    source = (PLUGIN / "helpers" / "client.py").read_text()
    assert sorted(set(re.findall(r"\brequests\.(\w+)\(", source))) == ["get", "post"]
    assert not re.search(r"\b(put|patch|delete|head|options|request|Session)\b\s*\(", source.replace("def ", ""), re.I)
    assert not re.search(r"""["'](PUT|PATCH|DELETE)["']""", source)
    assert "verify=" not in source and "allow_redirects=False" in source


def test_the_only_addresses_are_the_three_known_ones():
    source = (PLUGIN / "helpers" / "client.py").read_text()
    assert "/list" not in source.lower()
    assert len(re.findall(r'url = f"[^"]*/oauth2/v2.0/token"', source)) == 1
    assert [source.count(part) for part in ("/subscriptions?api-version=", "/providers/Microsoft.ResourceGraph/resources?")] == [1, 1]
    assert re.findall(r"api-version=\{(\w+)\}", source) == ["SUBSCRIPTIONS_API", "GRAPH_API"]
    assert len(re.findall(r"https?://", source)) == 2  # the two public hosts of CLOUDS, and nothing else


def test_no_function_takes_a_url_a_path_or_a_method_from_a_caller():
    import inspect

    public = {n: f for n, f in vars(client).items() if inspect.isfunction(f) and not n.startswith("_") and f.__module__ == client.__name__}
    assert set(public) == {"reset", "token", "subscriptions", "graph"}
    assert list(inspect.signature(client.graph).parameters) == ["query", "subscription_ids"]
    assert not inspect.signature(client.token).parameters and not inspect.signature(client.subscriptions).parameters


def _constant(argument: str, source: str) -> bool:
    """A `queries.X` constant, or a parameter whose every caller in the same file passes one."""
    if re.fullmatch(r"queries\.[A-Z_]+", argument):
        return True
    wrapper = re.search(rf"def (\w+)\(\s*{argument}\b", source)
    calls = re.findall(rf"(?<!def )\b{wrapper.group(1)}\(\s*([^,)\s]+)", source) if wrapper else []
    return bool(calls) and all(re.fullmatch(r"queries\.[A-Z_]+", c) for c in calls)


def test_every_graph_call_site_passes_a_constant_from_queries():
    sites = 0
    for path in [*(PLUGIN / "helpers").glob("*.py"), *(PLUGIN / "api").glob("*.py")]:
        source = path.read_text()
        if path.name == "client.py":
            continue
        for argument in re.findall(r"\bclient\.graph\(\s*([^,)\s]+)", source):
            sites += 1
            assert _constant(argument, source), f"{path.name}: client.graph({argument}"
    assert sites >= 1
    for name in ("CONTAINERS", "RESOURCES", "SITE_CONFIG", "COUNT"):
        assert isinstance(getattr(queries, name), str)


def test_a_whole_pull_reaches_only_the_three_routes(azure):
    client.subscriptions()
    for query in (queries.CONTAINERS, queries.RESOURCES, queries.SITE_CONFIG, queries.COUNT):
        _pages(query)
    assert {(r["method"], r["path"]) for r in azure.received} == {
        ("POST", f"/{TENANT_ID}/oauth2/v2.0/token"), ("GET", "/subscriptions"), ("POST", "/providers/Microsoft.ResourceGraph/resources"),
    }
    assert all(b"Bearer" not in str(r["raw"]).encode() for r in azure.received)


def test_a_token_never_appears_in_an_error(azure):
    token = client.token()
    azure.tokens.clear()  # Azure forgets it: every authorised call now answers 401
    with pytest.raises(client.ReadFailed) as raised:
        client.subscriptions()
    assert token not in str(raised.value) and raised.value.status == 401
    assert base64.b64encode(SECRET.encode()).decode() not in str(raised.value)


# --- the stand-in itself --------------------------------------------------------------------------------------------

def test_the_stand_in_answers_only_three_routes_and_the_control_route_only_when_asked():
    import requests

    with FakeAzure(hand_tenant()) as fake:
        for method in ("get", "post", "put", "patch", "delete"):
            assert getattr(requests, method)(f"{fake.url}/_control", timeout=5).status_code == 404
        assert requests.get(f"{fake.url}/subscriptions/x/resources", timeout=5).status_code == 404
        assert len(fake.received) == 6
    with FakeAzure(hand_tenant(), control=True) as fake:
        count = len(fake.tenant["resources"])
        assert requests.post(f"{fake.url}/_control", json={"op": "remove", "name": "app-api-prod"}, timeout=5).status_code == 200
        assert len(fake.tenant["resources"]) == count - 1
        assert requests.post(f"{fake.url}/_control", json={"op": "nope"}, timeout=5).status_code == 400


def test_planted_secrets_are_planted_in_every_known_place():
    fake = FakeAzure(hand_tenant())
    markers = fake.plant_secrets()
    dump = json.dumps(fake.tenant)
    assert len(markers) == 7 and all(m in dump for m in markers)


def test_the_stand_in_refuses_a_sort_on_a_dropped_column_as_azure_does():
    from plugins._ea_azure.tests.fake_azure import _sorts_a_dropped_column

    assert _sorts_a_dropped_column("resourcechanges | extend t = now() | project id, name | order by t desc")
    for name in ("CONTAINERS", "RESOURCES", "SITE_CONFIG", "COUNT", "ADVISOR", "POLICY", "CHANGES", "HEALTH"):
        assert not _sorts_a_dropped_column(getattr(queries, name)), name

"""A local stand-in for Azure: Entra sign-in, the subscription list, Resource Graph, (phase 6) Cost Management and
(phase 5) the Log Analytics query route, and nothing else.

Those routes. Everything else is a 404, recorded in `received` so a test can see it. It verifies what a real
service verifies, as far as the plugin's own documentation says it does: a client secret, or a certificate
assertion (signature, `aud`, `iss`, `sub`, `exp` and `x5t#S256`). It vouches for nothing about Azure itself: the
answer shapes are the plugin's reading of Microsoft's documentation until phase 4.

In a test: `with FakeAzure(hand_tenant()) as fake:` and point `client.TEST_ENDPOINTS` at `fake.cloud`.
As a process (the coordinator's run): `PYTHONPATH=. .venv/bin/python plugins/_ea_azure/tests/fake_azure.py --port 8099 --control` and start
the app with `REZZTOR_AZURE_ENDPOINTS_FOR_TESTS="login=http://127.0.0.1:8099;arm=http://127.0.0.1:8099"`. `--control`
adds `POST /_control` ({"op": ...}) to register a certificate and change the data; tests leave it off.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from plugins._ea_azure.helpers.client import Cloud
from plugins._ea_azure.tests.fixtures.tenant import CLIENT_ID, TENANT_ID, generated_tenant, hand_tenant, row  # noqa: F401

# The log app (key D): its own application, its own certificate, its own token audience. A token for it opens the log
# route and nothing else; an Azure Resource Manager token opens everything but the log route.
LOG_CLIENT_ID = "c47a19e2-0000-4000-8000-5b0e3d9a7f64"
LOGS_SCOPE = "https://api.loganalytics.io/.default"

# Documented sign-in failures: switch name -> (AADSTS number, error). The numbers are the plugin's unverified map.
SIGN_IN_ERRORS = {
    "tenant": (90002, "invalid_request"), "application": (700016, "unauthorized_client"),
    "secret": (7000215, "invalid_client"), "expired": (7000222, "invalid_client"), "certificate": (700027, "invalid_client"),
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sorts_a_dropped_column(query: str) -> bool:
    """Azure refuses `order by c` after a `project` that dropped `c` (seen on the real tenant, 2026-10-03)."""
    kept = None
    for stage in (part.strip() for part in query.split("|")):
        low = stage.lower()
        if low.startswith("project "):
            kept = {c.strip().split("=")[0].strip().lower() for c in stage[len("project "):].split(",")}
        elif low.startswith("order by ") and kept is not None:
            column = low[len("order by "):].split()[0]
            if column not in kept:
                return True
    return False


def _query_kind(query: str) -> str | None:
    q = query.strip().lower()
    if q.startswith("resourcecontainers"):
        return "containers"
    if q.startswith("appserviceresources"):
        return "site_config"
    for table, kind in (("advisorresources", "advisor"), ("policyresources", "policy"), ("resourcechanges", "changes"),
                        ("healthresources", "health")):
        if q.startswith(table):
            return kind
    if q.startswith("resources | summarize total = count()"):
        return "count"
    return "resources" if q.startswith("resources |") else None


class FakeAzure:
    def __init__(self, tenant=None, *, secret="fake-client-secret", control=False, port=0):
        self.tenant = tenant if tenant is not None else hand_tenant()
        self.tenant_id, self.client_id, self.secret = self.tenant["tenant_id"], CLIENT_ID, secret
        self.control = control
        self._cert = None  # (public key, x5t#S256) of the registered certificate
        self._log_cert = None  # the same, for the log app
        self.log_client_id = LOG_CLIENT_ID
        self.tokens: set[str] = set()
        self.log_tokens: set[str] = set()
        self.expires_in = 3599
        # switches
        self.sign_in_error: str | None = None  # a key of SIGN_IN_ERRORS: every sign-in answers with it
        self.throttle_after: int | None = None  # graph calls answered normally before the 429s start
        self.throttle_count = 1  # how many 429s in a row
        self.quota = (12, "00:00:05")  # the two quota headers on every graph answer: (remaining, resets-after)
        self.fail_page: tuple[str, int, int] | None = None  # (subscription id, page number, status) for resource pages
        self.hide_subscription: set[str] = set()
        self.fail_kind: tuple[str, int] | None = None  # (a query kind: advisor, policy, changes, health, ...; the status it answers with)
        self.truncate: int | None = None  # a page asking for more than this many rows is answered truncated, cut to it
        self.delay = 0.0
        self.malformed = False
        self.evil_next_link = False  # the subscription list answers with a nextLink on another host
        # Cost Management (phase 6). The shapes are the plugin's reading of Microsoft's reference, not a recording.
        self.cost_lines: list[dict] = []  # {"sub", "rg", "resource", "service", "date" (a date), "cost", "currency", "tags": {}}
        self.forecast_days: dict[str, list[tuple[dt.date, float]]] = {}  # subscription id -> what its "Forecast" rows say; none and no history = 204
        self.budgets: dict[str, list] = {}  # subscription id -> budget items, in Microsoft's shape
        self.cost_alerts: dict[str, list] = {}
        self.agreements: list[str] = []  # agreementType of each billing account the application "sees"
        self.billing_status = 200
        self.cost_fail: dict[str, int] = {}  # a leaf (query, forecast, budgets, alerts) -> the status it answers with
        self.cost_throttle = 0  # how many cost calls are answered 429 before they work
        self.cost_page = 0  # rows per page of a query; 0 = all in one
        self.cost_evil_next_link = False
        self.cost_quota = ""  # the value of the qpu-remaining header, when set
        # Live named reads (phase 4): (METHOD, lower-cased path) -> (status, body, headers), or a list of them answered in
        # order (the last one repeats): 200, 202 with Location, 403, 429, a redirect. Nothing is registered by default.
        self.arm_answers: dict[tuple[str, str], tuple | list] = {}
        # Log Analytics (phase 5): workspace GUID (lower case) -> (status, body, headers), or a list answered in order.
        # The body is the plugin's reading of Microsoft's documented response format, not a recording.
        self.log_answers: dict[str, tuple | list] = {}
        # what it saw
        self.received: list[dict] = []
        self.graph_calls: list[float] = []  # time of each accepted Resource Graph call
        self._graph_n = 0
        self._throttled = 0
        self._page_no: dict[tuple, int] = {}
        self._token_n = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                url = urlsplit(self.path)
                entry = {"method": method, "path": url.path, "query": url.query, "raw": raw, "auth": self.headers.get("Authorization"),
                         "prefer": self.headers.get("Prefer")}
                fake.received.append(entry)
                time.sleep(fake.delay)
                status, payload, headers = fake.route(method, url, raw, self.headers, entry)
                if fake.malformed and status == 200:
                    status, payload = 200, "not json{"
                data = b"" if status == 204 else payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_PATCH(self):
                self._handle("PATCH")

            def do_DELETE(self):
                self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.cloud = Cloud(login=self.url, arm=self.url)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- what a test can do to it ---------------------------------------------------------------------------------

    def register_certificate(self, pem: bytes | str) -> None:
        """Upload a certificate's public file to the 'app registration'."""
        cert = x509.load_pem_x509_certificate(pem.encode() if isinstance(pem, str) else pem)
        der = cert.public_bytes(serialization.Encoding.DER)
        self._cert = (cert.public_key(), _b64url(hashlib.sha256(der).digest()))

    def register_log_certificate(self, pem: bytes | str) -> None:
        """Upload the log app's public certificate to its 'app registration'."""
        cert = x509.load_pem_x509_certificate(pem.encode() if isinstance(pem, str) else pem)
        self._log_cert = (cert.public_key(), _b64url(hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()))

    def rows(self, kind: str, subscriptions: list[str] | None = None) -> list[dict]:
        hidden = {s.lower() for s in self.hide_subscription}
        keep = {s.lower() for s in subscriptions} if subscriptions is not None else None
        out = [r for r in self.tenant.get(kind, [])
               if r["subscriptionId"].lower() not in hidden and (keep is None or r["subscriptionId"].lower() in keep)]
        return sorted(out, key=lambda r: r["id"].lower())

    def find(self, name: str) -> dict:
        return next(r for r in self.tenant["resources"] if r["name"] == name)

    def remove(self, name: str) -> None:
        self.tenant["resources"].remove(self.find(name))

    def set_property(self, name: str, path: str, value) -> None:
        """`path` is dotted from the row's `properties`: `routes.0.properties.nextHopIpAddress`."""
        node = self.find(name)["properties"]
        *head, last = path.split(".")
        for part in head:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[int(last) if isinstance(node, list) else last] = value

    def plant_secrets(self) -> list[str]:
        """Put a `PLANTED-SECRET-<n>` marker into every place a secret is known to turn up. Returns the markers."""
        markers: list[str] = []

        def mark() -> str:
            markers.append(f"PLANTED-SECRET-{len(markers) + 1}")
            return markers[-1]

        prod = self.tenant["subscriptions"][0]["subscriptionId"]
        vm = self.find("vm-prod-1")
        vm["properties"]["osProfile"]["customData"] = mark()
        extension = row(prod, vm["resourceGroup"], "Microsoft.Compute", "virtualMachines", "vm-prod-1/ext", properties={
            "provisioningState": "Succeeded", "settings": {"commandToExecute": mark()}})
        extension["type"] = "microsoft.compute/virtualmachines/extensions"
        self.find("app-portal-prod")["properties"]["siteConfig"] = {"appSettings": [{"name": "API_KEY", "value": mark()}]}
        group = row(prod, "rg-aks", "Microsoft.ContainerInstance", "containerGroups", "ci-batch", properties={"containers": [{
            "name": "c", "properties": {"environmentVariables": [{"name": "TOKEN", "value": mark()}]}}]})
        self.find("logic-orders")["properties"]["definition"] = {"actions": {"call": {"inputs": {"headers": {"x": mark()}}}}}
        self.find("stprodlogs001")["tags"]["db_password"] = mark()
        self.find("kv-prod-001")["tags"]["conn"] = f"Server=tcp:db.example.net;User Id=app;Password={mark()};"
        for extra in (extension, group):
            extra["id"] = extra["id"].lower()
            self.tenant["resources"].append(extra)
        return markers

    # --- the routes -----------------------------------------------------------------------------------------------

    def route(self, method, url, raw, headers, entry):
        path = url.path
        if method == "POST" and path.endswith("/oauth2/v2.0/token"):
            return self.sign_in(path, raw, entry)
        if method == "GET" and path == "/subscriptions":
            return self.subscription_list(url, headers)
        if method == "POST" and path == "/providers/Microsoft.ResourceGraph/resources":
            return self.resource_graph(raw, headers)
        cost = re.fullmatch(r"/subscriptions/([^/]+)(?:/resourceGroups/([^/]+))?/providers/Microsoft\.CostManagement/"
                            r"(query|forecast|budgets|alerts)", path, re.I)
        if cost:
            return self.cost_management(method, cost, url, raw, headers)
        if method == "GET" and path == "/providers/Microsoft.Billing/billingAccounts":
            if not self._authorised(headers):
                return 401, {"error": {"code": "InvalidAuthenticationToken"}}, {}
            if self.billing_status != 200:
                return self.billing_status, {"error": {"code": "Simulated"}}, {}
            return 200, {"value": [{"name": f"ba-{n}", "properties": {"agreementType": a, "displayName": "Contoso"}}
                                   for n, a in enumerate(self.agreements)]}, {}
        if self.control and method == "POST" and path == "/_control":
            return self.control_op(json.loads(raw or b"{}"))
        logs = re.fullmatch(r"/v1/workspaces/([^/]+)/query", path)
        if logs and method == "POST":
            return self.log_query(logs.group(1).lower(), raw, headers)
        if (method, path.lower()) in self.arm_answers:
            if not self._authorised(headers):
                return 401, {"error": {"code": "InvalidAuthenticationToken"}}, {}
            answer = self.arm_answers[(method, path.lower())]
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            return answer
        return 404, {"error": {"code": "NotFound", "message": f"{method} {path}"}}, {}

    def _fail(self, code: int, error: str, status=400):
        return status, {"error": error, "error_description": f"AADSTS{code}: simulated", "error_codes": [code]}, {}

    def sign_in(self, path, raw, entry):
        form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        entry["form"] = form
        if self.sign_in_error:
            return self._fail(*SIGN_IN_ERRORS[self.sign_in_error])
        if path != f"/{self.tenant_id}/oauth2/v2.0/token":
            return self._fail(*SIGN_IN_ERRORS["tenant"])
        logs = form.get("client_id") == self.log_client_id
        if form.get("client_id") != self.client_id and not logs:
            return self._fail(*SIGN_IN_ERRORS["application"])
        if form.get("grant_type") != "client_credentials" or form.get("scope") != (LOGS_SCOPE if logs else f"{self.url}/.default"):
            return self._fail(900144, "invalid_request")
        if "client_secret" in form and not logs:
            if form["client_secret"] != self.secret:
                return self._fail(*SIGN_IN_ERRORS["secret"])
        elif not self.assertion_ok(form, f"{self.url}{path}", self._log_cert if logs else self._cert):
            return self._fail(*SIGN_IN_ERRORS["certificate"])
        self._token_n += 1
        token = f"fake-{'log-' if logs else ''}token-{self._token_n}"
        (self.log_tokens if logs else self.tokens).add(token)
        return 200, {"token_type": "Bearer", "expires_in": self.expires_in, "access_token": token}, {}

    def assertion_ok(self, form, token_url, cert=None) -> bool:
        cert = cert or self._cert
        if cert is None or form.get("client_assertion_type") != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
            return False
        public_key, thumbprint = cert
        try:
            assertion = form["client_assertion"]
            header = jwt.get_unverified_header(assertion)
            claims = jwt.decode(
                assertion, public_key, algorithms=["PS256"], audience=token_url, issuer=form.get("client_id"),
                options={"require": ["exp", "nbf", "jti", "sub"]},
            )
        except (KeyError, jwt.PyJWTError):
            return False
        return header.get("alg") == "PS256" and header.get("x5t#S256") == thumbprint and claims["sub"] == form.get("client_id")

    def _authorised(self, headers) -> bool:
        return (headers.get("Authorization") or "").removeprefix("Bearer ") in self.tokens

    def log_query(self, workspace, raw, headers):
        """`POST /v1/workspaces/{guid}/query`: needs a log-app token. Answers what a test registered for the workspace."""
        if (headers.get("Authorization") or "").removeprefix("Bearer ") not in self.log_tokens:
            return 401, {"error": {"code": "AuthenticationFailed", "message": "The access token is invalid."}}, {}
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return 200, "", {}  # Microsoft: a POST without a JSON body is answered with an empty 200
        if not isinstance(body.get("query"), str) or not body["query"].strip():
            return 400, {"error": {"code": "BadArgumentError", "message": "The request had some invalid properties"}}, {}
        if workspace not in self.log_answers:
            return 404, {"error": {"code": "PathNotFoundError", "message": "The requested path does not exist"}}, {}
        answer = self.log_answers[workspace]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return answer

    def subscription_list(self, url, headers):
        if not self._authorised(headers):
            return 401, {"error": {"code": "InvalidAuthenticationToken"}}, {}
        if "api-version=2022-12-01" not in url.query:
            return 400, {"error": {"code": "InvalidApiVersionParameter"}}, {}
        shown = [s for s in self.tenant["subscriptions"] if s["subscriptionId"].lower() not in {h.lower() for h in self.hide_subscription}]
        body = {"value": shown, "count": {"type": "Total", "value": len(shown)}}
        if self.evil_next_link:
            body["nextLink"] = "https://evil.example.com/subscriptions?page=2"
        return 200, body, {}

    def cost_management(self, method, match, url, raw, headers):
        sub, group, leaf = match.group(1).lower(), (match.group(2) or "").lower(), match.group(3).lower()
        if not self._authorised(headers):
            return 401, {"error": {"code": "InvalidAuthenticationToken"}}, {}
        if (method == "POST") != (leaf in ("query", "forecast")) or "api-version=2026-06-01" not in url.query:
            return 400, {"error": {"code": "BadRequest"}}, {}
        if self.cost_throttle > 0:
            self.cost_throttle -= 1
            return 429, {"error": {"code": "429"}}, {"Retry-After": "30", "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after": "30"}
        if leaf in self.cost_fail:
            return self.cost_fail[leaf], {"error": {"code": "Simulated"}}, {}
        quota = {"x-ms-ratelimit-microsoft.costmanagement-qpu-remaining": self.cost_quota} if self.cost_quota else {}
        if leaf in ("budgets", "alerts"):
            items = (self.budgets if leaf == "budgets" else self.cost_alerts).get(sub, [])
            return 200, {"value": items}, quota
        body = json.loads(raw or b"{}")
        span = body["timePeriod"]
        first, last = dt.date.fromisoformat(span["from"][:10]), dt.date.fromisoformat(span["to"][:10])
        lines = [x for x in self.cost_lines if x["sub"].lower() == sub and (not group or x["rg"].lower() == group)
                 and first <= x["date"] <= last]
        if leaf == "forecast":
            actual = {}
            for x in lines:
                actual[x["date"]] = actual.get(x["date"], 0.0) + x["cost"]
            days = [(d, c, "Actual") for d, c in sorted(actual.items())] + [(d, c, "Forecast") for d, c in self.forecast_days.get(sub, []) if first <= d <= last]
            if not days:
                return 204, "", quota
            cur = lines[0]["currency"] if lines else "USD"
            return 200, {"properties": {"columns": [{"name": "PreTaxCost", "type": "Number"}, {"name": "UsageDate", "type": "Number"},
                                                    {"name": "CostStatus", "type": "String"}, {"name": "Currency", "type": "String"}],
                                        "rows": [[c, int(d.strftime("%Y%m%d")), st, cur] for d, c, st in days]}}, quota
        data = body["dataset"]
        flt = ((data.get("filter") or {}).get("dimensions") or {}).get("values")
        if flt:
            lines = [x for x in lines if x["resource"].lower() in {v.lower() for v in flt}]
        grouping = (data.get("grouping") or [{}])[0]
        daily = data.get("granularity") == "Daily"
        kind, name = grouping.get("type"), grouping.get("name")
        pick = {"ServiceName": "service", "ResourceGroupName": "rg", "ResourceId": "resource"}
        totals: dict[tuple, float] = {}
        for x in lines:
            key = ((int(x["date"].strftime("%Y%m%d")),) if daily else ()) + (
                (x["tags"].get(name, ""),) if kind == "TagKey" else (x[pick[name]],) if name else ())
            totals[(*key, x["currency"])] = totals.get((*key, x["currency"]), 0.0) + x["cost"]
        cols = [{"name": "Cost", "type": "Number"}] + ([{"name": "UsageDate", "type": "Number"}] if daily else []) + (
            [{"name": "TagKey", "type": "String"}, {"name": "TagValue", "type": "String"}] if kind == "TagKey"
            else [{"name": name, "type": "String"}] if name else []) + [{"name": "Currency", "type": "String"}]
        rows = []
        for key, cost in sorted(totals.items(), key=lambda kv: kv[0]):
            *head, cur = key
            if kind == "TagKey":
                head = [*head[:-1], name, head[-1]]
            rows.append([cost, *head, cur])
        skip = int(parse_qs(url.query).get("$skiptoken", ["0"])[0])
        page = rows[skip:skip + self.cost_page] if self.cost_page else rows
        props = {"columns": cols, "rows": page, "nextLink": None}
        if self.cost_page and skip + self.cost_page < len(rows):
            host = "https://evil.example.com" if self.cost_evil_next_link else self.url
            props["nextLink"] = f"{host}{url.path}?api-version=2026-06-01&$skiptoken={skip + self.cost_page}"
        return 200, {"properties": props}, quota

    def resource_graph(self, raw, headers):
        if not self._authorised(headers):
            return 401, {"error": {"code": "InvalidAuthenticationToken"}}, {}
        body = json.loads(raw or b"{}")
        kind = _query_kind(body.get("query", ""))
        if kind is None:
            return 400, {"error": {"code": "InvalidQuery"}}, {}
        if _sorts_a_dropped_column(body.get("query", "")):
            return 400, {"error": {"code": "BadRequest", "details": [{"code": "InvalidQuery", "message": "order operator: key should be a column reference"}]}}, {}
        self._graph_n += 1
        self.graph_calls.append(time.monotonic())
        remaining, resets = self.quota
        quota = {"x-ms-user-quota-remaining": str(remaining), "x-ms-user-quota-resets-after": resets}
        if self.throttle_after is not None and self._graph_n > self.throttle_after and self._throttled < self.throttle_count:
            self._throttled += 1
            return 429, {"error": {"code": "RateLimiting"}}, {**quota, "x-ms-user-quota-remaining": "0", "x-ms-user-quota-resets-after": "00:00:01"}
        options = body.get("options") or {}
        top, skip = int(options.get("$top") or 1000), int(options.get("$skipToken") or 0)
        subs = body.get("subscriptions")
        scope = (subs[0].lower() if subs and len(subs) == 1 else None, kind)
        self._page_no[scope] = self._page_no.get(scope, 0) + 1 if skip else 1
        if self.fail_page and kind == "resources" and scope[0] == self.fail_page[0].lower() and self._page_no[scope] == self.fail_page[1]:
            return self.fail_page[2], {"error": {"code": "Simulated"}}, quota
        if self.fail_kind and self.fail_kind[0] == kind:
            return self.fail_kind[1], {"error": {"code": "Simulated"}}, quota
        rows = self.rows("resources" if kind == "count" else kind, subs)
        if kind == "count":
            return 200, {"count": 1, "totalRecords": 1, "data": [{"total": len(rows)}], "resultTruncated": "false"}, quota
        if self.truncate and top > self.truncate:
            return 200, {"count": self.truncate, "totalRecords": len(rows), "data": rows[skip:skip + self.truncate], "resultTruncated": "true"}, quota
        page = rows[skip:skip + top]
        answer = {"count": len(page), "totalRecords": len(rows), "data": page, "resultTruncated": "false"}
        if skip + top < len(rows):
            answer["$skipToken"] = str(skip + top)
        return 200, answer, quota

    def control_op(self, op: dict):
        name = op.pop("op", "")
        actions = {
            "register_certificate": lambda pem: self.register_certificate(pem),
            "remove": lambda name: self.remove(name),
            "set_property": lambda name, path, value: self.set_property(name, path, value),
            "fail_page": lambda subscription, page, status: setattr(self, "fail_page", (subscription, page, status)),
            "clear_fail_page": lambda: setattr(self, "fail_page", None),
            "hide_subscription": lambda id: self.hide_subscription.add(id),
            "show_subscription": lambda id: self.hide_subscription.discard(id),
            "plant_secrets": lambda: self.plant_secrets(),
        }
        if name not in actions:
            return 400, {"error": f"unknown op {name!r}", "ops": sorted(actions)}, {}
        return 200, {"ok": True, "result": actions[name](**op)}, {}

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--secret", default="fake-client-secret")
    parser.add_argument("--control", action="store_true", help="enable POST /_control")
    parser.add_argument("--generated", type=int, help="serve this many generated resources instead of the hand tenant")
    args = parser.parse_args()
    fake = FakeAzure(generated_tenant(args.generated) if args.generated else hand_tenant(), secret=args.secret,
                     control=args.control, port=args.port)
    print(f"stand-in Azure on {fake.url}; tenant {fake.tenant_id}, client {fake.client_id}, secret {args.secret!r}", flush=True)
    print(f'REZZTOR_AZURE_ENDPOINTS_FOR_TESTS="login={fake.url};arm={fake.url}"', flush=True)
    fake._server.serve_forever()

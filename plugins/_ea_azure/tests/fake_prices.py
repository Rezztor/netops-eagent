"""A stand-in for prices.azure.com: one route, the filter evaluated the way the real service reads it
(`field eq 'value'` clauses joined by `and`). The rows are copied from the live answer of
`GET https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&currencyCode=USD` for uaenorth on
2026-10-03 (field names and units as Microsoft returns them; only the columns the module reads are kept).
Reference: https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices

In a test: `with FakePrices() as fake:` and `monkeypatch.setattr(prices, "HOST", fake.url)`.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _row(price, unit, meter, product, sku, service, tier=0.0):
    return {"retailPrice": price, "unitPrice": price, "tierMinimumUnits": tier, "armRegionName": "uaenorth",
            "meterName": meter, "productName": product, "skuName": sku, "serviceName": service,
            "unitOfMeasure": unit, "type": "Consumption", "currencyCode": "USD"}


VM, APP, STO, NET = "Virtual Machines", "Azure App Service", "Storage", "Virtual Network"
ROWS = [
    _row(0.0839, "1 Hour", "D2s v5 Low Priority", "Virtual Machines Dsv5 Series Windows", "Standard_D2s_v5 Low Priority", VM),
    _row(0.0235, "1 Hour", "D2s v5 Low Priority", "Virtual Machines Dsv5 Series", "Standard_D2s_v5 Low Priority", VM),
    _row(0.038808, "1 Hour", "D2s v5 Spot", "Virtual Machines Dsv5 Series Windows", "Standard_D2s_v5 Spot", VM),
    _row(0.21, "1 Hour", "D2s v5", "Virtual Machines Dsv5 Series Windows", "Standard_D2s_v5", VM),
    _row(0.021806, "1 Hour", "D2s v5 Spot", "Virtual Machines Dsv5 Series", "Standard_D2s_v5 Spot", VM),
    _row(0.118, "1 Hour", "D2s v5", "Virtual Machines Dsv5 Series", "Standard_D2s_v5", VM),
    _row(0.191, "1 Hour", "P1 v3 App", "Azure App Service Premium v3 Plan - Linux", "P1 v3", APP),
    _row(0.351, "1 Hour", "P1 v3 App", "Azure App Service Premium v3 Plan", "P1 v3", APP),
    _row(21.504, "1/Month", "P10 LRS Disk", "Premium SSD Managed Disks", "P10 LRS", STO),
    _row(21.504, "1/Month", "P10 LRS Disk", "Premium Page Blob", "P10 LRS", STO),
    _row(1.201, "1/Month", "P10 LRS Disk Mount", "Premium SSD Managed Disks", "P10 LRS", STO),
    _row(0.005, "1 Hour", "Standard IPv4 Static Public IP", "IP Addresses", "Standard", NET),
    _row(0.006, "1 Hour", "Standard Static IP Addresses", "Public IP Prefix", "Standard", NET),
]
_CLAUSE = re.compile(r"(\w+) eq '([^']*)'")


class FakePrices:
    def __init__(self, rows=None):
        self.rows = ROWS if rows is None else rows
        self.status = 200  # 500: the service is down
        self.redirect: str | None = None  # a Location to answer with
        self.extra: bytes = b""  # padding added to the body, to exceed the module's cap
        self.received: list[dict] = []  # {"path", "query"} of every request
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                fake.received.append({"path": url.path, "query": query})
                if fake.redirect:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect)
                    self.end_headers()
                    return
                if url.path != "/api/retail/prices" or fake.status != 200:
                    self.send_response(404 if url.path != "/api/retail/prices" else fake.status)
                    self.end_headers()
                    return
                want = dict(_CLAUSE.findall(query.get("$filter", "")))
                items = [r for r in fake.rows if all(str(r.get(k)) == v for k, v in want.items() if k != "priceType")]
                body = json.dumps({"BillingCurrency": "USD", "Items": items, "NextPageLink": None}).encode() + fake.extra
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

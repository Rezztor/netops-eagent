"""A local stand-in for SolarWinds' SWIS REST query endpoint.

Speaks only `POST /SolarWinds/InformationService/v3/Json/Query` and understands
only the bulk templates in `helpers/capabilities.py` (anything else is a 400, and
is recorded in `received` so a test can see it). Column names are the plugin's
*unverified* guesses -- the fake mirrors them, it does not vouch for Orion.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

QUERY_PATH = "/SolarWinds/InformationService/v3/Json/Query"

_NODES = re.compile(
    r"^SELECT TOP @page (?P<cols>[\w, ]+) FROM Orion\.Nodes WHERE NodeID > @after ORDER BY NodeID$"
)
_HW = re.compile(
    r"^SELECT NodeID, ServiceTag FROM Orion\.HardwareHealth\.HardwareInfo "
    r"WHERE NodeID > @after AND NodeID <= @upto ORDER BY NodeID$"
)
_NCM = re.compile(
    r"^SELECT CNode\.CoreNodeID AS NodeID, Physical\.SerialNumber FROM Cirrus\.EntityPhysical AS Physical "
    r"INNER JOIN Cirrus\.Nodes AS CNode ON Physical\.NodeID = CNode\.NodeID "
    r"WHERE CNode\.CoreNodeID > @after AND CNode\.CoreNodeID <= @upto "
    r"AND Physical\.EntityClass = 3 AND Physical\.SerialNumber <> '' ORDER BY CNode\.CoreNodeID$"
)


def _node(i, caption, ip, vendor, machine, version, oid, status=1, dns="", sys_name="", loc="DC1"):
    return {
        "NodeID": i, "Caption": caption, "DNS": dns, "SysName": sys_name or caption, "IPAddress": ip,
        "Vendor": vendor, "MachineType": machine, "IOSVersion": version, "SysObjectID": oid,
        "Location": loc, "Status": status, "LastSystemUpTimePollUtc": f"2026-09-30T10:{i:02d}:00.0000000",
    }


def hand_nodes():
    return [
        _node(1, "core-sw1", "10.0.0.1", "Cisco", "Cisco Catalyst 9300", "17.9.4a", "1.3.6.1.4.1.9.1.2", dns="core-sw1.corp.example"),
        _node(2, "CORE-SW2", "10.0.0.2", "Cisco", "Cisco Nexus 9000", "NX-OS 10.3(2)", "1.3.6.1.4.1.9.12.3.1.3", loc="DC2"),
        _node(3, "fw-edge1", "10.0.1.1", "Fortinet", "FortiGate 100F", "v7.2.5", "1.3.6.1.4.1.12356.101.1"),
        _node(4, "pa-dc1", "10.0.1.2", "Palo Alto Networks", "PA-3220", "10.2.3", "1.3.6.1.4.1.25461.2.3"),
        _node(5, "jn-agg1", "10.0.2.1", "Juniper Networks", "EX4300-48T", "21.4R3", "1.3.6.1.4.1.2636.1.1.1.2"),
        _node(6, "arista-leaf1", "10.0.2.2", "Arista Networks", "DCS-7050SX3", "4.30.1F", "1.3.6.1.4.1.30065.1.3011"),
        _node(7, "mystery-box", "10.0.3.1", "", "", "", ""),
        _node(8, "dup-ip-a", "10.0.4.4", "Cisco", "Cisco ISR 4331", "16.12.4", "1.3.6.1.4.1.9.1.1"),
        _node(9, "dup-ip-b", "10.0.4.4", "Cisco", "Cisco ISR 4331", "16.12.4", "1.3.6.1.4.1.9.1.1"),
        _node(10, "legacy-name", "10.0.5.1", "Cisco", "Cisco Catalyst 2960", "15.2(7)E3", "1.3.6.1.4.1.9.1.1", dns="sw-floor3.corp.example"),
        _node(11, "branch-rtr", "10.0.6.1", "Juniper Networks", "SRX300", "20.4R3", "1.3.6.1.4.1.2636.1.1.1.2", status=2, loc=""),
        _node(12, "NO-SERIAL-SW", "10.0.7.1", "Cisco", "Cisco Catalyst 9200", "17.6.5", "1.3.6.1.4.1.9.1.2", sys_name="no-serial-sw"),
    ]


HAND_HW_SERIALS = {1: "FOC1111AAAA", 2: "FDO2222BBBB", 3: "FG100F0001", 4: "N/A", 5: "JN0001", 8: "SN-DUP-A", 9: "SN-DUP-B"}
HAND_NCM_SERIALS = {6: "JPE6666FFFF", 10: "FCW1010JJJJ"}  # 7, 11, 12: no serial anywhere


def generated_nodes(count=450):
    vendors = ("Cisco", "Fortinet", "Palo Alto Networks", "Juniper Networks", "Arista Networks")
    return [
        _node(i, f"gen-{i:04d}", f"10.{i // 250}.{i % 250}.1", vendors[i % 5], "model", f"1.{i}", "1.3.6.1")
        for i in range(1, count + 1)
    ]


class FakeSwis:
    def __init__(self, nodes=None, hw_serials=None, ncm_serials=None):
        self.nodes = sorted(nodes if nodes is not None else hand_nodes(), key=lambda n: n["NodeID"])
        self.hw = HAND_HW_SERIALS if hw_serials is None else hw_serials
        self.ncm = HAND_NCM_SERIALS if ncm_serials is None else ncm_serials
        # switches
        self.status_all = None  # e.g. 401: every query answers with it
        self.fail_node_page = None  # (page_number, status): that Orion.Nodes query fails
        self.delay = 0.0  # seconds before answering (simulates a timeout)
        self.malformed = False
        self.fail_serials = None  # e.g. 500: the serial queries fail
        self.missing_columns: set[str] = set()  # a query selecting one of these gets a 400
        self.missing_entities: set[str] = set()  # "Orion.HardwareHealth.HardwareInfo", "Cirrus.EntityPhysical"
        # what it saw
        self.received: list[dict] = []
        self._node_queries = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fake.received.append({"path": self.path, "query": body.get("query", ""), "params": body.get("parameters", {}),
                                      "auth": self.headers.get("Authorization")})
                if self.path != QUERY_PATH:
                    return self._send(404, {"Message": "not found"})
                time.sleep(fake.delay)
                if fake.malformed:
                    return self._send(200, "not json{", raw=True)
                status, payload = fake.answer(body.get("query", ""), body.get("parameters", {}))
                self._send(status, payload)

            def _send(self, status, payload, raw=False):
                data = payload.encode() if raw else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def answer(self, query, params):
        if self.status_all:
            return self.status_all, {"Message": "forced failure"}
        after, page, upto = params.get("after"), params.get("page"), params.get("upto")
        if m := _NODES.match(query):
            self._node_queries += 1
            if self.fail_node_page and self.fail_node_page[0] == self._node_queries:
                return self.fail_node_page[1], {"Message": "Internal error"}
            cols = [c.strip() for c in m["cols"].split(",")]
            if bad := self.missing_columns.intersection(cols):
                return 400, {"Message": f"Invalid column name '{sorted(bad)[0]}'."}
            rows = [n for n in self.nodes if n["NodeID"] > after][:page]
            return 200, {"results": [{c: n[c] for c in cols} for n in rows]}
        for rx, entity, column, source in ((_HW, "Orion.HardwareHealth.HardwareInfo", "ServiceTag", self.hw),
                                           (_NCM, "Cirrus.EntityPhysical", "SerialNumber", self.ncm)):
            if rx.match(query):
                if self.fail_serials:
                    return self.fail_serials, {"Message": "Internal error"}
                if entity in self.missing_entities:
                    return 400, {"Message": f"Source entity [{entity}] not found in catalog."}
                if column in self.missing_columns:
                    return 400, {"Message": f"Invalid column name '{column}'."}
                return 200, {"results": [{"NodeID": i, column: s} for i, s in sorted(source.items()) if after < i <= upto]}
        return 400, {"Message": "Syntax error: unknown query"}

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

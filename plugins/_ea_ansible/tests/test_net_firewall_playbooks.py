"""The FortiGate and Palo Alto reads can only read, and the playbooks work end to end.

The first half needs nothing. The second runs every read through the built executor image
(`ansible-playbook --check`, as the executor does) against a fake HTTPS device that serves the
documented-output fixtures, so the Jinja, the URL building, the header and the verified TLS
connection are exercised for real; it skips, with the build command, when the image is absent.
Neither half talks to a real device.
"""

from __future__ import annotations

import datetime
import http.server
import ipaddress
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import urllib.parse

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
READS = os.path.join(os.path.dirname(HERE), "playbooks", "reads")
FIXTURES = os.path.join(HERE, "fixtures", "firewall")
IMAGE = "rezztor/eagent-ansible:latest"
TOKEN = "TESTTOKEN-Zq9xKvLm"

#: Everything that can change a device through these two APIs.
WRITES = re.compile(
    r"(?i)(method:\s*(?!GET\b)\w+|\btype=(commit|import|export|keygen|user-id|log|report|version|config&action=(set|edit|delete|rename|move|clone|override|multi-move|multi-clone)|op&cmd=<(request|debug|clear|set|test|commit|load|save|delete|edit|rename|move)))"
    r"|<(request|debug|clear|set|test|commit|load|save|delete|edit|rename|move)>"
)


def _firewall_playbooks() -> list[str]:
    found = []
    for root, _dirs, names in os.walk(READS):
        for name in sorted(names):
            path = os.path.join(root, name)
            if name.endswith(".yml") and ("fw/read.yml" in open(path, encoding="utf-8").read() or root.endswith("fw")):
                found.append(path)
    return found


def test_every_firewall_playbook_can_only_read():
    paths = _firewall_playbooks()
    assert len(paths) >= 9, paths
    for path in paths:
        text = open(path, encoding="utf-8").read()
        assert not WRITES.search(re.sub(r"(?m)^\s*#.*$", "", text)), path
        for request in re.findall(r'"(/api/v2/[^"]*)"', text):  # the requests a read playbook lists
            assert re.match(r"/api/v2/(monitor|cmdb)/", request), (path, request)


def test_the_one_call_site_is_a_get_that_verifies_and_hides_the_token():
    document = yaml.safe_load(open(os.path.join(READS, "fw", "read.yml"), encoding="utf-8"))
    calls = [t for t in document if "ansible.builtin.uri" in t]
    assert len(calls) == 1
    task = calls[0]
    assert task["ansible.builtin.uri"]["method"] == "GET" and task["ansible.builtin.uri"]["validate_certs"] is True
    assert task["no_log"] is True and task["delegate_to"] == "localhost" and task["check_mode"] is False
    assert "url_password" not in task["ansible.builtin.uri"]
    assert "?key=" not in str(task) and "access_token" not in str(task)  # the token goes in a header, never the URL
    assert not [k for k in os.listdir(os.path.join(READS, "fw")) if not k.endswith(".yml")]


def test_the_backup_playbook_holds_the_same_rules():
    document = yaml.safe_load(open(os.path.join(HERE, "..", "..", "..", "executor", "playbooks", "config_backup.yml"), encoding="utf-8"))
    (task,) = [t for t in document[0]["tasks"] if "ansible.builtin.uri" in t]
    call = task["ansible.builtin.uri"]
    assert call["method"] == "GET" and call["validate_certs"] is True and task["no_log"] is True
    assert task["delegate_to"] == "localhost" and "?key=" not in str(task) and "access_token" not in str(task)


def test_no_playbook_turns_certificate_checking_off():
    for path in _firewall_playbooks():
        assert not re.search(r"validate_certs:\s*(false|no|\{)", open(path, encoding="utf-8").read(), re.I), path


# --- end to end against a fake device, through the real image ----------------------------------

def _image_exists() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


def _certificate(directory: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-firewall")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(directory, "ca.pem"), os.path.join(directory, "key.pem")
    open(cert_path, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(key_path, "wb").write(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


class _Device(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str, dict]] = []

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        type(self).seen.append((self.command, self.path, dict(self.headers)))
        forti, pan = url.path.startswith("/api/v2/"), url.path == "/api/"
        sent = self.headers.get("Authorization", "") if forti else self.headers.get("X-PAN-KEY", "")
        if sent != (f"Bearer {TOKEN}" if forti else TOKEN):
            return self._send(403 if pan else 401, "<response status='error' code='403'><result><msg>Invalid Credential</msg></result></response>" if pan else "{}")
        if forti and url.path == "/api/v2/monitor/system/config/backup":
            return self._send(200, BACKUP["fortios"])
        if pan and query.get("type") == ["export"] and query.get("category") == ["configuration"]:
            return self._send(200, BACKUP["panos"])
        if forti:
            key = url.path + ("?" + url.query if url.query else "")
            body = FORTIOS.get(key)
            return self._send(200, json.dumps(body)) if body else self._send(404, "{}")
        key = (query.get("cmd") or query.get("xpath") or [""])[0]
        return self._send(200, PANOS[key]) if query.get("type") in (["op"], ["config"]) and key in PANOS else self._send(200, "<response status='error' code='12'><result><msg>no</msg></result></response>")

    def _send(self, status: int, body: str):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


BACKUP = {
    "fortios": "#config-version=FGT60F-7.2.4-FW-build1396-230106:opmode=0:vdom=0:user=apiadmin\nconfig system global\n    set hostname \"FW-Edge1\"\nend\n",
    "panos": '<config version="10.2.0"><devices><entry name="localhost.localdomain"/></devices></config>',
}
FORTIOS = json.load(open(os.path.join(FIXTURES, "fortios.json"), encoding="utf-8"))
PANOS = json.load(open(os.path.join(FIXTURES, "panos.json"), encoding="utf-8"))
for _vendor, _answers in (("fortios", FORTIOS), ("panos", PANOS)):  # the ports read asks for a few more
    for _request, _answer in json.load(open(os.path.join(os.path.dirname(HERE), "..", "_ea_inventory", "tests", "fixtures", "ports", f"{_vendor}_documented.json"), encoding="utf-8")).items():
        _answers.setdefault(_request, _answer)
READ_FILES = ["device_facts", "inventory_facts", "inventory_ports", "interfaces", "routing_table", "firewall_policy_summary",
              "vpn_tunnels", "system_resources", "session_summary"]
EXPECT = {  # a value each read must print for each platform
    "device_facts": ("FGT60F0000000001", "012345678901"), "inventory_facts": ("EAGENT_FACTS", "EAGENT_FACTS"),
    "inventory_ports": ("EAGENT_PORTS fgt1", "EAGENT_PORTS pan1"),
    "interfaces": ("192.0.2.2/30", "ethernet1/1"), "routing_table": ("0.0.0.0/0", "192.0.2.1"),
    "firewall_policy_summary": ("lan-to-wan", "allow-web"), "vpn_tunnels": ("198.51.100.7", "to-hq:proxy1"),
    "system_resources": ("38", "cpu_used_percent"), "session_summary": ("1241", "num-active"),
}


@pytest.fixture(scope="module")
def device(tmp_path_factory):
    directory = str(tmp_path_factory.mktemp("fw"))
    os.chmod(directory, 0o755)
    cert, key = _certificate(directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Device)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    # The inventory is rendered by the executor itself, so the token's variable name, the local connection
    # and the group variables are the ones a real job gets.
    from executor import credentials, inventory

    patch = pytest.MonkeyPatch()
    patch.setenv(credentials.ENV_KEY, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    credentials.reset_for_tests(os.path.join(directory, "credentials.json")).put("fw-token", kind="api_token", secret=TOKEN)
    store = inventory.reset_for_tests(os.path.join(directory, "inventory.json"))
    host = {"ansible_host": "127.0.0.1", "vars": {"ansible_port": port, "eagent_tls_ca": "/fw/ca.pem",
                                                  "ansible_python_interpreter": "/opt/ansible-venv/bin/python"}}
    store.save({"hosts": {"fgt1": host, "pan1": host}, "groups": {
        "fgt": {"hosts": ["fgt1"], "credential": "fw-token", "vars": {
            "ansible_network_os": "fortinet.fortios.fortios", "ansible_connection": "ansible.netcommon.httpapi"}},
        "pan": {"hosts": ["pan1"], "credential": "fw-token", "vars": {
            "ansible_network_os": "paloaltonetworks.panos.panos", "ansible_connection": "ansible.netcommon.httpapi"}}}})
    inventory_path = store.render(directory)
    patch.undo()
    credentials.reset_for_tests(None)
    inventory.reset_for_tests(None)
    for name in (inventory_path, cert):
        os.chmod(name, 0o644)
    yield {"directory": directory, "port": port}
    server.shutdown()


def _run(device, playbook: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "--network", "host", "-e", "HOME=/tmp", "-e", "ANSIBLE_LOCAL_TEMP=/tmp/a",
         "-e", "ANSIBLE_NOCOLOR=1", "-v", f"{READS}:/pb:ro", "-v", f"{device['directory']}:/fw:ro",
         "--entrypoint", "ansible-playbook", IMAGE, "-i", "/fw/inventory.yml", f"/pb/{playbook.split(chr(47))[-1]}.yml",
         "--check", "--diff", *extra],
        capture_output=True, text=True, timeout=300)


@pytest.mark.skipif(not _image_exists(), reason=f"build the executor image first: docker build -f executor/Dockerfile -t {IMAGE} .")
@pytest.mark.parametrize("read", READ_FILES)
def test_each_read_runs_against_both_vendors_with_only_gets_and_a_hidden_token(device, read):
    _Device.seen.clear()
    done = _run(device, f"reads/{read}")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-3000:]
    for expected in EXPECT[read]:
        assert expected in out, (expected, out[-3000:])
    assert TOKEN not in out
    if read == "inventory_facts":  # what the inventory refresh would make of it, through its own parser and normaliser
        from plugins._ea_inventory.helpers.facts import collect, normalisers

        lines = collect.parse_facts(out)
        assert normalisers.normalise_facts("fortios", lines["fgt1"]["facts"]).fields["serial"] == "FGT60F0000000001"
        assert normalisers.normalise_facts("panos", lines["pan1"]["facts"]).fields["serial"] == "012345678901"
    if read == "inventory_ports":  # and what the ports refresh makes of the printed line
        from plugins._ea_inventory.helpers.facts import collect_ports, ports_parse

        lines = collect_ports.parse_output(out)
        assert {i["name"] for i in ports_parse.parse("fortios", lines["fgt1"]).interfaces} >= {"port1", "vlan10"}
        assert {i["name"] for i in ports_parse.parse("panos", lines["pan1"]).interfaces} >= {"ethernet1/1", "tunnel.1"}
    assert _Device.seen and {method for method, _p, _h in _Device.seen} == {"GET"}
    assert all(TOKEN not in path for _m, path, _h in _Device.seen)  # never in the URL


@pytest.mark.skipif(not _image_exists(), reason=f"build the executor image first: docker build -f executor/Dockerfile -t {IMAGE} .")
def test_an_untrusted_certificate_and_a_wrong_token_are_plain_failures(device, tmp_path):
    done = _run(device, "reads/routing_table", "-e", "eagent_tls_ca=/nonexistent.pem")
    assert done.returncode != 0 and TOKEN not in done.stdout + done.stderr
    done = _run(device, "reads/routing_table", "-e", "ansible_httpapi_pass=WRONG")
    assert done.returncode != 0 and "Access denied" in done.stdout + done.stderr


@pytest.mark.skipif(not _image_exists(), reason=f"build the executor image first: docker build -f executor/Dockerfile -t {IMAGE} .")
def test_the_backup_playbook_writes_each_firewalls_configuration_and_nothing_on_a_refusal(device):
    out = os.path.join(device["directory"], "out")
    os.makedirs(out, exist_ok=True)
    os.chmod(out, 0o777)
    backup_playbook = os.path.join(HERE, "..", "..", "..", "executor", "playbooks")
    run = lambda *extra: subprocess.run(
        ["docker", "run", "--rm", "--network", "host", "-e", "HOME=/tmp", "-e", "ANSIBLE_LOCAL_TEMP=/tmp/a",
         "-e", "ANSIBLE_NOCOLOR=1", "-v", f"{os.path.abspath(backup_playbook)}:/pb:ro", "-v", f"{device['directory']}:/fw:ro",
         "-v", f"{out}:/out", "--entrypoint", "ansible-playbook", IMAGE, "-i", "/fw/inventory.yml",
         "/pb/config_backup.yml", "-e", "eagent_backup_dir=/out", *extra],
        capture_output=True, text=True, timeout=300)
    done = run()
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert open(os.path.join(out, "fgt1.txt"), encoding="utf-8").read() == BACKUP["fortios"]
    assert open(os.path.join(out, "pan1.txt"), encoding="utf-8").read() == BACKUP["panos"]
    assert TOKEN not in done.stdout + done.stderr
    for name in ("fgt1.txt", "pan1.txt"):
        os.remove(os.path.join(out, name))
    refused = run("-e", "ansible_httpapi_pass=WRONG")
    assert refused.returncode != 0 and not os.listdir(out)  # an error body is never stored as a configuration

"""The executor behind a Unix socket (ADR 0051), and secrets read from files.

The client test talks to a real `AF_UNIX` server, so the transport is exercised
rather than assumed; waitress itself is not needed.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import socketserver
import threading

import pytest
import yaml

from executor import app as executor_app
from executor import credentials, secret_env
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import executor_client

from plugins._ea_ansible.tests.conftest import AUTH, CREDENTIAL_KEY, TOKEN

ROOT = pathlib.Path(__file__).resolve().parents[3]


# --- secrets from files ------------------------------------------------------

def test_a_secret_comes_from_its_file_and_both_forms_are_refused(monkeypatch, tmp_path):
    path = tmp_path / "s"
    path.write_text("from-the-file\n")
    monkeypatch.delenv("X_SECRET", raising=False)
    monkeypatch.setenv("X_SECRET_FILE", str(path))
    assert secret_env.get("X_SECRET") == "from-the-file"
    monkeypatch.setenv("X_SECRET", "from-env")
    with pytest.raises(secret_env.SecretConfigError, match="Use one"):
        secret_env.get("X_SECRET")
    monkeypatch.setenv("X_SECRET", "")  # an empty variable is the same as unset
    assert secret_env.get("X_SECRET") == "from-the-file"
    monkeypatch.setenv("X_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(secret_env.SecretConfigError, match="cannot be read"):
        secret_env.get("X_SECRET")


def test_the_credential_key_can_be_a_file_but_not_both(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(CREDENTIAL_KEY + "\n")
    monkeypatch.delenv(credentials.ENV_KEY, raising=False)
    monkeypatch.setenv(credentials.ENV_KEY + "_FILE", str(key_file))
    assert len(credentials._key()) == credentials.KEY_BYTES
    monkeypatch.setenv(credentials.ENV_KEY, CREDENTIAL_KEY)
    with pytest.raises(credentials.CredentialError, match="Use one"):
        credentials._key()


def test_a_token_file_authenticates_and_a_conflicting_token_refuses(client, monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    monkeypatch.delenv(executor_app.ENV_TOKEN)
    monkeypatch.setenv(executor_app.ENV_TOKEN + "_FILE", str(token_file))
    assert client.get("/jobs", headers=AUTH).status_code == 200
    monkeypatch.setenv(executor_app.ENV_TOKEN, TOKEN)  # both forms: closed, not open
    assert client.get("/jobs", headers=AUTH).status_code == 401


# --- listening ---------------------------------------------------------------

def test_the_socket_variable_replaces_the_tcp_listener(monkeypatch):
    monkeypatch.setenv(executor_app.ENV_SOCKET, "/run/ansible-socket/executor.sock")
    assert executor_app.listen_options() == {
        "unix_socket": "/run/ansible-socket/executor.sock",
        "unix_socket_perms": "660",
    }
    monkeypatch.setenv(executor_app.ENV_SOCKET, "")
    assert executor_app.listen_options()["port"] == 8500


# --- the client over a socket ------------------------------------------------

class _Unix(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def unix_executor(monkeypatch, tmp_path):
    seen: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append({
                "method": self.command, "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(self.rfile.read(length) or "null"),
            })
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "results": {"h": {"ok": True}}}).encode())

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

        def address_string(self):  # an AF_UNIX peer has no address to format
            return "unix"

    path = str(tmp_path / "executor.sock")
    server = _Unix(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(plugin_config, "get_config", lambda agent=None: {"executor_url": "unix://" + path})
    monkeypatch.setattr(plugin_config, "executor_token", lambda agent=None: TOKEN)
    yield seen
    server.shutdown()
    server.server_close()


def test_the_client_speaks_http_over_the_socket_with_the_token(unix_executor):
    assert executor_client.health()["status"] == "ok"
    assert executor_client.preflight(None, ["h"]) == {"h": {"ok": True}}
    executor_client.backup_list(None, "h")
    assert unix_executor[0]["auth"] is None  # health needs no token
    assert unix_executor[1] == {
        "method": "POST", "path": "/v1/preflight", "auth": f"Bearer {TOKEN}", "body": {"hosts": ["h"]},
    }
    assert unix_executor[2]["path"] == "/v1/backups?host=h"


def test_a_dead_socket_is_unreachable_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_config, "get_config", lambda agent=None: {"executor_url": "unix://" + str(tmp_path / "none.sock")})
    with pytest.raises(executor_client.ExecutorUnreachable):
        executor_client.health()


# --- the compose files -------------------------------------------------------

@pytest.mark.parametrize("compose", ["docker/run/docker-compose.yml", "deploy/customer/docker-compose.yml"])
def test_the_socket_volume_is_shared_by_the_application_and_the_executor_only(compose):
    doc = yaml.safe_load((ROOT / compose).read_text())
    mounting = {
        name for name, service in doc["services"].items()
        if any(str(v).startswith("ansible_socket:") for v in service.get("volumes") or [])
    }
    assert mounting == {"eagent", "ansible"}
    assert "ansible_socket" in doc["volumes"]
    env = doc["services"]["ansible"]["environment"]
    assert env["REZZTOR_ANSIBLE_SOCKET"].endswith("/run/ansible-socket/executor.sock}")
    assert "unix:///run/ansible-socket/executor.sock" in doc["services"]["eagent"]["environment"]["REZZTOR_ANSIBLE_EXECUTOR_URL"]

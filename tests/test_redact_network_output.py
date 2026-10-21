"""Fake secrets in realistic device output must not survive the extension or the executor scrub."""
import asyncio
import importlib.util
import pathlib

import pytest

from executor.redaction import redact
from helpers.tool import Response
from plugins._ea_inventory.helpers.redact import redact_text

_path = pathlib.Path(__file__).resolve().parents[1] / "extensions/python/tool_execute_after/_20_redact_network_output.py"
_spec = importlib.util.spec_from_file_location("redact_network_output", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# (device output, the fake secret inside it)
LEAKS = [
    ("enable secret 9 $9$Zq9fakeHash1234$abcdefghij", "Zq9fakeHash1234"),
    ("username admin privilege 15 secret 5 $1$Zq9f$FakeHash99", "FakeHash99"),
    ("snmp-server community Zq9-fake-RO RO", "Zq9-fake-RO"),
    ("snmp-server user u g v3 auth sha Zq9AuthKey1234 priv aes 128 Zq9PrivKey5678", "Zq9AuthKey1234"),
    ("snmp-server user u g v3 auth sha Zq9AuthKey1234 priv aes 128 Zq9PrivKey5678", "Zq9PrivKey5678"),
    ("tacacs-server host 10.0.0.5 key 7 Zq9TacKey0822", "Zq9TacKey0822"),
    ("radius-server host 10.0.0.6 key Zq9RadKey", "Zq9RadKey"),
    ("neighbor 10.1.1.1 password 7 Zq9BgpPw0001", "Zq9BgpPw0001"),
    ('set authentication-password "Zq9JunosPw"\nset encrypted-password "$6$Zq9salt$JunosHash99"', "JunosHash99"),
    ("    set password ENC SH2Zq9FortiHash+/==", "SH2Zq9FortiHash+/=="),
    ("    set psksecret ENC Zq9FortiPsk0123", "Zq9FortiPsk0123"),
    ("    set passwd ENC Zq9FortiPasswd", "Zq9FortiPasswd"),
    ("    <phash>$1$Zq9pan$PanHash99</phash>", "PanHash99"),
    ("    <password>Zq9-fake-Pass-1234</password>", "Zq9-fake-Pass-1234"),
    ("username admin secret sha512 $6$Zq9salt$AristaHash99", "AristaHash99"),
    ("admin:\n  password_hash: Zq9SonicHash\nansible_password: Zq9-fake-Pass-1234", "Zq9-fake-Pass-1234"),
    ("Authorization: Bearer Zq9fakeBearerToken1234", "Zq9fakeBearerToken1234"),
]


def _hook(text, tool="net_read"):
    r = Response(message=text, break_loop=False)
    asyncio.run(_mod.RedactNetworkOutput(None).execute(response=r, tool_name=tool))
    return r.message


@pytest.mark.parametrize("line,secret", LEAKS)
def test_no_secret_survives_either_layer(line, secret):
    assert secret not in _hook(line)
    assert secret not in redact(line)
    assert secret not in redact_text(redact(line))[0]


def test_ordinary_output_is_not_mangled():
    text = (
        "GigabitEthernet0/1 is up, line protocol is up\n10.1.1.1/24 vlan 100,200\n"
        "Eth1/1 up 10G\nno secret configured\nthe token is expired\nsnmp-server enable traps\n"
    )
    assert _hook(text) == text
    assert redact(text.replace("no secret configured\n", "")) == text.replace("no secret configured\n", "")


def test_hook_is_idempotent_and_scoped():
    once = _hook("set password ENC SH2Zq9FortiHash")
    assert _hook(once) == once and "[REDACTED:" in once
    assert _hook("password hunter2-fake", tool="code_execution_tool") == "password hunter2-fake"
    assert "hunter2-fake" not in _hook("password hunter2-fake", tool="inventory_lookup")


def test_a_solarwinds_result_is_scrubbed():
    """`solarwinds_lookup device_config` returns archived configurations, secrets and all."""
    text = "device_config: 1 result(s).\n- NodeCaption: sw1; Config: hostname sw1\nsnmp-server community Zq9-fake-RO RO"
    assert "Zq9-fake-RO" not in _hook(text, tool="solarwinds_lookup")


def test_approved_commands_keep_operational_values():
    cmd = "snmp-server community FINANCE-RO\nset password ENC SH2abc\ntacacs-server key 7 K1"
    assert redact(cmd, keep_operational=True) == cmd

import pytest

from plugins._ea_inventory.helpers import redact

# (vendor, config, secrets that must not survive, lines that must stay as they are)
SAMPLES = {
    "ios": (
        """hostname core-sw1
enable secret 5 $1$mERr$hx5rVt7rPNoS4wqbXKX7m0
enable password 7 0822455D0A16
username admin privilege 15 secret 9 $9$abcDEF123$ghiJKL456xyzQQ
username ops password 0 Sup3rS3cret!
snmp-server community Pr1vateRW RW
snmp-server community public RO 10
snmp-server host 10.1.1.9 traps version 2c TrapC0mm
snmp-server user nms grp v3 auth sha AuthPass123 priv aes 128 PrivPass456
tacacs-server host 10.0.0.5 key 7 060506324F41
radius-server host 10.0.0.6 key RadK3y!
crypto isakmp key VpnPSK123 address 1.2.3.4
ip ospf message-digest-key 1 md5 7 094F471A1A0A
ip ospf authentication-key 7 02050D480809
ntp authentication-key 1 md5 NtpS3cret
router bgp 65000
 neighbor 10.9.9.9 password 7 13061E010803
key chain K1
 key 1
  key-string 7 0A1B2C3D4E
line vty 0 4
 password 7 104D000A0618
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
""",
        ["mERr", "0822455D0A16", "abcDEF123", "Sup3rS3cret", "Pr1vateRW", "TrapC0mm", "AuthPass123",
         "PrivPass456", "060506324F41", "RadK3y", "VpnPSK123", "094F471A1A0A", "02050D480809",
         "NtpS3cret", "13061E010803", "0A1B2C3D4E", "104D000A0618", "public"],
        ["hostname core-sw1", "key chain K1", " key 1", "ip address 10.10.10.1 255.255.255.0", "router bgp 65000"],
    ),
    "nxos": (
        """feature tacacs+
username admin password 5 $5$abc$DEFghiJKLmno role network-admin
snmp-server user admin network-admin auth md5 0xAbCdEf01 priv 0x12345678 localizedkey
tacacs-server key 7 "QzZmYXRhbA"
snmp-server community NxCommunity group network-operator
""",
        ["abc$DEFghi", "0xAbCdEf01", "0x12345678", "QzZmYXRhbA", "NxCommunity"],
        ["feature tacacs+"],
    ),
    "junos": (
        """system {
    host-name edge-mx1;
    root-authentication {
        encrypted-password "$6$SaLt$H4shH4shH4shH4sh/abc"; ## SECRET-DATA
    }
    login {
        user neteng {
            authentication {
                encrypted-password "$1$xyz$Qwerty123456";
                ssh-rsa "ssh-rsa AAAA";
            }
        }
    }
    radius-server 10.0.0.7 secret "$9$kQqPT3nCu0IVsRh-dsaJHq";
}
snmp {
    community Jun1perRO {
        authorization read-only;
    }
}
set security ike policy P1 pre-shared-key ascii-text "$9$mfF6A0Ic.Pz3"
set protocols ospf area 0 interface ge-0/0/0 authentication simple-password "Ospf$ecret!"
set snmp v3 usm local-engine user u1 authentication-password "AuthJunos99"
""",
        ["SaLt$H4sh", "xyz$Qwerty", "kQqPT3nCu0IVsRh", "Jun1perRO", "mfF6A0Ic", "AuthJunos99", "Ospf$ecret"],
        ["host-name edge-mx1;", "authorization read-only;", "ssh-rsa \"ssh-rsa AAAA\";"],
    ),
    "fortios": (
        """config system admin
    edit "admin"
        set password ENC SH2xyzABCdefGHIjkl+/=
    next
end
config vpn ipsec phase1-interface
    edit "to-hq"
        set psksecret ENC abc123DEFghi456JKL=
    next
end
config user radius
    edit "rad1"
        set secret ENC QWxhZGRpbjpvcGVuIHNlc2FtZQ==
    next
end
config system ntp
    set key ENC ntpkeyENCvalue==
end
config firewall address
    edit "lan"
        set subnet 192.168.1.0 255.255.255.0
    next
end
""",
        ["SH2xyzABC", "abc123DEFghi", "QWxhZGRpbjpvcGVuIHNlc2FtZQ", "ntpkeyENCvalue"],
        ["set subnet 192.168.1.0 255.255.255.0", 'edit "lan"'],
    ),
    "panos": (
        """set mgt-config users admin phash $1$qwerty$AbCdEfGhIjKlMnOpQrSt
set deviceconfig system hostname fw-edge1
<entry name="admin"><phash>$5$salt$HaShHaShHaSh</phash></entry>
<auth><password>Pa$$w0rdXML</password></auth>
<ike><pre-shared-key>PanPskValue</pre-shared-key></ike>
set network ike gateway g1 authentication pre-shared-key key PanPsk2
""",
        ["qwerty$AbCd", "salt$HaSh", "Pa$$w0rdXML", "PanPskValue", "PanPsk2"],
        ["set deviceconfig system hostname fw-edge1", "</phash></entry>", "</password></auth>"],
    ),
    "generic": (
        """password: hunter2hunter2
api_key = sk-ABCDEF123456
"token": "tok_live_998877"
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig
aws_access_key_id = AKIAIOSFODNN7EXAMPLE
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAxyz
-----END RSA PRIVATE KEY-----
site: Jakarta
""",
        ["hunter2hunter2", "sk-ABCDEF123456", "tok_live_998877", "eyJhbGci", "AKIAIOSFODNN7EXAMPLE", "MIIEowIBAAKC"],
        ["site: Jakarta"],
    ),
}


@pytest.mark.parametrize("vendor", SAMPLES)
def test_no_secret_survives_and_is_idempotent(vendor):
    cfg, secrets, keep = SAMPLES[vendor]
    out, counts = redact.redact_text(cfg)
    for s in secrets:
        assert s not in out, f"{vendor}: {s!r} survived\n{out}"
    for line in keep:
        assert line in out, f"{vendor}: lost {line!r}\n{out}"
    assert sum(counts.values()) >= len(secrets) - 2 and "[REDACTED:" in out
    again, again_counts = redact.redact_text(out)
    assert again == out and again_counts == {}


def test_counts_by_type_and_token_shape():
    out, counts = redact.redact_text("enable secret 5 $1$a$b\nsnmp-server community x RO\n")
    assert out == "enable secret 5 [REDACTED:password]\nsnmp-server community [REDACTED:community] RO\n"
    assert counts == {"password": 1, "community": 1}


@pytest.mark.parametrize("bad", [None, 5, "", b"bytes", "\x00\ud800 odd"])
def test_never_raises(bad):
    out, counts = redact.redact_text(bad)
    assert isinstance(out, str) and isinstance(counts, dict)


def test_cells_secret_columns_are_redacted_whole():
    counts: dict = {}
    for header in ("Password", "SNMP Community", "ssh_key", "api-token", "psk", "Enable Secret"):
        assert redact.redact_cell(header, "plain-value", counts) == "[REDACTED:secret]"
    assert redact.redact_cell("password", 1234) == "[REDACTED:secret]"
    assert redact.redact_cell("password", "") == ""
    assert redact.redact_cell("password", "[REDACTED:secret]") == "[REDACTED:secret]"
    assert counts == {"secret": 6}
    # other columns keep their value but are still scanned for secrets inside it
    assert redact.redact_cell("hostname", "sw1") == "sw1"
    assert redact.redact_cell("notes", "enable secret 5 $1$x$y") == "enable secret 5 [REDACTED:password]"
    assert redact.redact_cell("serial", 12345) == 12345


def test_both_scrubbers_agree_on_a_fortigate_and_a_palo_alto_backup():
    from executor.tests import test_redaction as executor_samples

    for text in (executor_samples.FORTIOS_CONFIG, executor_samples.PANOS_CONFIG):
        out, _ = redact.redact_text(text)
        assert not [s for s in executor_samples.CONFIG_SECRETS if s in out], out
        assert redact.redact_text(out)[0] == out  # idempotent
    forti, counts = redact.redact_text(executor_samples.FORTIOS_CONFIG)
    assert counts["community"] == 1
    for kept in ('set hostname "FW-Edge1"', 'set name "nms-host"', "set ip 10.0.0.5 255.255.255.255", "set status enable"):
        assert kept in forti, kept  # only the community's own name is masked, not its host entry's
    assert "action>allow" in redact.redact_text(executor_samples.PANOS_CONFIG)[0]


_S = "Zq9Fake"


@pytest.mark.parametrize("line", [
    f"radius server R1\n address ipv4 10.0.0.1\n key {_S}",
    f" key 8 {_S}",
    f" server-private 10.1.1.1 key {_S}",
    f" standby 1 authentication {_S}",
    f" vrrp 1 authentication text {_S}",
    f"  authentication text {_S}",
    f" ip nhrp authentication {_S}",
    f" isis password {_S}",
    f" domain-password {_S}",
    f"snmp-server host 10.0.0.1 vrf MGMT version 2c {_S}",
    f"ntp authentication-key 1 sha1 7 {_S}",
    f"aaa root secret sha512 {_S}",
    f'    secret "{_S}";',
    f'    privacy-key "{_S}";',
    f"    set passphrase {_S}",
    f"    set psk {_S}",
])
def test_forms_that_used_to_reach_the_model(line):
    once = redact.redact_text(line)[0]
    assert _S not in once and redact.redact_text(once)[0] == once


@pytest.mark.parametrize("line", [
    f"ansible_httpapi_pass: {_S}",
    f"ansible_ssh_pass={_S}",
    f"ansible_become_pass: '{_S}'",
    f'"ansible_httpapi_pass": "{_S}"',
    f'        "passkey": "{_S}",',
    f"sudo config tacacs passkey {_S}",
    f"TACPLUS global passkey {_S}",
    f"failover key {_S}",
    f"failover key hex {_S}",
    f"enable algorithm-type scrypt secret {_S}",
    f"enable secret sha512 {_S}",
    f"username admin role network-admin secret sha512 {_S}",
    f"key config-key password-encrypt {_S}",
    f" security wpa psk set-key ascii 0 {_S}",
    f"set system radius-server 10.0.0.1 secret {_S}",
    f"set system tacplus-server 10.0.0.1 secret {_S}",
    f"ip ftp password {_S}",
    f"ip http client password {_S}",
    f" ppp chap password {_S}",
    f" l2tp tunnel password {_S}",
    f"cts sxp default password {_S}",
    f"set system login user x authentication plain-text-password-value {_S}",
    f"set protocols ospf area 0 interface ge-0/0/0.0 authentication md5 1 key {_S}",
    f"set system ntp authentication-key 1 type md5 value {_S}",
    f"local-user admin password irreversible-cipher $1a${_S}$",
    f"snmp-agent community read cipher %^%#{_S}%^%#",
    f"radius-server shared-key cipher %$%${_S}%$%$",
    f" ospf authentication-mode md5 1 cipher {_S}",
    f" set authentication password simple {_S}",
])
def test_more_forms_that_reached_the_model(line):
    """Found by running the command references of the platforms the catalogue has playbooks for through both
    scrubbers (October 2026). Each of these came back with its value."""
    once = redact.redact_text(line)[0]
    assert _S not in once and redact.redact_text(once)[0] == once


def test_the_rules_of_october_2026_leave_ordinary_lines_alone():
    text = (
        "failover\nno failover\nfailover lan unit primary\nbypass: true\npassed: 12 failed: 0\n"
        "the radius server answered\nntp authentication-key 1 type md5\n authentication md5 1\nkey config-key\n"
        "ssl cipher tlsv1.2 high\nssh cipher encryption high\n"
    )
    assert redact.redact_text(text)[0] == text


def test_the_new_rules_leave_ordinary_lines_alone():
    text = (
        "key chain OSPF\n key 1\nno secret configured\nstandby 1 authentication md5 key-chain K\nstandby 1 priority 110\n"
        "Key vault: suggest `kv-payments-web-prod-001`.\n key vault names are short\n"
    )
    assert redact.redact_text(text)[0] == text


@pytest.mark.parametrize("header", ["PW", "Enable", "SNMP RO", "RO String", "Creds", "passcode"])
def test_more_secret_shaped_columns(header):
    assert redact.redact_cell(header, "abc123") == "[REDACTED:secret]"
    assert redact.redact_cell("Enabled", "yes") == "yes"

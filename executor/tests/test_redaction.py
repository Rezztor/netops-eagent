"""FortiOS ENC, SNMPv3, AAA keys and ansible_password go from output but stay in approved commands."""
import pytest

from executor.redaction import redact

SHAPES = [
    ("set password ENC SH2FakeHash", "SH2FakeHash"),
    ("set psksecret ENC PSK123456", "PSK123456"),
    ("set passwd ENC Zq9FortiPasswd", "Zq9FortiPasswd"),
    ("snmp-server user u g v3 auth sha AK123456 priv aes 128 PK123456", "AK123456"),
    ("snmp-server user u g v3 auth sha AK123456 priv aes 128 PK123456", "PK123456"),
    ("tacacs-server host 1.1.1.1 key 7 TK123456", "TK123456"),
    ("ansible_password: Zq9-fake-Pass-1234", "Zq9-fake-Pass-1234"),
    ('"ansible_password": "Zq9 fake"', "Zq9 fake"),
    # Cisco IOS forms; the lab 3725 holds only enable/username passwords, these come from the CLI reference
    ("enable password level 5 7 0822455D0A16", "0822455D0A16"),
    ("enable secret level 5 5 $1$zz$LevelHash", "$1$zz$LevelHash"),
    ("snmp-server host 10.1.1.1 informs version 2c TrapComm1", "TrapComm1"),
    (" key 7 RadKey123", "RadKey123"),
    ("  key-string 7 KeyStr123", "KeyStr123"),
    ("ntp authentication-key 1 md5 NtpKey123 7", "NtpKey123"),
    (" ip ospf message-digest-key 1 md5 OspfKey123", "OspfKey123"),
    (" ip ospf authentication-key OspfPlain1", "OspfPlain1"),
    ("crypto isakmp key IsaKey123 address 1.2.3.4", "IsaKey123"),
    (" pre-shared-key address 1.1.1.1 key PskKey123", "PskKey123"),
    (" standby 1 authentication HsrpPlain1", "HsrpPlain1"),
    (" vrrp 1 authentication text VrrpKey123", "VrrpKey123"),
    # Forms that still reached the store in October 2026: the key of an IOS-XE `tacacs server` block has no type
    # digit, and the rest are words no rule knew.
    ("tacacs server T1\n address ipv4 10.0.0.1\n key BlockKey123", "BlockKey123"),
    (" server-private 10.0.0.1 key PrivKey123", "PrivKey123"),
    ("ntp authentication-key 1 sha1 NtpSha123", "NtpSha123"),
    (" wpa-psk ascii 0 WifiPsk123", "WifiPsk123"),
    (" security wpa psk set-key ascii 0 WlcPsk123", "WlcPsk123"),
    ('pre-shared-key ascii-text "JunosPsk123";', "JunosPsk123"),
    ("set security ike policy p pre-shared-key ascii-text JunosSet123", "JunosSet123"),
    ("ansible_httpapi_pass: HttpApi123", "HttpApi123"),
    ("sudo config tacacs passkey SonicKey123", "SonicKey123"),
    ("TACPLUS global passkey SonicShow123", "SonicShow123"),
    ("failover key AsaFail123", "AsaFail123"),
    ("failover key hex 0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef"),
    ("key config-key password-encrypt MasterKey123", "MasterKey123"),
    ("set system login user x authentication plain-text-password-value JunosPlain123", "JunosPlain123"),
    ("set protocols ospf area 0 interface ge-0/0/0.0 authentication md5 1 key OspfJunos123", "OspfJunos123"),
    ("set system ntp authentication-key 1 type md5 value NtpJunos123", "NtpJunos123"),
    ("local-user admin password irreversible-cipher $1a$Vrp$HashedVrp123$", "HashedVrp123"),
    ("snmp-agent community read cipher %^%#VrpCommunity123%^%#", "VrpCommunity123"),
    ("radius-server shared-key cipher %$%$VrpShared123%$%$", "VrpShared123"),
    (" ospf authentication-mode md5 1 cipher VrpOspf123", "VrpOspf123"),
    (" set authentication password simple VrpSimple123", "VrpSimple123"),
    (" snmp-agent usm-user v3 u authentication-mode sha cipher %#%#VrpAuth123%#%#", "VrpAuth123"),
]

NOT_SECRETS = [  # lines that name a key without holding one must read the same afterwards
    "no service password-encryption", " key 1", "key chain K", " ip authentication key-chain eigrp 1 K",
    " standby 1 authentication md5 key-chain K", " ip ospf authentication message-digest", "snmp-server enable traps",
    " key chain OSPF", "failover", "no failover", " key 12", "ssl cipher tlsv1.2 high", "ssh cipher encryption high",
]


@pytest.mark.parametrize("line,secret", SHAPES)
def test_output_loses_the_value_but_commands_keep_it(line, secret):
    assert secret not in redact(line)
    assert redact(line, keep_operational=True) == line


@pytest.mark.parametrize("line", NOT_SECRETS)
def test_a_line_naming_a_key_without_holding_one_is_left_alone(line):
    assert redact(line) == line


def test_an_snmp_community_stored_as_a_key_is_masked_in_every_layout():
    """SONiC's config_db: `"SNMP_COMMUNITY": {"<community>": {...}}`. Found on a live backup review."""
    from executor import redaction

    pretty = (
        '{\n    "DEVICE_METADATA": {\n        "localhost": {\n            "hwsku": "Force10-S6000"\n        }\n    },\n'
        '    "SNMP_COMMUNITY": {\n        "MyComm0nity": {\n            "TYPE": "RO"\n        },\n'
        '        "Wr1teComm": {\n            "TYPE": "RW"\n        }\n    },\n'
        '    "VLAN": {\n        "Vlan10": {\n            "vlanid": "10"\n        }\n    }\n}'
    )
    one_line = '{"SNMP_COMMUNITY": {"MyComm0nity": {"TYPE": "RO"}, "Wr1teComm": {"TYPE": "RW"}}, "VLAN": {"Vlan10": {"vlanid": "10"}}}'
    quoted = '"    \\"SNMP_COMMUNITY\\": {",\n"        \\"MyComm0nity\\": {",\n"            \\"TYPE\\": \\"RO\\"",\n"        }",\n"    },",\n"    \\"VLAN\\": {",\n"        \\"Vlan10\\": {"'
    for text in (pretty, one_line, quoted):
        out = redaction.redact(text)
        assert "MyComm0nity" not in out and "Wr1teComm" not in out, out
        assert "Vlan10" in out and "TYPE" in out and "SNMP_COMMUNITY" in out, out  # nothing else is touched
    assert "MyComm0nity" in redaction.redact(one_line, keep_operational=True)  # the approver's view keeps it
    assert redaction.redact('{"VLAN": {"Vlan10": {}}}') == '{"VLAN": {"Vlan10": {}}}'


# FortiOS config as `GET /api/v2/monitor/system/config/backup` returns it; PAN-OS as `type=export&category=configuration`.
# Layouts follow the vendors' documentation (the FortiOS CLI reference and the PAN-OS XML API guide); values are invented.
FORTIOS_CONFIG = """#config-version=FGT60F-7.2.4-FW-build1396-230106:opmode=0:vdom=0:user=apiadmin
config system global
    set hostname "FW-Edge1"
end
config system admin
    edit "admin"
        set accprofile "super_admin"
        set password ENC SH2zq9AdminHashAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==
    next
end
config system snmp community
    edit 1
        set name "Zq9PublicCommunity"
        config hosts
            edit 1
                set ip 10.0.0.5 255.255.255.255
                set name "nms-host"
            next
        end
        set status enable
    next
end
config system snmp user
    edit "monitor"
        set auth-pwd ENC Zq9AuthPwdENCVALUE==
        set priv-pwd ENC Zq9PrivPwdENCVALUE==
    next
end
config vpn ipsec phase1-interface
    edit "to-hq"
        set interface "port1"
        set psksecret ENC Zq9PskSecretENCVALUE==
    next
end
config user radius
    edit "rad"
        set secret ENC Zq9RadiusENCVALUE==
    next
end
config system api-user
    edit "apiadmin"
        set api-key ENC Zq9ApiKeyENCVALUE==
    next
end
"""
PANOS_CONFIG = """<config version="10.2.0" urldb="paloaltonetworks" detail-version="10.2.4">
  <mgt-config><users><entry name="admin"><phash>$5$Zq9saltSALT$Zq9PhashVALUEHASHhashHASH</phash></entry></users></mgt-config>
  <devices><entry name="localhost.localdomain"><deviceconfig><system><hostname>PA-Edge1</hostname>
    <snmp-setting><access-setting><version><v2c><snmp-community-string>Zq9SnmpCommunity</snmp-community-string></v2c>
    <v3><views/><users><entry name="u"><authpwd>x</authpwd></entry></users></v3></version></access-setting></snmp-setting>
  </system></deviceconfig>
  <network><ike><gateway><entry name="gw"><authentication><pre-shared-key><key>-AQ==Zq9PresharedKeyVALUE</key></pre-shared-key></authentication></entry></gateway></ike></network>
  <vsys><entry name="vsys1"><rulebase><security><rules><entry name="allow-web"><action>allow</action></entry></rules></security></rulebase></entry></vsys>
  </entry></devices>
</config>
"""
CONFIG_SECRETS = [
    "SH2zq9AdminHash", "Zq9PublicCommunity", "Zq9AuthPwdENCVALUE", "Zq9PrivPwdENCVALUE", "Zq9PskSecretENCVALUE",
    "Zq9RadiusENCVALUE", "Zq9ApiKeyENCVALUE", "Zq9PhashVALUE", "Zq9SnmpCommunity", "Zq9PresharedKeyVALUE",
]


def test_a_fortigate_and_a_palo_alto_backup_lose_every_secret_and_keep_the_rest():
    from executor import redaction

    for text in (FORTIOS_CONFIG, PANOS_CONFIG):
        shown = redaction.redact(redaction._mask_keyed_tables(text))
        assert not [secret for secret in CONFIG_SECRETS if secret in shown], shown
        assert "[redacted]" in shown
    forti = redaction.redact(FORTIOS_CONFIG)
    for kept in ('set hostname "FW-Edge1"', 'set accprofile "super_admin"', "config system snmp community", "edit 1",
                 'set name "nms-host"', "set ip 10.0.0.5 255.255.255.255", "set status enable", "set interface \"port1\""):
        assert kept in forti, kept  # only the community's own `set name` is masked, not the host entry's
    assert 'set name "Zq9' not in forti and "action>allow" in redaction.redact(PANOS_CONFIG)


def test_a_changed_secret_in_a_backup_diff_is_a_changed_line_without_its_value(tmp_path, monkeypatch):
    from executor import backups, credentials

    monkeypatch.setenv(credentials.ENV_KEY, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    store = backups.BackupStore(str(tmp_path))
    old = store.save("fgt1", FORTIOS_CONFIG)
    new = store.save("fgt1", FORTIOS_CONFIG.replace("Zq9PskSecretENCVALUE", "Zq9NewPskSecretVALUE").replace("Zq9PublicCommunity", "Zq9OtherCommunity"))
    shown = store.diff("fgt1", old["id"], new["id"])
    assert not [word for word in ("Zq9PskSecret", "Zq9NewPsk", "Zq9PublicCommunity", "Zq9OtherCommunity") if word in shown["diff"]]
    assert shown["changed_lines"] >= 2 and "[redacted]" in shown["diff"]

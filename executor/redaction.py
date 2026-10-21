"""What the executor refuses to write down.

Everything a job produces passes through here before it reaches the store, which
means it also reaches the console and, for a change, the approval view. The
interesting question is not what to redact. It is what to leave alone.

**Approval reads the commands.** ADR 0030 and the plan both rest on an engineer
approving the exact command lines a change would push. If `snmp-server community
FINANCE-RO` is stored as `snmp-server community ****`, the approver can no longer
tell a correct value from a wrong one, and condition 2 becomes a ritual. So the
commands an approver reads keep their operational values.

**Nothing needs to read key material.** A private key block, an Ansible vault
blob or a bearer token in a command line is not something anyone reviews
character by character -- its presence is the finding, not its content. Those are
removed everywhere, including from the commands.

**Raw stdout is not an approval artifact.** It is a debugging convenience, and it
is where a missing `no_log` and a verbose module dump end up. It gets the full
treatment.

That split is the whole design, and it is why this module has two functions
rather than one.
"""

from __future__ import annotations

import re
from typing import Any

from executor import secret_env

MASK = "[redacted]"

#: Never readable, anywhere. Their presence is the finding, not their content.
STRUCTURAL_SECRETS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "private key",
        re.compile(
            r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("ansible vault", re.compile(r"\$ANSIBLE_VAULT;[0-9.]+;[A-Z0-9]+(?:\s*[0-9a-f]{2,})+")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("authorization header", re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+")),
)

#: Readable by an approver in a command line, removed from raw output. A device
#: password is exactly the thing condition 2 exists to let someone check.
OPERATIONAL_SECRETS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # Huawei VRP puts a word between the keyword and the value (`password cipher X`, `community read cipher X`,
    # `authentication-mode md5 1 cipher X`). First in the list: the rules below would mask that word and keep X.
    # A bare `cipher` counts only before something shaped like VRP's ciphertext, so `ssl cipher tlsv1.2` stays.
    (
        "vrp cipher",
        re.compile(
            r"(?i)(?:\b(?:password|key|community(?:[ \t]+(?:read|write))?|(?:authentication|privacy)-mode[ \t]+\S+(?:[ \t]+\d+)?)"
            r"[ \t]+(?:irreversible-cipher|cipher|simple|plain)|\b(?:irreversible-)?cipher(?=[ \t]+(?:[%$]|\S{20,})))"
            r"[ \t]+(?P<value>\S+)"
        ),
    ),
    (
        "password",
        # `ENC` is FortiOS's "the next token is a hash", never the value itself.
        re.compile(r"(?i)\b(password|passwd|pwd)\b(\s+level\s+\d+)?(\s+\d+)?(\s+ENC)?\s+(?P<value>\S+)"),
    ),
    ("secret", re.compile(r"(?i)\bsecret\b(\s+level\s+\d+)?(\s+\d+)?(\s+(?:ENC|sha512|scrypt))?\s+(?P<value>\S+)")),
    # PAN-OS XML.
    (
        "xml secret",
        re.compile(
            r"(?i)<(?:password|passwd|phash|key|secret|psk|pre-shared-key|community-name|auth-password"
            r"|priv-password|private-key|token|api-key|snmp-community-string|bind-password|shared-secret"
            r"|passphrase|auth-key|private-key-password)>(?P<value>[^<]+)"
        ),
    ),
    # FortiOS: any `set <name> ENC <hash>` (password, passwd, psksecret, auth-pwd, secret, key, apikey ...).
    ("fortios enc", re.compile(r"(?i)\bset\s+\S+\s+ENC\s+(?P<value>\S+)")),
    # FortiOS names with no word boundary before `secret`/`key`.
    (
        "fortios secret",
        re.compile(r"(?i)\bset\s+(?:psksecret(?:-remote)?|auth-pwd|priv-pwd|key)\s+(?:ENC\s+)?(?P<value>\S+)"),
    ),
    (
        "snmpv3 auth key",
        re.compile(r"(?i)\bsnmp-server\s+user\b[^\n]{0,256}?\bauth\s+(?:(?:md5|sha-?\d*)\s+)?(?P<value>\S+)"),
    ),
    (
        "snmpv3 priv key",
        re.compile(
            r"(?i)\bsnmp-server\s+user\b[^\n]{0,256}?\bpriv\s+(?:(?:aes-128|aes\s+\d{3}|aes|3des|des)\s+)?(?P<value>\S+)"
        ),
    ),
    ("aaa key", re.compile(r"(?i)\b(?:tacacs|radius|server-private)[^\n]{0,256}?\bkey\s+(?:\d\s+)?(?P<value>\S+)")),
    (
        "ansible password",
        re.compile(
            r"(?i)\bansible_\w{0,24}pass(?:word)?[\"']?\s*[:=]\s*"
            r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,}]+)"
        ),
    ),
    ("snmp community", re.compile(r"(?i)\bcommunity[ \t]+(?P<value>\S+)")),  # not across a newline: FortiOS ends a line with `community`
    # JSON config (SONiC `config_db.json`): `"passkey": "..."`, `"community": "..."`.
    (
        "json secret",
        re.compile(
            r'(?i)"[^"\n]*(?:password|passwd|passkey|secret|psk|token|community|key)[^"\n]*"\s*:\s*"(?P<value>[^"\n]+)'
        ),
    ),
    (
        "pre-shared key",
        re.compile(r"(?i)\bpre-shared-key\s+(?:(?:local|remote|ascii-text|hexadecimal|address\s+\S+(?:\s+\S+){0,8}?\s+key)\s+)?(?:\d\s+)?(?P<value>\S+)"),
    ),
    ("key string", re.compile(r"(?i)\bkey-string\s+(?:\d\s+)?(?P<value>\S+)")),
    # IOS keys that no `password`/`secret`/`key-string` word announces. Found by reviewing the Cisco
    # forms a real config can hold; the lab 3725's config has none of them.
    ("ios block key", re.compile(r"(?im)^\s*key\s+\d\s+(?P<value>\S+)")),
    # The same line with no type digit, under `tacacs server` / `radius server`: lower case, indented, the whole
    # line. Never `key chain X` or a key id (`key 1`).
    ("ios block key, no type", re.compile(r"(?m)^[ \t]+key[ \t]+(?!chain\b|config-key\b|\d+[ \t]*$)(?P<value>\S+)(?=[ \t]*$)")),
    ("junos ntp key", re.compile(r"(?i)\bauthentication-key[ \t]+\d+[ \t]+type[ \t]+\S+[ \t]+value[ \t]+(?P<value>\S+)")),
    (
        "ios authentication key",
        re.compile(r"(?i)\b(?:authentication-key|message-digest-key)\s+(?:\d+\s+)?(?:(?:md5|sha1|sha256|sha512)\s+)?(?:\d\s+)?(?!type\b)(?P<value>\S+)"),
    ),
    ("junos ospf key", re.compile(r"(?i)\bmd5[ \t]+\d+[ \t]+key[ \t]+(?P<value>\S+)")),
    ("junos plain password", re.compile(r"(?i)\bplain-text-password-value[ \t]+(?P<value>\S+)")),
    ("wireless key", re.compile(r"(?i)\b(?:wpa-psk|set-key)[ \t]+(?:ascii|hex)[ \t]+(?:\d[ \t]+)?(?P<value>\S+)")),
    ("sonic passkey", re.compile(r"(?i)\bpasskey[ \t]+(?P<value>\S+)")),
    ("asa failover key", re.compile(r"(?i)\bfailover[ \t]+key[ \t]+(?:hex[ \t]+)?(?:\d[ \t]+)?(?P<value>\S+)")),
    ("ios master key", re.compile(r"(?i)\bkey[ \t]+config-key[ \t]+password-encrypt(?:ion)?[ \t]+(?P<value>\S+)")),
    ("isakmp key", re.compile(r"(?i)\bisakmp\s+key\s+(?:\d\s+)?(?P<value>\S+)")),
    (
        "snmp trap community",
        re.compile(r"(?i)\bsnmp-server\s+host\s+\S+\s+(?:(?:traps|informs|vrf\s+\S+|version\s+(?:1|2c|3(?:\s+(?:auth|noauth|priv))?))\s+)*(?P<value>\S+)"),
    ),
    ("first-hop authentication", re.compile(r"(?i)\b(?:standby|vrrp)\s+\d+\s+authentication\s+(?:text\s+)?(?!md5\b)(?P<value>\S+)")),
)

#: Environment variables whose values must never appear in anything stored, even
#: if something contrives to print them.
SECRET_ENV_VARS = (
    "REZZTOR_ANSIBLE_EXECUTOR_TOKEN",
    "REZZTOR_ANSIBLE_ADMIN_TOKEN",
    "REZZTOR_ANSIBLE_CREDENTIAL_KEY",
)

#: Below this length an env value is more likely to collide with ordinary text
#: than to be the secret, and blanket-replacing "1" would corrupt everything.
MIN_ENV_SECRET_LENGTH = 8


#: SONiC's config_db names an SNMP community as a *key*
#: (`"SNMP_COMMUNITY": {"<community>": {"TYPE": "RO"}}`), which no key/value rule can
#: see. Every key directly under one of these tables is masked.
KEYED_SECRET_TABLES = ("SNMP_COMMUNITY",)
_QUOTE = r'\\?"'  # a quote, bare or backslash-escaped inside an Ansible-quoted line
_TABLE_OPEN = re.compile(rf'(?:{"|".join(KEYED_SECRET_TABLES)}){_QUOTE}\s*:\s*\{{')
_KEY_OR_BRACE = re.compile(rf'({_QUOTE})([^"\\{{}}]+)({_QUOTE}\s*:\s*\{{)|(\{{)|(\}})')


_FORTIOS_COMMUNITY_NAME = re.compile(r"^(\s*set\s+name\s+)\S.*$")


def _mask_fortios_snmp_names(text: str) -> str:
    """FortiOS keeps an SNMP community as `set name "<community>"` inside `config system snmp community`,
    which no line rule can tell from any other `set name`. Counts `config`/`end` from the block's opening line;
    only a name directly under the block (depth 1) is masked, never a nested `config hosts` entry."""
    if "config system snmp community" not in text:
        return text
    depth, lines = 0, []
    for line in text.split("\n"):
        word = line.strip()
        if depth == 0:
            depth = 1 if word == "config system snmp community" else 0
        elif word.startswith("config "):
            depth += 1
        elif word == "end":
            depth -= 1
        elif depth == 1:
            line = _FORTIOS_COMMUNITY_NAME.sub(rf"\g<1>{MASK}", line)
        lines.append(line)
    return "\n".join(lines)


def _mask_keyed_tables(text: str) -> str:
    """Block-aware masks that need the whole text: SONiC's keyed SNMP table and FortiOS's community names."""
    return _mask_fortios_snmp_names(_mask_sonic_tables(text))


def _mask_sonic_tables(text: str) -> str:
    """Mask the keys of `KEYED_SECRET_TABLES`, on one line or spread over many.

    Counts braces from the table's opening one. Output that stops mid-table leaves the
    count open, which masks more than it should rather than less.
    """
    if not _TABLE_OPEN.search(text):
        return text
    depth, lines = 0, []
    for line in text.split("\n"):
        pos, built = 0, []
        while True:
            if depth == 0:
                opened = _TABLE_OPEN.search(line, pos)
                if not opened:
                    built.append(line[pos:])
                    break
                built.append(line[pos:opened.end()])
                pos, depth = opened.end(), 1
                continue
            found = _KEY_OR_BRACE.search(line, pos)
            if not found:
                built.append(line[pos:])
                break
            built.append(line[pos:found.start()])
            if found.group(5):
                depth -= 1
                built.append("}")
            elif found.group(4):
                depth += 1
                built.append("{")
            else:
                built.append(found.group(1) + (MASK if depth == 1 else found.group(2)) + found.group(3))
                depth += 1
            pos = found.end()
        lines.append("".join(built))
    return "\n".join(lines)


def _mask_value(match: "re.Match[str]") -> str:
    """Replace only the captured value, so the surrounding line stays readable.

    `password [redacted]` says a password was set. `[redacted]` says nothing.
    """
    text = match.group(0)
    return text[: match.start("value") - match.start(0)] + MASK


def redact(text: str, *, keep_operational: bool = False) -> str:
    """Scrub secrets from a piece of text.

    `keep_operational=True` leaves device passwords and community strings alone,
    for the one case that has to read them: the command lines an engineer
    approves. Key material is removed either way.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""

    for _name, pattern in STRUCTURAL_SECRETS:
        text = pattern.sub(MASK, text)

    if not keep_operational:
        for _name, pattern in OPERATIONAL_SECRETS:
            text = pattern.sub(_mask_value, text)
        text = _mask_keyed_tables(text)

    # Last, and unconditionally: a token that reached the output is a bug
    # somewhere else, and this is not the place to let it through while that is
    # being found.
    for name in SECRET_ENV_VARS:
        try:
            value = secret_env.get(name)
        except secret_env.SecretConfigError:
            value = ""
        if len(value) >= MIN_ENV_SECRET_LENGTH:
            text = text.replace(value, MASK)
    return text


def redact_structure(value: Any, *, keep_operational: bool = False) -> Any:
    """Walk a decoded JSON structure, scrubbing every string in it.

    ansible-runner's events are nested dicts and lists whose shape depends on the
    module, so this recurses rather than naming fields. Naming fields would mean
    a module that reports its result under a key nobody anticipated slips through
    unredacted, which is precisely the case that matters.
    """
    if isinstance(value, str):
        return redact(value, keep_operational=keep_operational)
    if isinstance(value, dict):
        return {
            key: redact_structure(item, keep_operational=keep_operational)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, keep_operational=keep_operational) for item in value]
    return value


def redact_job_result(result: dict[str, Any]) -> dict[str, Any]:
    """The one call site that matters: everything a job produces, on its way in.

    `events` keeps operational values because `commands` lives inside them and is
    what an approver reads. `output` and `error` do not, because neither is an
    approval artifact and stdout is where a missing `no_log` ends up.
    """
    scrubbed = dict(result)
    if "events" in scrubbed:
        scrubbed["events"] = redact_structure(scrubbed["events"], keep_operational=True)
    for field in ("output", "error"):
        if field in scrubbed:
            scrubbed[field] = redact(str(scrubbed[field] or ""))
    return scrubbed

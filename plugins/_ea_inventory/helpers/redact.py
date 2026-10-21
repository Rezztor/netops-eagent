"""Strip secrets from text and table cells before a model, a tool result, a
proposal or the audit log can see them. Pure, idempotent, never raises: if
redaction itself fails the whole text is replaced, never passed through.

Each rule keeps the keyword and replaces only the value with `[REDACTED:<type>]`.
Every value pattern refuses to start with an existing token, which is what makes
a second pass a no-op. Optional encryption-type digits use possessive groups
(`?+`) so a re-run cannot backtrack onto the digit and redact it instead.

FortiOS names an SNMP community as `set name "<community>"` inside `config system snmp community`, which
only block context can tell from any other `set name`, so `_mask_fortios_snmp_names` counts `config`/`end`.
executor/redaction.py masks the same secrets for fortios and panos; keep the two in step.
"""

from __future__ import annotations

import re

_W = r"[ \t]+"  # never `\s`: a value must stay on its own line
_LVL = r"(?:\d" + _W + r")?+"  # Cisco encryption type: 0 plain, 5/8/9 hash, 7 reversible
_NOT = r'(?!["\']?\[REDACTED)'
# The most a rule looks past its keyword for the rest of itself, on one line. Without a bound, `keyword, then
# anything, then key` scans to the end of the line from every keyword on it, and a long line of keywords costs
# the square of its length: this runs on every chat message, on the event loop. No config line needs more.
_GAP = r"[^\n]{0,256}?"
_VAL = _NOT + r'(?P<val>"[^"\n]*"|\'[^\'\n]*\'|[^\s]+?(?=;?(?:\s|$)))'


def _r(kind: str, pattern: str, flags: int = re.I) -> tuple[str, re.Pattern]:
    return kind, re.compile(pattern, flags | re.M)


_KW = r"(?:password|passwd|passkey|pwd|secret|token|api[_-]?key|community|psk|private[_-]?key|(?<=_)pass(?=[\"']?[ \t]*[:=]))"
RULES: list[tuple[str, re.Pattern]] = [
    # --- blocks and standalone shapes first
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # --- Huawei VRP: a word between the keyword and the value. A bare `cipher` only before VRP-shaped ciphertext.
    _r("password", rf"(?P<pre>(?:\b(?:password|key|community(?:{_W}(?:read|write))?|(?:authentication|privacy)-mode{_W}\S+(?:{_W}\d+)?)"
                   rf"{_W}(?:irreversible-cipher|cipher|simple|plain)|\b(?:irreversible-)?cipher(?={_W}(?:[%$]|\S{{20,}}))){_W}){_VAL}"),
    # --- Cisco IOS / NX-OS / ASA
    # `sha512` is EOS's word for "a hash follows"; `algorithm-type scrypt` is how IOS is told to make one.
    _r("password", rf"(?P<pre>\benable{_W}(?:algorithm-type{_W}\S+{_W})?+(?:secret|password){_W}(?:level{_W}\d+{_W})?+(?:sha512{_W})?+{_LVL}){_VAL}"),
    _r("password", rf"(?P<pre>\busername{_W}\S+{_GAP}\b(?:secret|password){_W}(?:sha512{_W})?+{_LVL}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:ip{_W}ftp|ip{_W}http{_W}client|ppp{_W}chap|l2tp{_W}tunnel|cts{_W}sxp{_W}default){_W}password{_W}{_LVL}){_VAL}"),
    _r("password", rf"(?P<pre>^[ \t]*(?:password|passwd){_W}{_LVL}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:password|passwd){_W}[0-7]{_W}){_VAL}"),
    _r("community", rf"(?P<pre>\bsnmp[\w-]*{_W}(?:[\w-]+{_W}){{0,8}}?community{_W}){_VAL}"),
    _r("community", rf"(?P<pre>\bsnmp-server{_W}host{_W}\S+(?:{_W}(?:traps|informs|vrf{_W}\S+|version{_W}\S+))*+{_W}){_VAL}"),
    _r("community", r"(?P<pre>^[ \t]*community[ \t]+)" + _NOT + r"(?P<val>[^\s{;]+)(?=[ \t]*\{)"),  # Junos hierarchy
    _r("password", rf"(?P<pre>\bsnmp-server{_W}user\b{_GAP}\bauth{_W}(?:(?:md5|sha-?\d*){_W})?+){_VAL}"),
    _r("password", rf"(?P<pre>\bsnmp-server{_W}user\b{_GAP}\bpriv{_W}(?:(?:aes-128|aes{_W}\d{{3}}|aes|3des|des){_W})?+){_VAL}"),
    _r("key", rf"(?P<pre>\b(?:tacacs|radius|server-private){_GAP}\bkey{_W}{_LVL}){_VAL}"),
    _r("key", rf"(?P<pre>\b(?:tacplus|radius)-server{_W}\S+{_W}secret{_W}){_VAL}"),  # Junos, set style
    _r("key", rf"(?P<pre>\bpasskey{_W}){_VAL}"),  # SONiC: `config tacacs passkey`, `TACPLUS global passkey`
    _r("key", rf"(?P<pre>\bfailover{_W}key{_W}(?:hex{_W})?+{_LVL}){_VAL}"),  # ASA
    _r("key", rf"(?P<pre>\bkey{_W}config-key{_W}password-encrypt(?:ion)?{_W}){_VAL}"),  # the master key of type 6
    _r("key", rf"(?P<pre>(?<![\w-])key{_W}\d{_W}){_VAL}"),
    # An indented, lower-case `key <value>` that is the whole line (under `radius server` / `tacacs server`): never
    # `key chain X`, a key id (`key 1`) or prose such as "Key vault: ...".
    _r("key", rf"(?P<pre>^[ \t]+key{_W}(?!chain\b|config-key\b|\d+[ \t]*$){_LVL}){_VAL}(?=[ \t]*$)", 0),
    _r("key", rf"(?P<pre>\b(?:standby|vrrp|glbp){_W}\d+{_W}authentication{_W}(?!md5\b)(?:text{_W})?+){_VAL}"),
    _r("key", rf"(?P<pre>^[ \t]*authentication{_W}text{_W}){_VAL}"),  # NX-OS, inside `hsrp N`
    _r("key", rf"(?P<pre>\bnhrp{_W}authentication{_W}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:isis{_W}password|(?:domain|area)-password){_W}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:aaa{_W}root|boot){_W}secret{_W}(?:sha512{_W})?+{_LVL}){_VAL}"),  # EOS
    _r("psk", rf"(?P<pre>\bpre-shared-key{_W}(?:ascii-text|hexadecimal){_W}){_VAL}"),
    _r("psk", rf"(?P<pre>\bpre-shared-key{_GAP}\bkey{_W}{_LVL}){_VAL}"),
    _r("psk", rf"(?P<pre>\bpre-shared-key{_W}(?!(?:address|key|ascii-text|hexadecimal)\b)(?:(?:local|remote){_W})?+{_LVL}){_VAL}"),
    _r("psk", rf"(?P<pre>\bcrypto{_W}isakmp{_W}key{_W}{_LVL}){_VAL}"),
    _r("psk", rf"(?P<pre>\b(?:wpa-psk|set-key){_W}(?:ascii|hex){_W}{_LVL}){_VAL}"),
    _r("password", rf"(?P<pre>\bneighbor{_W}\S+{_W}password{_W}{_LVL}){_VAL}"),
    _r("key", rf"(?P<pre>\bauthentication-key{_W}\d+{_W}type{_W}\S+{_W}value{_W}){_VAL}"),  # Junos NTP
    _r("key", rf"(?P<pre>\bauthentication-key{_W}(?:\d+{_W}(?:md5|sha1|sha256|sha512){_W})?+{_LVL}(?!type\b)){_VAL}"),
    _r("key", rf"(?P<pre>\bmd5{_W}\d+{_W}key{_W}){_VAL}"),  # Junos OSPF: `authentication md5 1 key <key>`
    _r("key", rf"(?P<pre>\bmessage-digest-key{_W}\d+{_W}md5{_W}{_LVL}){_VAL}"),
    _r("key", rf"(?P<pre>\bkey-string{_W}{_LVL}){_VAL}"),
    # --- Junos
    _r("hash", rf"(?P<pre>\bencrypted-password{_W}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:(?:authentication|privacy|simple)-password|plain-text-password-value){_W}){_VAL}"),
    _r("password", rf"(?P<pre>\b(?:secret|privacy-key){_W})" + _NOT + r'(?P<val>"[^"\n]*")'),  # only quoted: `no secret configured` is prose
    ("hash", re.compile(r"\$(?:9|1|5|6|y|2[aby])\$[^\s\"<;]+")),
    # --- FortiOS: any `set <name> ENC <hash>` (api-key, ...) as well as the names that need no ENC
    _r("password", rf"(?P<pre>\bset{_W}(?:password|passwd|passphrase|psk|psksecret(?:-remote)?|secret|key|auth-pwd|priv-pwd){_W}(?:ENC{_W})?+){_VAL}"),
    _r("password", rf"(?P<pre>\bset{_W}\S+{_W}ENC{_W}){_VAL}"),
    # --- PAN-OS
    _r("hash", rf"(?P<pre>\bphash{_W}){_VAL}"),
    _r("password", r"(?P<pre><(?:password|passwd|phash|key|secret|psk|pre-shared-key|community-name|auth-password|priv-password|private-key|token|api-key|snmp-community-string|bind-password|shared-secret|passphrase|auth-key|private-key-password)>)" + _NOT + r"(?P<val>[^<]+)"),
    # --- generic
    _r("token", rf"(?P<pre>\b(?:Proxy-)?Authorization[ \t]*:[ \t]*(?:(?:Bearer|Basic|Token){_W})?+){_VAL}"),
    _r("token", rf"(?P<pre>\bBearer{_W}){_VAL}"),
    _r("secret", rf"(?P<pre>{_KW}[\w-]{{0,64}}[\"']?[ \t]*[:=][ \t]*){_VAL}"),
]


_FORTIOS_COMMUNITY_NAME = re.compile(r"^(?P<pre>[ \t]*set[ \t]+name[ \t]+)" + _NOT + r"(?P<val>\S.*?)[ \t]*$", re.M)


def _mask_fortios_snmp_names(text: str, counts: dict[str, int]) -> str:
    """Mask `set name` directly under `config system snmp community` (depth 1), never a nested `config hosts` entry."""
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
            line, n = _FORTIOS_COMMUNITY_NAME.subn(lambda m: m.group("pre") + "[REDACTED:community]", line)
            if n:
                counts["community"] = counts.get("community", 0) + n
        lines.append(line)
    return "\n".join(lines)


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Clean text and a count of what was removed, by type."""
    counts: dict[str, int] = {}
    try:
        out = text if isinstance(text, str) else ("" if text is None else str(text))
        out = _mask_fortios_snmp_names(out, counts)
        for kind, rx in RULES:
            def sub(m: re.Match, kind=kind) -> str:
                counts[kind] = counts.get(kind, 0) + 1
                if "val" not in rx.groupindex:
                    return f"[REDACTED:{kind}]"
                return m.group(0)[: m.start("val") - m.start()] + f"[REDACTED:{kind}]" + m.group(0)[m.end("val") - m.start():]
            out = rx.sub(sub, out)
        return out, counts
    except Exception:
        return "[REDACTED:unparseable]", {"unparseable": 1}


# Table columns redacted whole, whatever the cell holds.
SECRET_HEADERS = re.compile(
    r"password|passwd|passphrase|passcode|pwd|secret|community|token|psk|credential|(?<![a-z])pass(?![a-z])|(?<![a-z])key(?![a-z])"
    r"|(?<![a-z])(?:pw|creds|enable)(?![a-z])|r[ow][ _-]?string|snmp[ _-]?r[ow](?![a-z])",
    re.I,
)


def redact_cell(header: str, value, counts: dict[str, int] | None = None):
    """One table cell: whole if its column is secret-shaped, else text-redacted."""
    counts = counts if counts is not None else {}
    try:
        if value is None or not str(value).strip() or str(value).startswith("[REDACTED:"):
            return value
        if SECRET_HEADERS.search(str(header or "")):  # a numeric password is still one
            counts["secret"] = counts.get("secret", 0) + 1
            return "[REDACTED:secret]"
        if not isinstance(value, str):
            return value
        out, found = redact_text(value)
        for k, n in found.items():
            counts[k] = counts.get(k, 0) + n
        return out
    except Exception:
        return "[REDACTED:unparseable]"

"""Redaction must cost time in proportion to the text, whatever the text is.

Both redactors -- the application's (`plugins/_ea_inventory/helpers/redact.py`)
and the executor's (`executor/redaction.py`) -- had rules with an unbounded span
after their keyword: "`snmp-server user`, then anything on the line, then
`auth`". On a line that repeats the keyword and never completes the rule, every
occurrence scanned to the end of the line, so the cost was the square of the
line's length. The application's runs on every chat message as it is typed in,
on the event loop: 40 KB of `token-token-token-...` took four seconds, 400 KB
would take seven minutes, and nothing else was served meanwhile. Any signed-in
account can send that.
"""

import time

import pytest

from executor import redaction
from plugins._ea_inventory.helpers import redact

#: A keyword of each rule that had an unbounded span, repeated without the rest of the rule.
SEEDS = [
    "snmp-server user a ", "username a ", "snmp a ", "tacacs a ", "radius a ", "server-private a ",
    "pre-shared-key a ", "pre-shared-key address a ", "password", "secretsecret", "token-", "api_key_",
]
SIZE = 200_000
#: Seconds. After the bound these take a few hundredths; before it the fastest took 10 and the slowest 100.
BUDGET = 3.0


@pytest.mark.parametrize("redactor", [lambda text: redact.redact_text(text), redaction.redact], ids=["application", "executor"])
@pytest.mark.parametrize("seed", SEEDS)
def test_a_long_line_of_keywords_costs_no_more_than_its_length(redactor, seed):
    text = seed * (SIZE // len(seed))
    started = time.perf_counter()
    redactor(text)
    assert time.perf_counter() - started < BUDGET


@pytest.mark.parametrize(
    "line, secret",
    [
        ("username admin privilege 15 role network-admin secret 9 $9$abcdefgh", "$9$abcdefgh"),
        ("snmp-server user monitor GROUP v3 auth sha AuthPass123 priv aes 128 PrivPass123", "AuthPass123"),
        ("snmp-server user monitor GROUP v3 auth sha AuthPass123 priv aes 128 PrivPass123", "PrivPass123"),
        ("snmp-server group ops v3 priv community S3cretRO", "S3cretRO"),
        ("tacacs-server host 10.1.1.1 timeout 5 key 7 0822455D0A16", "0822455D0A16"),
        ("radius-server host 10.1.1.2 auth-port 1812 acct-port 1813 key MyRadKey", "MyRadKey"),
        ("crypto isakmp key 0 MyIkeKey address 203.0.113.9", "MyIkeKey"),
        ("api_key_for_the_gateway = abcdef123456", "abcdef123456"),
    ],
)
def test_the_bounded_rules_still_find_the_secret_on_a_real_line(line, secret):
    assert secret not in redact.redact_text(line)[0]

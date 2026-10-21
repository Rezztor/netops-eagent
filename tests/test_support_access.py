"""A closed console and the one way through it (ADR 0083).

An ISO that carries a support key installs a machine whose console account
gets the setup menu and nothing else. A root shell opens from that menu for a
code Rezztor signed for the challenge the machine shows. The host tool is
loaded from its file and pointed at a temporary folder; the keys are made here
and openssl checks the signatures, as on the machine.
"""

import importlib.machinery
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "deploy" / "iso" / "host"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    # No bytecode beside the tool: the ISO build copies that folder as it is.
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


tool = _load("rezztor_support_tool", HOST / "rezztor-support")
builder = _load("sign_release_for_support", ROOT / "scripts" / "sign_release.py")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with a support key on it. Returns the private half, which only Rezztor would hold."""
    private, public = builder.generate_keypair(None)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "support-test.pub").write_text(public)
    (tmp_path / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    monkeypatch.setattr(tool, "KEYS", str(tmp_path / "keys"))
    monkeypatch.setattr(tool, "MACHINE_ID", str(tmp_path / "machine-id"))
    monkeypatch.setattr(tool, "STATE", str(tmp_path / "run" / "support.json"))
    monkeypatch.setattr(tool, "LOG", str(tmp_path / "setup.log"))
    return private.encode()


def test_a_console_is_closed_exactly_when_a_support_key_is_on_the_machine(machine, tmp_path, monkeypatch):
    assert tool.closed()
    monkeypatch.setattr(tool, "KEYS", str(tmp_path / "no-such-folder"))
    assert not tool.closed()


def test_a_code_rezztor_signed_for_the_challenge_opens_once(machine, tmp_path):
    challenge = tool.challenge()
    assert challenge.startswith("RZ-") and len(challenge) == 20 and tool.challenge() == challenge
    code = builder.support_code(challenge, machine, None, hours=8)
    # As a person types it: lower case, dashes for the spaces.
    opens, said = tool.verify(code.lower().replace(" ", "-"))
    assert opens and "support-test" in said
    assert "opened" in (tmp_path / "setup.log").read_text()
    again, said = tool.verify(code)
    assert not again and "No challenge is waiting" in said
    assert tool.challenge() != challenge


def test_what_does_not_open_it(machine, tmp_path):
    challenge = tool.challenge()
    now = time.time()
    other_private, _ = builder.generate_keypair(None)
    refused = {
        "another key": builder.support_code(challenge, other_private.encode(), None, hours=8),
        "another machine's challenge": builder.support_code("RZ-FFFFFFFF-AAAAAAAA", machine, None, hours=8),
        "past its time": builder.support_code(challenge, machine, None, hours=1, now=now - 7200),
        "longer than any code may run": builder.support_code(challenge, machine, None, hours=24 * 30),
    }
    for why, code in refused.items():
        opens, said = tool.verify(code, now=now)
        assert not opens, why
    assert tool.verify("not a code at all")[0] is False
    # That was the fifth wrong code: the challenge is used up, and the right code for it opens nothing.
    assert tool.verify(builder.support_code(challenge, machine, None, hours=8))[0] is False
    assert tool.challenge() != challenge
    assert "refused" in (tmp_path / "setup.log").read_text()


def test_the_installer_closes_the_console_only_for_an_iso_with_a_support_key():
    install = (ROOT / "deploy" / "iso" / "install-target.sh").read_text()
    closing = install[install.index("if ls keys/support/*.pub") :]
    closing = closing[: closing.index("\nfi\n")]
    for step in (
        "usermod -s /usr/local/sbin/rezztor-console rezadmin",
        "gpasswd -d rezadmin sudo",
        'rezadmin ALL=(root) NOPASSWD: /usr/local/sbin/rezztor-setup ""',
        "AllowTcpForwarding no",
        "password_pbkdf2 rezztor",
        "--unrestricted",
        'GRUB_DISABLE_RECOVERY="true"',
        "update-grub",
    ):
        assert step in closing, f"a closed console no longer has: {step}"
    assert "usermod -s" not in install.replace(closing, ""), "the account loses its shell outside the closed case"
    # The shell the account gets runs the menu whatever it was started with.
    console = (HOST / "rezztor-console").read_text()
    commands = [line for line in console.splitlines() if line and not line.startswith("#")]
    assert commands == ["exec sudo -n /usr/local/sbin/rezztor-setup"]
    build = (ROOT / "scripts" / "build_iso.sh").read_text()
    assert 'if [[ "$KIND" == LAB ]]; then rm -rf "$STAGE/keys/support"; fi' in build, "a lab machine's console stays open"
    assert "^[A-Z0-9]{4,16}$" in build, "a build is marked with a code, never a name"


def test_the_menu_offers_support_access_on_a_closed_console_and_says_so_on_the_status_screen():
    setup = (HOST / "rezztor-setup").read_text()
    assert 'if console_closed; then more=(support "Support access' in setup
    assert "support) support_access; continue ;;" in setup
    assert 'if console_closed; then echo "Console      closed' in setup
    access = setup[setup.index("support_access() {") : setup.index("\nmenu() {")]
    assert "$SUPPORT challenge" in access and "$SUPPORT verify" in access
    assert access.index("$SUPPORT verify") < access.index("bash --login"), "the shell opens after the code is checked"
    assert access.count("say ") == 2, "opening and closing the shell are both written to the log"


# --- a lost console password ----------------------------------------------------


def test_a_recovery_code_recovers_and_opens_no_shell_and_a_support_code_does_not_recover(machine):
    support, recovery = tool.challenge(), tool.challenge("recover")
    assert support != recovery and tool.challenge("recover") == recovery  # each purpose its own challenge
    # A code made for the one does not work for the other, whichever challenge it names.
    assert not tool.verify(builder.support_code(recovery, machine, None, hours=8), purpose="recover")[0]
    assert not tool.verify(builder.support_code(support, machine, None, hours=8, purpose="recover"))[0]
    assert tool.verify(builder.support_code(recovery, machine, None, hours=8, purpose="recover"), purpose="recover")[0]
    assert tool.verify(builder.support_code(support, machine, None, hours=8))[0]


def _chpasswd(monkeypatch):
    """chpasswd recorded instead of run; openssl, which checks the code, runs as on the machine."""
    calls, real = [], tool.subprocess.run

    def run(argv, **kw):
        if argv[0] != "chpasswd":
            return real(argv, **kw)
        calls.append((argv, kw.get("input")))
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tool.subprocess, "run", run)
    return calls


class Console:
    def __init__(self, code, passwords):
        self.code, self.passwords, self.said = code, list(passwords), []

    def read(self, prompt):
        self.said.append(prompt)
        return self.code(self) if callable(self.code) else self.code

    def secret(self, prompt):
        self.said.append(prompt)
        return self.passwords.pop(0)

    def say(self, text=""):
        self.said.append(text)


def test_recovery_sets_a_new_console_password_and_nothing_else(machine, tmp_path, monkeypatch):
    calls = _chpasswd(monkeypatch)
    good = "a-new-console-password-1"
    console = Console(lambda c: builder.support_code(c.said[3].split()[-1], machine, None, hours=8, purpose="recover"),
                      ["short", "short", "has-rezadmin-in-it-x", "has-rezadmin-in-it-x", good, "not the same", good, good])  # fmt: skip
    assert tool.recover(console.read, console.secret, console.say) == 0
    said = "\n".join(console.said)
    assert "Challenge    RZ-" in said and "at least 15 characters" in said and "must not contain the account name" in said
    assert "The two entries are not the same." in said and "The console password is set." in said
    assert calls == [(["chpasswd"], f"rezadmin:{good}\n")]  # the one thing it changes
    log = (tmp_path / "setup.log").read_text()
    assert "console password recovery opened" in log and "was set through console password recovery" in log
    assert good not in log and good not in said


def test_recovery_without_the_right_code_changes_nothing(machine, monkeypatch):
    calls = _chpasswd(monkeypatch)
    support_code = Console(lambda c: builder.support_code(c.said[3].split()[-1], machine, None, hours=8), [])
    assert tool.recover(support_code.read, support_code.secret, support_code.say) == 1
    assert "not made for this challenge" in "\n".join(support_code.said)
    assert tool.recover(*(lambda c: (c.read, c.secret, c.say))(Console("", []))) == 1  # Enter alone ends it
    assert calls == []


def test_recovery_is_a_local_console_login_with_one_sudo_rule():
    install = (ROOT / "deploy" / "iso" / "install-target.sh").read_text()
    closing = install[install.index("if ls keys/support/*.pub") :]
    closing = closing[: closing.index("\nfi\n")]
    for step in (
        "--shell /usr/local/sbin/rezztor-recover",
        "-:rezrecover:ALL EXCEPT LOCAL",
        "pam_access",
        "rezrecover ALL=(root) NOPASSWD: /usr/local/sbin/rezztor-support recover",
        "DenyUsers rezrecover",
    ):
        assert step in closing, f"console password recovery no longer has: {step}"
    assert "rezrecover" not in install.replace(closing, ""), "the account exists only on a closed console"
    assert "AllowUsers rezadmin" in install
    shell = (HOST / "rezztor-recover").read_text()
    assert [line for line in shell.splitlines() if line and not line.startswith("#")] == ["exec sudo -n /usr/local/sbin/rezztor-support recover"]
    assert "Lost the console password? Sign in as rezrecover" in (HOST / "rezztor-setup").read_text()

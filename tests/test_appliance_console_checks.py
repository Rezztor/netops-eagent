"""The console's own checks, from the deployment audit of 2026-10-16 (L1-L4).

The shell functions are cut out of rezztor-setup and run by bash; the USB pick
of rezztor-update runs against a made-up lsblk. Nothing here touches a disk,
a mount or the network.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "iso" / "host" / "rezztor-setup"


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SETUP.read_text(encoding="utf-8"), re.M | re.S)
    assert match, f"rezztor-setup must define {name}"
    return match.group(0)


def _bash(script: str, argv0: str = "bash", cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([argv0, "-c", script], capture_output=True, text=True, cwd=cwd, env=env, check=False)


def _under_sshd() -> bool:
    pid = os.getpid()
    while pid > 1:
        if Path(f"/proc/{pid}/comm").read_text().startswith("sshd"):
            return True
        pid = int(re.search(r"^PPid:\s+(\d+)", Path(f"/proc/{pid}/status").read_text(), re.M).group(1))
    return False


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="reads /proc")
def test_the_administrator_reset_is_refused_over_ssh(tmp_path):
    """L1: the console password typed over SSH gave a new password for any product administrator."""
    probe = _function("over_ssh") + 'bash -c "$(declare -f over_ssh); over_ssh && echo ssh || echo console"; :'  # `; :` keeps bash from exec-ing over the parent
    env = {k: v for k, v in os.environ.items() if k != "SSH_CONNECTION"}
    # A login over SSH: some ancestor is sshd (sshd-session on Debian 13), and sudo has dropped SSH_CONNECTION.
    (tmp_path / "sshd-session").symlink_to("/bin/bash")
    assert _bash(probe, str(tmp_path / "sshd-session"), env=env).stdout.strip() == "ssh"
    assert _bash(probe, env={**env, "SSH_CONNECTION": "10.0.0.5 50000 10.0.0.1 22"}).stdout.strip() == "ssh"
    if not _under_sshd():
        assert _bash(probe, env=env).stdout.strip() == "console"
    reset = _function("reset_admin")
    assert reset.index("if over_ssh; then") < reset.index("product_accounts list")


def _check(answers: dict, key: str, cwd) -> str:
    assignments = "".join(f"A[{k}]={v!r}\n" for k, v in answers.items())
    script = (
        "set -euo pipefail\ndeclare -A A\n" + assignments
        + "layouts() { printf 'us\\ngb\\nde\\n'; }\nconsole_locked() { false; }\nconsole_closed() { false; }\n"
        + _function("check") + f"check {key}\n"
    )
    done = _bash(script, cwd=cwd)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_keyboard_layout_is_a_name_and_not_a_pattern(tmp_path):
    """L2: `[u/]s` matched "us", passed, and then ended the sed in apply_keyboard."""
    assert _check({"KEYMAP": "us"}, "KEYMAP", tmp_path) == ""
    for bad in ("[u/]s", ".*", "u."):
        assert "is not known" in _check({"KEYMAP": bad}, "KEYMAP", tmp_path), bad


def test_an_ntp_answer_is_not_expanded_into_file_names(tmp_path):
    """L2: an unquoted `*` became the names in the current folder, which passed the per-word check."""
    (tmp_path / "pool.ntp.org").write_text("")
    assert "is not a host name" in _check({"NTP": "*"}, "NTP", tmp_path)
    assert _check({"NTP": "0.pool.ntp.org 10.0.0.1"}, "NTP", tmp_path) == ""
    assert "At most three" in _check({"NTP": "a b c d"}, "NTP", tmp_path)


def _load_update_tool():
    path = ROOT / "deploy" / "iso" / "host" / "rezztor-update"
    loader = importlib.machinery.SourceFileLoader("rezztor_update_for_media", str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


def _medium_pick(monkeypatch, tmp_path, disks):
    tool = _load_update_tool()
    mounted = []

    def run(*argv, **_):
        if argv[0] == "lsblk":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"blockdevices": disks}), "")
        if argv[0] == "mount":
            mounted.append(argv[-2])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tool, "_run", run)
    monkeypatch.setattr(tool.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(tool.shutil, "copyfile", lambda *a, **k: None)
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_text("x")
    return tool.Host().write_to_medium(str(bundle)), mounted


def test_the_support_bundle_goes_to_a_usb_stick_and_never_to_a_fixed_disk(monkeypatch, tmp_path):
    """L3: an unused second disk with ext4 on it was mounted read-write and written to."""
    system = {"path": "/dev/sda", "type": "disk", "rm": False, "tran": "sas", "children": [
        {"path": "/dev/sda1", "fstype": "vfat", "mountpoint": "/boot/efi", "type": "part"}]}
    spare = {"path": "/dev/sdb", "type": "disk", "rm": False, "tran": "sas", "model": "Virtual disk", "size": "20G",
             "children": [{"path": "/dev/sdb1", "fstype": "ext4", "mountpoint": None, "type": "part"}]}
    stick = {"path": "/dev/sdc", "type": "disk", "rm": "1", "tran": "usb", "model": "Cruzer Blade", "size": "14.9G",
             "children": [{"path": "/dev/sdc1", "fstype": "vfat", "mountpoint": None, "type": "part"}]}
    assert _medium_pick(monkeypatch, tmp_path, [system, spare]) == (None, [])
    device, mounted = _medium_pick(monkeypatch, tmp_path, [system, spare, stick])
    assert mounted == ["/dev/sdc1"]
    assert device == "/dev/sdc1 (Cruzer Blade 14.9G)", "the screen names the stick that got the file"


def test_the_docker_installer_is_downloaded_to_a_name_of_its_own():
    """L4: a fixed /tmp/get-docker.sh could be made first by another account and rewritten before `sudo sh`."""
    installer = (ROOT / "deploy" / "customer" / "installer.sh").read_text(encoding="utf-8")
    assert "/tmp/get-docker.sh" not in installer
    assert 'get_docker=$(mktemp)' in installer and '$sudo_cmd sh "$get_docker"' in installer

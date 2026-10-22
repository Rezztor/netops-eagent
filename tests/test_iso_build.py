"""The TEST ISO's files, checked without building or booting anything.

`deploy/iso/test/test-iso.sh` is the real proof (it installs the ISO in
QEMU), but it needs KVM and half an hour. These are the rules that must not
break quietly in between: the installer never erases a disk without asking,
nothing secret is on the ISO, and the ISO stays a carrier for the one bundle.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISO = ROOT / "deploy" / "iso"
CUSTOMER = ROOT / "deploy" / "customer"
SETUP = ISO / "host" / "rezztor-setup"
NETWORK = ISO / "host" / "rezztor-network"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def answered(text: str) -> set[str]:
    """The debconf questions a preseed file answers."""
    return {
        line.split()[1]
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and len(line.split()) >= 2
    }


DISK_QUESTIONS = ("partman-auto/disk", "partman/confirm", "partman/confirm_nooverwrite")


def test_the_installer_always_asks_before_it_erases_a_disk():
    """The disk and the erase confirmation are the one thing a person answers.
    Only the QEMU test may answer them, by typing into the boot loader."""
    keys = answered(read(ISO / "preseed.cfg"))
    menus = read(ISO / "grub.cfg") + read(ISO / "isolinux.cfg")
    for question in DISK_QUESTIONS:
        assert question not in keys, f"preseed.cfg answers {question}"
        assert question not in menus, f"a boot menu entry answers {question}"
    assert "partman-auto/method" in keys, "everything except the disk is answered"


def test_the_installer_uses_no_network_and_sets_no_password():
    text = read(ISO / "preseed.cfg")
    assert "d-i netcfg/enable boolean false" in text
    assert "d-i apt-setup/use_mirror boolean false" in text
    assert "d-i passwd/root-login boolean false" in text
    # "!" is a locked account: nobody can sign in until rezztor-setup sets a password.
    assert "d-i passwd/user-password-crypted password !" in text
    assert not re.search(r"^d-i passwd/\S*password(-again)? password (?!!$)", text, re.M)


def test_every_screen_says_test_build():
    for name in ("grub.cfg", "isolinux.cfg", "host/rezztor-setup", "install-target.sh"):
        assert "TEST BUILD, not signed" in read(ISO / name), name


def test_nothing_secret_is_in_the_iso_files():
    """No key, token, password or activation key travels on the ISO."""
    for path in ISO.rglob("*"):
        if not path.is_file() or path.name == "os-packages.sha256":
            continue
        text = read(path)
        assert "PRIVATE KEY" not in text, path
        assert not re.search(r"REZ-[A-Z0-9]{5}-[A-Z0-9]{5}", text), path
        assert not re.search(r"(?i)^\s*\w*(password|secret|token)\w*=['\"]?[A-Za-z0-9+/]{8,}", text, re.M), path


def test_the_iso_runs_the_bundle_and_no_second_installer():
    """One install path: the bundle's install.sh, unattended, run once."""
    build = read(ROOT / "scripts" / "build_iso.sh")
    setup = read(SETUP)
    assert "scripts/build_offline_bundle.sh" in build
    assert "sh ./install.sh --unattended" in setup
    assert "REZZTOR_BIND_ADDRESS=127.0.0.1" in setup
    # install.sh replaces .env when it runs again unattended, which locks the
    # product out of its own database. It must run only when there is none.
    assert re.search(r"if \[\[ ! -f \$STACK/\.env \]\]; then.*?install\.sh --unattended", setup, re.S)
    assert "docker compose" not in read(ISO / "install-target.sh")
    assert not list(ISO.rglob("docker-compose*.yml")), "the ISO has no Compose file of its own"


def test_the_appliance_starts_both_executors_and_nobody_types_a_token():
    """Since 2026-10-08, after the first install from the ISO: the customer enters devices and
    a licence, and no token. The profiles go through the bundle's installer like every other need of the ISO."""
    setup, installer = read(SETUP), read(CUSTOMER / "installer.sh")
    assert re.search(r"COMPOSE_PROFILES=ansible,cloud \\\n.*?sh \./install\.sh --unattended", setup, re.S)
    assert "COMPOSE_PROFILES=%s" in installer and "REZZTOR_ANSIBLE_ADMIN_TOKEN=$(openssl rand -hex 32)" in installer
    guide = read(ROOT / "docs" / "guides" / "install-from-iso-vmware.md")
    assert "run from the first start" in guide and "Azure changes stay off until" in guide
    assert "ansible_config" in read(ISO / "test" / "test-iso.sh") and "azure_change_config" in read(ISO / "test" / "test-iso.sh")


def test_the_bind_address_keeps_a_hand_install_as_it_was():
    compose = read(CUSTOMER / "docker-compose.yml")
    assert '"${REZZTOR_BIND_ADDRESS:+${REZZTOR_BIND_ADDRESS}:}${REZZTOR_PORT:-50080}:8080"' in compose
    installer = read(CUSTOMER / "installer.sh")
    assert 'if [[ -n "${REZZTOR_BIND_ADDRESS:-}" ]]; then' in installer


def test_the_setup_tool_agrees_with_the_compose_file_and_the_guard():
    setup = read(SETUP)
    prefix = re.search(r'^DEFAULT_PREFIX = "(\S+)"$', read(NETWORK), re.M).group(1)
    assert f'BLOCK = "{prefix}.248.0/21"' in read(CUSTOMER / "rezztor-guard")
    assert prefix == "172.31" and "${REZZTOR_NET_PREFIX:-172.31}.250.0/24" in read(CUSTOMER / "docker-compose.yml")
    assert 'REZZTOR_NET_PREFIX="${prefix:-172.31}"' in setup, "a block the network tool moved must reach the installer"
    assert "proxy_pass http://127.0.0.1:50080;" in read(ISO / "host" / "nginx-rezztor.conf")
    assert "REZZTOR_PORT=50080" in setup


def test_the_appliance_has_one_firewall_the_bundles_guard_in_its_full_form():
    """443, 80, ping and optional SSH in; that is the guard's input chain now
    (tests/test_network_guard.py proves it). The ISO keeps no firewall script of
    its own, and nothing on it may flush the rule set."""
    assert not (ISO / "host" / "rezztor-firewall.sh").exists()
    assert not [path for path in ISO.rglob("*") if path.is_file() and "iptables -" in read(path)]
    target = read(ISO / "install-target.sh")
    assert """printf '{"form": "full"}\\n' > /etc/rezztor/guard.json""" in target
    assert "install -D -m 0755 bundle/rezztor-guard /opt/rezztor/rezztor-guard" in target
    assert "systemctl mask nftables.service" in target, "its stop action is `nft flush ruleset`: Docker's rules too"
    # The executor (uid 1000) reads the network list; the rest of /etc/rezztor stays root only.
    assert "chmod 0700 /etc/rezztor\n" in target and "chmod 0755 /etc/rezztor/networks" in target
    early = read(ISO / "host" / "rezztor-firewall.service")
    assert "ExecStart=/opt/rezztor/rezztor-guard apply" in early and "Before=network-pre.target" in early
    assert "ExecStop" not in early
    assert "systemctl restart rezztor-firewall.service" in read(SETUP), "SSH on or off must reload the guard"
    assert '/etc/rezztor/ssh-on' in read(CUSTOMER / "rezztor-guard")
    packages = read(ISO / "packages.txt").split()
    assert "nftables" in packages and "python3" in packages, "the guard needs both on the host"


def test_the_sysctls_that_go_with_the_guard_are_installed():
    text = read(ISO / "host" / "90-rezztor-network.conf")
    settings = dict(line.replace(" ", "").split("=") for line in text.splitlines() if line and not line.startswith("#"))
    assert settings == {
        "net.ipv4.conf.all.rp_filter": "1", "net.ipv4.conf.default.rp_filter": "1",
        "net.ipv4.conf.all.arp_ignore": "1", "net.ipv4.conf.all.arp_announce": "2",
        "net.ipv6.conf.all.forwarding": "0",
    }
    assert "install -m 0644 host/90-rezztor-network.conf /etc/sysctl.d/" in read(ISO / "install-target.sh")
    assert "disable_ipv6" in read(CUSTOMER / "rezztor-guard"), "IPv6 off on each device network is the guard's job"


def test_ssh_is_off_and_the_guard_is_on_after_the_install():
    text = read(ISO / "install-target.sh")
    assert "systemctl mask ssh.service ssh.socket" in text
    enabled = re.search(r"systemctl enable ([^\n]*\\\n)*[^\n]*", text).group(0)
    assert "rezztor-guard" in enabled and "rezztor-firewall" in enabled and "rezztor-guard-runner" in enabled
    assert "bundle/rezztor-guard-runner.service" in text
    assert "ConditionPathExists=/opt/rezztor/rezztor-guard" in read(CUSTOMER / "rezztor-guard.service")


def test_packages_come_from_the_iso_only():
    text = read(ISO / "install-target.sh")
    assert "sed -i '/^deb cdrom:/d' /etc/apt/sources.list" in text
    assert "file:$P/os" in text
    assert not re.search(r"https?://", text), "the install fetches nothing"
    assert not re.search(r"https?://", read(SETUP).replace("http://127.0.0.1:50080", "")
                         .replace("https://${ip:-<no address yet>}/", "").replace('https://\\\\4{$(netdev)}/', ""))


def check(key: str, answers: dict[str, str]) -> str:
    """Run the setup tool's own validator for one answer."""
    assigns = "".join(f"A[{k}]={subprocess.list2cmdline([v]) if v else chr(39) * 2}\n" for k, v in answers.items())
    body = read(SETUP).rsplit("if (( EUID != 0 ))", 1)[0]
    script = f"{body}\nSTACK=/nonexistent\nlayouts() {{ printf 'us\\ngb\\nde\\n'; }}\nconsole_locked() {{ true; }}\n{assigns}check {key}\n"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.strip()


def test_the_validator_refuses_what_the_plan_says_it_refuses():
    assert check("HOSTNAME", {"HOSTNAME": "eagent.example.com"}) == ""
    assert check("HOSTNAME", {"HOSTNAME": "12345"})
    assert check("HOSTNAME", {"HOSTNAME": "Bad_Name"})
    assert check("KEYMAP", {"KEYMAP": "de"}) == ""
    assert check("KEYMAP", {"KEYMAP": "xx; reboot"})
    assert check("CONSOLE_PASSWORD", {"CONSOLE_PASSWORD": ""}), "no default password"
    assert check("CONSOLE_PASSWORD", {"CONSOLE_PASSWORD": "short"})
    assert check("CONSOLE_PASSWORD", {"CONSOLE_PASSWORD": "a-long-console-passphrase"}) == ""
    assert check("ADMIN_PASSWORD", {"ADMIN_PASSWORD": "fourteen-chars"})
    assert check("ADMIN_PASSWORD", {"ADMIN_PASSWORD": "has a space in it 123"}), ".env cannot carry it"
    assert check("ADMIN_PASSWORD", {"ADMIN_PASSWORD": "Long-enough_and.plain1"}) == ""
    assert check("SSH", {"SSH": "maybe"})


def network_errors(text: str) -> str:
    """The network tool's validator, on an answers file with one network (the keys the first ISO had)."""
    done = subprocess.run(["python3", "-B", "-c", (
        "import importlib.machinery, importlib.util, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('n', {str(NETWORK)!r})\n"
        "n = importlib.util.module_from_spec(importlib.util.spec_from_loader('n', loader)); loader.exec_module(n)\n"
        "print('\\n'.join(n.check(n.read_answers(sys.stdin.read())).errors))\n")],
        input=text, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_the_network_answers_of_the_first_iso_are_still_checked_by_the_one_validator():
    """They moved from the setup tool's `check` to the network tool; tests/test_network_tool.py has the rest."""
    static = "NET_IFACE=ens192\nNET_MODE=static\nNET_ADDRESS=192.168.10.20/24\n"
    assert network_errors(static + "NET_GATEWAY=192.168.10.1\nNET_DNS=1.1.1.1 8.8.8.8\nNET_VLAN=4094\n") == ""
    assert network_errors("NET_IFACE=ens192\nNET_MODE=dhcp\n") == ""
    assert network_errors(static.replace("/24", ""))
    assert "which the containers on this machine use" in network_errors(static.replace("192.168.10.20", "172.31.250.9"))
    assert network_errors(static.replace("192.168.10.20", "172.31.247.9")) == "", "just below the block"
    assert network_errors(static + "NET_GATEWAY=192.168.11.1\n"), "gateway outside the subnet"
    assert network_errors(static + "NET_DNS=1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4\n")
    assert network_errors(static + "NET_VLAN=4095\n")
    setup = read(SETUP)
    assert '$NETWORK check "$ANSWERS_FILE"' in setup and '$NETWORK apply "$ANSWERS_FILE" --yes' in setup
    assert "NET_ADDRESS)" not in setup, "two validators for one answer drift apart"
    assert "install -m 0755 host/rezztor-setup host/rezztor-network host/rezztor-support host/rezztor-console host/rezztor-recover /usr/local/sbin/" in read(ISO / "install-target.sh")
    packages = read(ISO / "packages.txt").split()
    assert "netplan.io" in packages and "iputils-arping" in packages, "the network tool needs both on the host"


def test_the_answers_disk_is_never_copied():
    """It holds the two passwords. The network tool reads it where it is mounted."""
    setup = read(SETUP)
    assert "ANSWERS_FILE=/run/rezztor-answers/answers.env" in setup
    assert not re.search(r"(cp|install)[^\n]*rezztor-answers", setup)
    assert setup.index("try_apply\n        umount /run/rezztor-answers") > 0


def test_an_answers_file_is_read_and_never_run(tmp_path):
    marker = tmp_path / "ran"
    answers = tmp_path / "answers.env"
    answers.write_text(f"HOSTNAME=$(touch {marker})\nKEYMAP=us\ntouch {marker}\n", encoding="utf-8")
    body = read(SETUP).rsplit("if (( EUID != 0 ))", 1)[0]
    out = subprocess.run(
        ["bash", "-c", f'{body}\nload_answers "{answers}"\nprintf "%s|%s" "${{A[HOSTNAME]}}" "${{A[KEYMAP]}}"'],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out == f"$(touch {marker})|us"
    assert not marker.exists()


def test_the_status_screen_says_the_build_in_one_line_that_fits_the_console(tmp_path):
    """The whole health answer was printed and wrapped over three lines of an 80 column console."""
    health = ('{"gitinfo": {"branch": "", "commit_hash": "539290ea3aa244a9e172444065801eff3b019b11", "commit_time": '
              '"2026-10-06T08:46:19Z", "tag": "local", "short_tag": "local", "version": "2026.10.06"}, "error": null}')
    body = read(SETUP).rsplit("if (( EUID != 0 ))", 1)[0]

    def status(answer: str) -> str:
        script = (f"{body}\nETC={tmp_path}\nSAVED={tmp_path}/none\nNETWORK=true\ndocker() {{ :; }}\n"
                  'product_health() { printf "%s" "$HEALTH"; }\nstatus\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True, env={**os.environ, "HEALTH": answer}).stdout

    assert "Product      answering, version 2026.10.06, commit 539290e\n" in status(health)
    assert all(len(line) < 80 for line in status(health).splitlines())
    # An answer that is not what the product sends still says "answering", and nothing it holds reaches the console as it is.
    assert "Product      answering\n" in status("<html>busy</html>")
    assert "Product      answering\n" in status('{"gitinfo": null, "error": "no build information"}')
    hostile = status('{"gitinfo": {"version": "1.0\\u001b[2J $(reboot)", "commit_hash": "' + "f" * 400 + '"}}')
    assert "Product      answering, version 1.02Jreboot, commit fffffff\n" in hostile


def test_the_tls_front_does_not_log_what_the_product_keeps_out_of_its_own_log():
    """The product strips query strings and the `/mcp/t-<token>/` segment from
    its access log (helpers/server_startup.py), because a password-reset link
    carries its token in the query and the MCP and A2A endpoints carry theirs in
    the path. nginx sits in front of it on the appliance and had no logging rule
    of its own, which leaves the distribution's default in force: every request
    line and every Referer, whole, in /var/log/nginx/access.log on the host."""
    conf = read(ISO / "host" / "nginx-rezztor.conf")
    tls = conf[conf.index("listen 443 ssl default_server;"):]
    assert "access_log off;" in tls[: tls.index("location /")], (
        "the server that proxies to the product writes request lines to the host's log"
    )
    # A request the product does not answer while it starts is written to the
    # error log at level "error", with its request line (seen on an installed
    # machine, 2026-10-11). "crit" keeps nginx's own failures and drops those.
    assert "error_log /var/log/nginx/error.log crit;" in tls[: tls.index("location /")]


def test_the_tls_front_names_the_browser_and_keeps_no_upload_on_disk():
    """Seen on an installed machine, 2026-10-11: every browser reached the product as 172.31.249.1, so ten bad
    passwords from anybody locked the sign-in form for everybody; and 600 MB posted to /login without signing in
    was written to the machine's disk whole before the product answered 413."""
    conf = read(ISO / "host" / "nginx-rezztor.conf")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert "$proxy_add_x_forwarded_for" not in conf, "that keeps what the browser sent, which the product would believe"
    assert "proxy_request_buffering off;" in conf
    # An upload over the product's largest limit is told so by nginx (413); passed on unbuffered, the product's
    # own 413 is lost when it closes on a body still arriving, and the browser was told "did not answer".
    from helpers.ui_server import UPLOAD_LIMIT_BYTES
    assert UPLOAD_LIMIT_BYTES == 5 * 1024 ** 3 and "client_max_body_size 5g;" in conf
    # The product believes the header from one address, and the appliance names it at install and on every change.
    assert 'FORWARDED_ALLOW_IPS: "${FORWARDED_ALLOW_IPS-}"' in read(ROOT / "deploy" / "customer" / "docker-compose.yml")
    assert 'FORWARDED_ALLOW_IPS="${prefix:-172.31}.249.1"' in read(SETUP)
    assert "FORWARDED_ALLOW_IPS=%s" in read(ROOT / "deploy" / "customer" / "installer.sh")


def _script(name: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          env={**os.environ, **env})


def test_a_release_is_built_from_a_tag_and_never_from_a_working_folder(tmp_path):
    """ADR 0084. Until 2026-10-13 every ISO was built from a working folder, beside
    untracked files, lab settings and local secrets, and could not be built again. What a customer receives
    comes from a fresh clone of a tag; both scripts refuse the rest before they write or build anything."""
    work = tmp_path / "work"
    here = _script("build_iso.sh", REZZTOR_ISO_RELEASE="1", REZZTOR_ISO_VERSION="0.0.0.none", REZZTOR_ISO_WORK=str(work))
    assert here.returncode == 1 and "clean checkout of the tag v0.0.0.none" in here.stderr, here.stderr
    lab = _script("build_iso.sh", REZZTOR_ISO_RELEASE="1", REZZTOR_ISO_LAB="1", REZZTOR_ISO_WORK=str(work))
    assert lab.returncode == 1 and "a lab ISO is no longer built" in lab.stderr, lab.stderr
    assert not work.exists(), "a refused build wrote something"
    for not_a_tag in ("", "v", "main", "2026.10.13", "v2026.10.13;id", "v1.2.3", "v2026.10.13-lab"):
        assert _script("build_release.sh", not_a_tag, REZZTOR_ISO_WORK=str(work)).returncode == 1, not_a_tag
    assert not work.exists()

    release = read(ROOT / "scripts" / "build_release.sh")
    assert 'clone --quiet --depth 1 --branch "$TAG" "$REMOTE" "$SRC"' in release
    assert "unset REZZTOR_ISO_LAB REZZTOR_ISO_STANDIN" in release
    assert "BUILD_LICENCE=enterprise" in release
    iso = read(ROOT / "scripts" / "build_iso.sh")
    # The image must be the one built from the tag, and an open console is asked for, never fallen into.
    assert "was not built from the tag" in iso
    assert "REZZTOR_ISO_OPEN_CONSOLE=1 to build it open" in iso
    # And a machine that can never take an update is asked for too: 2026.10.13 was one, by nobody's choice.
    assert "REZZTOR_ISO_NO_UPDATES=1 to build it so" in iso and "REZZTOR_ISO_NO_UPDATES=1 to build it so" in release


def test_a_core_brings_its_bridge_and_a_package_names_the_one_before_it(tmp_path):
    """The release model of 2026-10-20: a new core is its ISO and, from the same tag, the bridge package from the
    last package of the old core; a package names the package before it or says it is the first of its core.
    Every refusal comes before the clone, and nothing is written."""
    work, key, recipients = tmp_path / "work", tmp_path / "release.pem", tmp_path / "recipients.txt"
    key.write_text("k")
    recipients.write_text("age1x\n")
    signing = ("--key", str(key), "--recipients", str(recipients))
    for refused in (("core", "v2027.01.15", *signing),                                   # no bridge
                    ("core", "v2027.01.15", "--bridge-from", "latest", *signing),
                    ("core", "v2027.01.15.2", "--bridge-from", "2027.01.08", *signing),  # a core is a date
                    ("core", "v2027.01.15-uat", "--bridge-from", "2027.01.08-uat", *signing),
                    ("core", "v2027.01.15", "--bridge-from", "2027.01.08"),             # not signed
                    ("package", "v2026.10.21", "--base", "2026.10.19", *signing),      # neither --min-from nor --first
                    ("package", "v2026.10.21", "--base", "2026.10.19", "--first", "--min-from", "2026.10.20", *signing),
                    ("package", "v2026.10.21", "--min-from", "2026.10.20", *signing),  # no core
                    ("package", "v2026.10.21", "--base", "2026.10.19", "--min-from", "2026.10.20", "--key", str(tmp_path / "none"),
                     "--recipients", str(recipients)),
                    ("v2026.10.13", "--bridge-from", "2026.10.12")):  # fmt: skip
        done = _script("build_release.sh", *refused, REZZTOR_ISO_WORK=str(work))
        assert done.returncode == 1 and "usage:" in done.stderr, (refused, done.stderr)
    # Well-formed, the next refusal is the image the package is compared with, still before the clone.
    (tmp_path / "docker").write_text("#!/bin/sh\nexit 1\n")  # a machine without that image
    (tmp_path / "docker").chmod(0o755)
    for asked, image in ((("core", "v2027.01.15", "--bridge-from", "2027.01.08"), "2027.01.08"),
                         (("package", "v2026.10.21.2", "--base", "2026.10.19", "--first"), "2026.10.19")):
        done = _script("build_release.sh", *asked, *signing, REZZTOR_ISO_WORK=str(work), PATH=f"{tmp_path}:/usr/bin:/bin")
        assert done.returncode == 1 and f"rezztor/eagent:{image} is not on this machine" in done.stderr, done.stderr
    assert not work.exists()
    release = read(ROOT / "scripts" / "build_release.sh")
    assert 'SIGN=(--base "$VERSION" --version "$VERSION" --min-from "$BRIDGE_FROM")' in release
    assert 'scripts/sign_release.py package "${SIGN[@]}"' in release


def test_a_uat_build_never_becomes_a_release(tmp_path):
    """ADR 0085. A UAT image trusts a dummy licence root and a UAT ISO may carry dummy release keys: neither goes
    into a release, whatever the shell has set, and an image with the UAT trust file goes into a UAT ISO only."""
    work = tmp_path / "work"
    for env, said in (({"REZZTOR_ISO_RELEASE": "1", "REZZTOR_ISO_UAT": "1"}, "a release is not a UAT build"),
                      ({"REZZTOR_ISO_LAB": "1", "REZZTOR_ISO_UAT": "1"}, "a lab ISO is no longer built"),
                      ({"REZZTOR_ISO_RELEASE": "1", "REZZTOR_ISO_RELEASE_KEYS": str(tmp_path)}, "not REZZTOR_ISO_RELEASE_KEYS")):
        done = _script("build_iso.sh", REZZTOR_ISO_WORK=str(work), **env)
        assert done.returncode == 1 and said in done.stderr, done.stderr
    assert not work.exists(), "a refused build wrote something"
    assert _script("build_release.sh", "v2026.10.18-uat", REZZTOR_ISO_WORK=str(work)).returncode == 1
    release = read(ROOT / "scripts" / "build_release.sh")
    assert "unset REZZTOR_ISO_LAB REZZTOR_ISO_STANDIN REZZTOR_UAT_TRUST REZZTOR_ISO_UAT REZZTOR_ISO_RELEASE_KEYS" in release
    assert "BUILD_LICENCE=enterprise" in release
    iso = read(ROOT / "scripts" / "build_iso.sh")
    assert "*UAT_TRUST_FILE | *'\"licence\": \"uat\"'*) is_lab=\"half-UAT\"" in iso
    assert 'VERSION="${VERSION%-uat}-uat"' in iso
    local = read(ROOT / "scripts" / "build_local.sh")
    assert '[[ "${BUILD_LICENCE:-}" == uat ]] && app_tag=uat' in local and "rezztor/eagent:$app_tag" in local


def _setup_functions(script: str, tmp_path: Path, docker: str = "", **env: str) -> subprocess.CompletedProcess:
    """Run `script` in bash after the definitions of rezztor-setup (everything above its root check), with
    `docker` a stand-in on PATH: the shell script given, or one that prints $FAKE_LICENCE (failing when unset)."""
    text = read(SETUP)
    definitions = text[: text.index("if (( EUID != 0 ))")]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(docker or '#!/bin/sh\n[ -n "${FAKE_LICENCE+x}" ] || exit 1\necho "$FAKE_LICENCE"\n')
    (bin_dir / "docker").chmod(0o755)
    clean = {k: v for k, v in os.environ.items() if k != "FAKE_LICENCE"}
    return subprocess.run(["bash", "-c", definitions + "\n" + script], capture_output=True, text=True, timeout=30,
                          env={**clean, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env})


def test_the_status_screen_says_what_the_product_says_of_its_licence(tmp_path):
    """Until 2026-10-15 the status screen said "the product runs in Restricted mode" on every machine, a
    licensed one included. Now it asks the product (helpers/license_client.state) and says what it answers."""
    cases = {
        "licensed 1790000000": "Licence      licensed, until 2026-09-21",
        "licensed": "Licence      licensed",
        "grace1 1790000000": "Licence      grace: the product works; renew the licence under Settings -> Licence",
        "grace2": "Licence      grace: the product works; renew the licence under Settings -> Licence",
        "restricted": "Licence      Restricted: enter the activation key under Settings -> Licence",
        "": "Licence      not known: the product does not answer",
        "licensed $(id)": "Licence      licensed",  # what comes back is data, never run
    }
    for answer, line in cases.items():
        run = _setup_functions("licence_line", tmp_path, FAKE_LICENCE=answer)
        assert run.returncode == 0 and run.stdout == line + "\n", (answer, run.stdout, run.stderr)
    silent = _setup_functions("licence_line", tmp_path)  # the product's container does not answer at all
    assert silent.stdout == "Licence      not known: the product does not answer\n", silent.stderr
    assert "Restricted mode" not in read(SETUP)


def test_the_status_screen_says_whether_both_guard_units_hold(tmp_path):
    """The runner's watch is retried with a backoff after a failed load, so between tries it is
    "activating" with a failed result: the status screen must not read that as on."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "systemctl").write_text(
        '#!/bin/sh\ncase "$3:$4" in\n'
        '  ActiveState:rezztor-guard) echo active;; Result:rezztor-guard) echo success;;\n'
        '  ActiveState:rezztor-guard-runner) echo activating;; Result:rezztor-guard-runner) echo exit-code;;\n'
        'esac\n')
    (fake / "systemctl").chmod(0o755)
    run = _setup_functions('echo "host $(guard_state rezztor-guard); runner $(guard_state rezztor-guard-runner)"',
                           tmp_path, PATH=f"{fake}:{os.environ['PATH']}")
    assert run.stdout == "host on; runner NOT LOADED (activating, exit-code)\n", run.stderr
    assert "runner loopback $(guard_state rezztor-guard-runner)" in read(SETUP)


def test_the_password_step_from_the_menu_has_a_way_back_and_first_boot_has_none(tmp_path):
    """Back (or Esc) in the menu's password step leaves the password as it was. At first boot there is no
    password yet, so there is no Back either."""
    back = _setup_functions(
        'wt() { echo "$*" >> "$CALLS"; return 1; }\n'
        'A[CONSOLE_PASSWORD]=""\n'
        'rc=0; ask_console_password cancel || rc=$?\n'
        'echo "rc=$rc password=[${A[CONSOLE_PASSWORD]}]"',
        tmp_path, CALLS=str(tmp_path / "calls"))
    assert back.stdout == "rc=1 password=[]\n", back.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 1 and "--cancel-button Back --passwordbox" in calls[0] and "--nocancel" not in calls[0]

    # At first boot a Back is not offered, and a box that returns nothing is simply asked again.
    first = _setup_functions(
        # Each box runs in a subshell of its own, so what it has seen is counted in the file.
        'wt() { echo "$*" >> "$CALLS"; (( $(wc -l < "$CALLS") > 1 )) || return 1; echo "a-long-enough-password-1"; }\n'
        'check() { :; }\n'
        'ask_console_password\n'
        'echo "password=[${A[CONSOLE_PASSWORD]}]"',
        tmp_path, CALLS=str(tmp_path / "first"))
    assert first.stdout == "password=[a-long-enough-password-1]\n", first.stderr
    assert all("--nocancel --passwordbox" in call for call in (tmp_path / "first").read_text().splitlines())

    menu = read(SETUP)
    assert 'if ! ask_console_password cancel; then' in menu and '"The console password was not changed."' in menu


def test_the_menu_gives_a_product_administrator_a_new_password_and_keeps_it_nowhere(tmp_path):
    """Gap 3 of the closed console: the only administrator forgot the password and there is no mail. The menu
    asks the product (helpers/console_admin.py) and shows the new password once; the log names only who."""
    fake = ('#!/bin/sh\n'
            'printf "%s\\n" "$7" > "$CALLS.code"; head="$1 $2 $3 $4 $5 $6"; shift 7; echo "$head | $*" >> "$CALLS"\n'
            'case "$*" in\n'
            '"sh list") printf "chief\\tChief Admin\\tnever\\nsecond\\tsecond\\t2026-10-15 09:00\\n" ;;\n'
            '"sh reset chief") echo "Pw-from-the-product-1" ;;\n'
            '*) echo "not an administrator" >&2; exit 1 ;;\n'
            'esac\n')  # fmt: skip
    script = (
        f'LOG={tmp_path}/setup.log\n'
        'wt() { echo "$*" >> "$BOXES"; case "$*" in *--menu*) echo chief ;; esac; return 0; }\n'
        'reset_admin\n'
    )
    run = _setup_functions(script, tmp_path, docker=fake, CALLS=str(tmp_path / "calls"), BOXES=str(tmp_path / "boxes"))
    assert run.returncode == 0, run.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == ["exec -u eagent rezztor-eagent sh -c | sh list", "exec -u eagent rezztor-eagent sh -c | sh reset chief"], calls
    code = (tmp_path / "calls.code").read_text()
    assert "/opt/venv-eagent/bin/python -c" in code and "from helpers.console_admin import main" in code
    boxes = (tmp_path / "boxes").read_text()
    assert "chief Chief Admin, last sign-in never second second, last sign-in 2026-10-15 09:00" in boxes
    assert "--cancel-button Back --menu" in boxes and "Pw-from-the-product-1" in boxes.splitlines()[-1]
    log = (tmp_path / "setup.log").read_text()
    assert "'chief' was given a new password" in log and "Pw-from-the-product-1" not in log

    # The product does not answer: nothing is asked, and nothing is changed.
    (tmp_path / "boxes").unlink()
    down = _setup_functions('LOG=/dev/null\nwt() { echo "$*" >> "$BOXES"; return 0; }\nreset_admin\n', tmp_path,
                            BOXES=str(tmp_path / "boxes"))  # fmt: skip
    assert down.returncode == 0 and "does not answer" in (tmp_path / "boxes").read_text()

    menu = read(SETUP)
    assert 'admin "A new password for a product administrator who cannot sign in"' in menu
    assert "admin) reset_admin; continue ;;" in menu

"""The appliance's update tool (`deploy/iso/host/rezztor-update`) and the package builder
(`scripts/sign_release.py`), without an appliance.

The package in every test is made by the real builder and signed with a key made in the test. The
signature is checked by the real `openssl`, the payload is opened by the real `age` (both tests skip,
saying so, where the program is missing). Docker, Compose, the database and the product's health answer
are a fake machine that writes down what it was asked.

What none of this proves: that Compose on a real appliance brings the new version up, that the guard
and Docker stay in step while the containers are made again, or how long a restart takes. Those need
the machine; `deploy/iso/test/test-update.sh` is that test.
"""

from __future__ import annotations

from types import SimpleNamespace
import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import lzma
import os
import re
import secrets
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = "REZZTOR_VERSION=2026.10.08\nFLASK_SECRET_KEY=keep-me\nREZZTOR_PORT=50080\nREZZTOR_DB_APP_PASSWORD=also-keep\n"

needs_tools = pytest.mark.skipif(
    not (shutil.which("openssl") and shutil.which("age") and shutil.which("age-keygen")),
    reason="needs the programs openssl, age and age-keygen: the signature and the encryption are checked by the real ones",
)


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    # No bytecode beside the tool: the ISO build copies that directory as it is.
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


tool = _load("rezztor_update_tool", ROOT / "deploy" / "iso" / "host" / "rezztor-update")
builder = _load("sign_release_tool", ROOT / "scripts" / "sign_release.py")


class Machine(tool.Host):
    """A machine with real keys and real files under a temporary root, and no Docker."""

    def __init__(self):
        self.calls, self.images, self.schema_now, self.healthy = [], {"2026.10.08"}, 24, {"2026.10.08", "2026.10.12"}
        self.dump_problem = None
        # The application's files as they run, and as the new version has them.
        # version -> the application's files in that version's image. A version not named here differs in code.
        self.trees, self.copy_problem = {"2026.10.08": {"prompts/a.md": "1", "run_ui.py": "1"}}, None
        self.running = dict(self.trees["2026.10.08"])

    def sleep(self, seconds):
        pass

    def free(self):
        return 50 << 30

    def image(self, ref):
        return {"Cmd": ["run"]} if ref.rsplit(":", 1)[1] in self.images else None

    def tag(self, source, target):
        self.calls.append(("tag", source, target))
        return self.image(source) is not None

    def untag(self, ref):
        self.calls.append(("untag", ref))

    def load(self, path):
        self.calls.append(("load", path))
        self.images.add("2026.10.12")
        return True

    def build(self, base, tree, target):
        with lzma.open(tool._path(tree)) as plain, tarfile.open(fileobj=plain, mode="r|") as tar:
            names = tar.getnames()
        self.calls.append(("build", base, target, sorted(names)))
        self.images.add(target.rsplit(":", 1)[1])
        return None

    def tree(self, image=None, container=None):
        if container:
            # A running application has files of its own beside the code.
            return {**self.running, "helpers/__pycache__/db.cpython-313.pyc": "x", "tmp/upload.bin": "x"}
        version = image.rsplit(":", 1)[1]
        return dict(self.trees.get(version) or {"prompts/a.md": version, "run_ui.py": version})

    def copy_live(self, image, copy, remove, container="rezztor-eagent"):
        self.calls.append(("copy_live", image, copy, remove))
        if not self.copy_problem:
            self.running = {**self.running, **{path: self.tree(image=image)[path] for path in copy}}
            for path in remove:
                self.running.pop(path, None)
        return self.copy_problem

    def compose(self, *args, timeout=0):
        self.calls.append(("compose", *args))
        return None

    def health(self):
        version = tool.env_get(tool._read(tool.STACK + "/.env") or "", "REZZTOR_VERSION")
        return {"version": version} if version in self.healthy else None

    def schema(self):
        return self.schema_now

    def dump(self, path):
        self.calls.append(("dump", path))
        if self.dump_problem:
            return self.dump_problem
        tool._write(path, "-- the database\n")
        return None

    def restore(self, path):
        self.calls.append(("restore", path))
        return None

    def named(self, word):
        return [call for call in self.calls if call[0] == word]

    def collect(self):
        return [("status.txt", "Product      answering\n"), *getattr(self, "parts", [])]

    def write_to_medium(self, path):
        self.calls.append(("write_to_medium", path))
        return getattr(self, "medium", None)

    # The operating system (ADR 0092): dpkg's answer, apt reading the package's folder, systemd.
    dpkg = {"libc6": "2.41-12+deb13u4", "linux-image-amd64": "6.12.107-1", "curl": "8.14.1-2+deb13u5"}
    apt_problem, systemctl_problems, reboot_problem = None, {}, None

    def dpkg_versions(self):
        return dict(self.dpkg)

    def apt_upgrade(self, folder):
        files = sorted(path.name for path in Path(tool._path(folder)).iterdir())
        self.calls.append(("apt_upgrade", folder, files))
        if self.apt_problem:
            return self.apt_problem
        # As apt would: every package of the folder's index that is newer than what is installed.
        index = builder.packages_index((Path(tool._path(folder)) / "Packages").read_text())
        self.dpkg = {**self.dpkg, **{name: version for name, version in index if self.dpkg.get(name) != version}}
        return None

    def systemctl(self, *args):
        self.calls.append(("systemctl", *args))
        return self.systemctl_problems.get(args)

    def reboot(self):
        self.calls.append(("reboot",))
        return self.reboot_problem


@pytest.fixture
def machine(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setattr(tool, "ROOT", str(root))
    (root / "var/lib/rezztor/stack").mkdir(parents=True)
    (root / "var/lib/rezztor/stack/.env").write_text(ENV)
    (root / "var/lib/rezztor/stack/docker-compose.yml").write_text("services: {old: true}\n")
    (root / "opt/rezztor/keys").mkdir(parents=True)
    private, public = builder.generate_keypair()
    (root / "opt/rezztor/keys/release-test.pub").write_text(public)
    host = Machine()
    host.private, host.root, host.tmp = private.encode(), root, tmp_path
    return host


def package(machine, tmp_path, *, private=None, recipients=None, name="update.rezupd", texts=None, debian=None, **changes):
    """A real package for the machine: the application's files, and whatever `changes` says. `debian` is a list of
    (name, from, to): Debian's updates the package carries (format 4)."""
    work = tmp_path / f"work-{name}"
    work.mkdir(exist_ok=True)
    tree = work / "eagent.tar.xz"
    with lzma.open(tree, "wb") as packed, tarfile.open(fileobj=packed, mode="w|") as tar:
        for path, text in (("eagent/run_ui.py", b"print('new')\n"), ("eagent/build_info.json", b"{}\n")):
            info = tarfile.TarInfo(path)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text))
    parts = {"app/eagent.tar.xz": tree}
    manifest = {
        "format": 3, "product": "rezztor-eagent", "version": "2026.10.12", "base": "2026.10.08",
        "min_from": "2026.10.08", "created": "2026-10-12T09:30:00Z", "restart": "application",
        "database_change": False, "notes": ["A fix."], "images": {"eagent": {"mode": "files", "file": "app/eagent.tar.xz"}},
        "host": [],
    }  # fmt: skip
    manifest.update(changes)
    for known in manifest["host"]:
        (work / known.replace("/", "_")).write_text((texts or {}).get(known, "services: {new: true}\n"))
        parts[known] = work / known.replace("/", "_")
    if debian:
        index, items = [], []
        for deb, before, after in debian:
            file = f"{deb}_{after.replace(':', '%3a')}_amd64.deb"
            (work / file).write_bytes(b"!<arch>\n" + file.encode())
            parts[f"os/{file}"] = work / file
            index.append(f"Package: {deb}\nVersion: {after}\nArchitecture: amd64\nFilename: ./{file}\n")
            items.append({"name": deb, "from": before, "to": after, "file": f"os/{file}"})
        (work / "Packages").write_text("\n".join(index))
        parts["os/Packages"] = work / "Packages"
        manifest.update(format=4, os={"snapshot": "20261020T000000Z", "packages": items,
                                      "reboot": any(builder.REBOOT.fullmatch(deb) for deb, _, _ in debian)})  # fmt: skip
    out = tmp_path / name
    builder.write_package(out, manifest, parts, private or machine.private, None,
                          recipients or [machine.update_key()], work)  # fmt: skip
    return out


def state_of(machine):
    return json.loads((machine.root / "var/lib/rezztor/updates/state/state.json").read_text())


def run(machine, *argv):
    said = []

    class Io:
        def say(self, text=""):
            said.append(text)

        def ask(self, text):
            said.append(text)
            return "y"

    code = tool.main(list(argv), host=machine, io=Io())
    return code, "\n".join(said)


# --- the manifest, plain -------------------------------------------------------


def _manifest(**changes):
    m = {
        "format": 3, "product": "rezztor-eagent", "version": "2026.10.12", "base": "2026.10.08",
        "min_from": "2026.10.08", "restart": "application", "database_change": False, "notes": [],
        "images": {"eagent": {"mode": "files", "file": "app/eagent.tar.xz"}}, "host": [],
        "payload": {"size": 1, "sha256": "0" * 64},
    }  # fmt: skip
    m.update(changes)
    return json.dumps(m).encode()


def test_a_manifest_this_tool_understands_is_read():
    assert tool.read_manifest(_manifest())["version"] == "2026.10.12"


@pytest.mark.parametrize(
    "changes",
    [
        {"format": 5},
        {"format": 2},
        {"product": "something-else"},
        {"version": "2026.10"},
        {"base": "../../etc"},
        {"restart": "reboot"},
        {"database_change": "no"},
        {"notes": ["x" * 401]},
        {"payload": {"size": 1, "sha256": "short"}},
        {"images": {"postgres": {"mode": "image", "file": "images/postgres.tar.gz"}}},
        {"images": {"eagent-runner": {"mode": "files", "file": "app/runner.tar.xz"}}},
        {"images": {"eagent": {"mode": "files", "file": "../../etc/passwd"}}},
        {"host": ["host/rezztor-guard"]},  # the guard and the units are format 4
        {"host": ["units/rezztor-guard.service"]},
        {"format": 4, "host": ["units/ssh.service"]},  # a unit outside the list
        {"format": 4, "host": ["units/../../etc/shadow"]},
        {"format": 4, "host": ["host/rezztor-guard.sh"]},
        {"host": ["../../etc/shadow"]},
        {"host": ["keys/../../etc/shadow.pub"]},
        {"host": ["keys/support/support-x.pub"]},  # no update touches the support keys
        {"host": ["keys/Release.pub"]},
        {"host": ["keys/release-2026b.pem"]},
        {"restart": "none", "host": ["keys/release-2026b.pub"]},
        {"restart": "none", "host": ["stack/docker-compose.yml"]},
        {"restart": "none", "database_change": True},
        {"restart": "none", "images": {"eagent": {"mode": "image", "file": "images/eagent.tar.gz"}}},
        {"base": "2026.10.12"},  # a new base release must carry every image whole
        {"start_minutes": 0},
    ],
)
def test_a_manifest_outside_the_rules_is_refused(changes):
    with pytest.raises(tool.Refused):
        tool.read_manifest(_manifest(**changes))


def test_fits_refuses_older_same_too_old_and_another_release():
    m = tool.read_manifest(_manifest())
    tool.fits(m, {"version": "2026.10.08", "base": "2026.10.08"})
    tool.fits(m, {"version": "2026.10.10", "base": "2026.10.08"})
    for installed, code in (
        ({"version": "2026.10.12", "base": "2026.10.08"}, "older"),
        ({"version": "2026.11.01", "base": "2026.10.08"}, "older"),
        ({"version": "2026.09.30", "base": "2026.09.30"}, "too_old"),
        ({"version": "2026.10.09", "base": "2026.10.09"}, "other_release"),
        ({"version": None, "base": None}, "not_installed"),
    ):
        with pytest.raises(tool.Refused) as refused:
            tool.fits(m, installed)
        assert refused.value.code == code
    # 2026.10.2 is older than 2026.10.12, although a text compare says otherwise.
    assert tool.version_key("2026.10.08.2") > tool.version_key("2026.10.08") and tool.version_key("2026.10.08.10") > tool.version_key("2026.10.08.9")


@pytest.mark.parametrize("running", ["2026.10.08-lab", "2026.10-uat", "latest", "local", "v2026.10.08", "2026.10"])
def test_a_machine_that_does_not_run_a_release_is_refused_in_words(running):
    """`.env` is what says which version runs, and it is not always a release's:
    a lab build's ends in -lab (ADR 0082), and a hand-installed stack may say
    `latest`. Comparing versions turned each part into a number and raised on
    those, so the tool stopped with "invalid literal for int()" and the Updates
    page showed no reason at all."""
    m = tool.read_manifest(_manifest())
    with pytest.raises(tool.Refused) as refused:
        tool.fits(m, {"version": running, "base": running})
    assert refused.value.code == "not_a_release"
    assert running in refused.value.text


def _uat(**changes):
    return _manifest(**{"version": "2026.10.12-uat", "base": "2026.10.08-uat", "min_from": "2026.10.08-uat", **changes})


def test_a_release_machine_refuses_a_uat_package():
    """ADR 0085: a UAT package is signed with dummy keys that no release machine holds, and even signed with a
    real one, a release machine takes no -uat version."""
    m = tool.read_manifest(_uat())
    with pytest.raises(tool.Refused) as refused:
        tool.fits(m, {"version": "2026.10.08", "base": "2026.10.08"})
    assert refused.value.code == "other_kind"


def test_a_uat_machine_takes_a_newer_uat_package_and_refuses_the_rest():
    tool.fits(tool.read_manifest(_uat()), {"version": "2026.10.08-uat", "base": "2026.10.08-uat"})
    for manifest, installed, code in (
        (_manifest(), {"version": "2026.10.08-uat", "base": "2026.10.08-uat"}, "other_kind"),  # a release package
        (_uat(base="2026.10.08"), {"version": "2026.10.08-uat", "base": "2026.10.08-uat"}, "other_kind"),
        (_uat(min_from="2026.10.08"), {"version": "2026.10.08-uat", "base": "2026.10.08-uat"}, "other_kind"),
        (_manifest(version="2026.10.12-uat"), {"version": "2026.10.08", "base": "2026.10.08"}, "other_kind"),
        (_uat(), {"version": "2026.10.12-uat", "base": "2026.10.08-uat"}, "older"),
        (_uat(), {"version": "2026.10.09-uat", "base": "2026.10.09-uat"}, "other_release"),
        (_uat(), {"version": "2026.10.08-lab", "base": "2026.10.08-lab"}, "not_a_release"),
    ):
        with pytest.raises(tool.Refused) as refused:
            tool.fits(tool.read_manifest(manifest), installed)
        assert refused.value.code == code, (manifest, installed)


@pytest.mark.parametrize("version", ["2026.10.12-lab", "2026.10.12-uat-uat", "2026.10.12uat"])
def test_a_manifest_takes_no_suffix_but_uat(version):
    with pytest.raises(tool.Refused):
        tool.read_manifest(_manifest(version=version))


@pytest.mark.parametrize("stamp, version, packaged", [
    ({"version": "2026.10.12"}, "2026.10.12", True),
    ({"version": "2026.10.12-uat", "licence": "uat"}, "2026.10.12-uat", True),
    ({"version": "2026.10.12", "licence": "uat"}, "2026.10.12", False),       # a UAT image as a release
    ({"version": "2026.10.12-uat"}, "2026.10.12-uat", False),                 # a release image as UAT
    ({"version": "2026.10.12-lab", "licence": "open"}, "2026.10.12-lab", False),
    ({"version": "2026.10.12-uat", "licence": "open"}, "2026.10.12-uat", False),
])
def test_the_builder_packages_a_uat_image_only_as_a_uat_version_and_a_lab_image_never(stamp, version, packaged):
    if packaged:
        builder.refuse_stamp(stamp, version)
    else:
        with pytest.raises(SystemExit):
            builder.refuse_stamp(stamp, version)


def test_a_new_base_release_fits_whatever_release_runs():
    images = {name: {"mode": "image", "file": f"images/{name}.tar.gz"} for name in tool.IMAGES}
    m = tool.read_manifest(_manifest(version="2026.11.02", base="2026.11.02", images=images))
    tool.fits(m, {"version": "2026.10.12", "base": "2026.10.08"})


def test_env_set_changes_one_line_and_leaves_every_other_as_it_was():
    new = tool.env_set(ENV, "REZZTOR_VERSION", "2026.10.12")
    assert new.splitlines()[1:] == ENV.splitlines()[1:] and new.splitlines()[0] == "REZZTOR_VERSION=2026.10.12"
    assert tool.env_set("A=1\n", "B", "2") == "A=1\nB=2\n"


# --- the builder's decisions ---------------------------------------------------


def test_classify_sends_files_for_code_and_a_whole_image_where_its_inputs_changed():
    code = builder.classify({"helpers/db.py", "build_info.json"}, set())
    assert code == {"images": {"eagent": "files"}, "host": [], "database_change": False, "restart": "application"}
    assert builder.classify({"requirements.txt"}, set())["images"] == {"eagent": "image"}
    assert builder.classify({"docker/runner/entrypoint.sh"}, set())["images"] == {"eagent-runner": "image", "eagent": "files"}
    both = builder.classify({"executor/store.py"}, set())["images"]
    assert both == {"eagent-ansible": "image", "eagent-cloud": "image", "eagent": "files"}
    assert builder.classify({"a.py"}, set(), ("eagent-cloud",))["images"] == {"eagent-cloud": "image", "eagent": "files"}


def test_classify_names_host_files_and_a_database_change():
    shape = builder.classify({"deploy/customer/docker-compose.yml", "deploy/iso/host/rezztor-update", "helpers/schema/0025_x.sql"},
                             {"helpers/schema/0025_x.sql"})  # fmt: skip
    assert shape["host"] == ["host/rezztor-update", "stack/docker-compose.yml"] and shape["database_change"] is True
    # A changed old migration is not a new table; a new file is.
    assert builder.classify({"helpers/schema/0001_a.sql"}, set())["database_change"] is False


def test_a_release_key_new_since_the_base_goes_to_the_host_and_nothing_else_about_keys_does():
    """ADR 0080: an update may add a key, never replace or remove one. So only a key file that is new is sent."""
    shape = builder.classify({"deploy/iso/keys/release-2026c.pub", "deploy/iso/keys/release-2026a.pub",
                              "deploy/iso/keys/support/support-2026c.pub"},
                             {"deploy/iso/keys/release-2026c.pub", "deploy/iso/keys/support/support-2026c.pub"})  # fmt: skip
    assert shape["host"] == ["keys/release-2026c.pub"] and shape["restart"] == "application"
    assert tool.read_manifest(_manifest(host=["keys/release-2026c.pub"]))["host"] == ["keys/release-2026c.pub"]


def test_only_texts_and_screens_need_no_restart():
    live = {"prompts/agent.system.md", "frontend/dist/assets/index-abc.js", "build_info.json"}
    assert builder.classify(live, set())["restart"] == "none"
    for one in ("helpers/db.py", "plugins/_ea_azure/reads.yaml", "skills/x/SKILL.md", "deploy/customer/docker-compose.yml"):
        assert builder.classify(live | {one}, set())["restart"] == "application", one
    assert builder.classify(set(), set()) == {"images": {}, "host": [], "database_change": False, "restart": "application"}


def test_the_input_lists_cover_every_copy_line_of_the_dockerfiles():
    """A path a Dockerfile copies and the list forgets would leave an image stale after an update."""
    files = {"eagent-runner": "docker/runner/Dockerfile", "eagent-ansible": "executor/Dockerfile", "eagent-cloud": "cloud_executor/Dockerfile"}
    for name, dockerfile in files.items():
        for line in (ROOT / dockerfile).read_text().splitlines():
            if not line.startswith("COPY ") or "--from=" in line:
                continue
            for source in line.split()[1:-1]:
                source = source.split("*")[0]  # executor/*.py: the folder covers it
                assert any(source.startswith(prefix) or prefix.startswith(source) for prefix in builder.INPUTS[name]), (name, source)
    assert set(builder.HOST.values()) == set(tool.HOST_FILES) and builder.IMAGES == tool.IMAGES and builder.FORMAT == tool.FORMAT
    assert builder.LIVE == tool.LIVE and builder.TREE == tool.TREE


def test_the_build_records_what_the_image_was_built_from_and_the_builder_reads_it_back(monkeypatch):
    """The image carries no Dockerfile, no docker/ and no requirements file (2026-10-12), and those are
    what says an image must travel whole. scripts/build_local.sh hashes them into a list the image does
    carry, and tree_of gives each back under its own path."""
    script = (ROOT / "scripts" / "build_local.sh").read_text()
    listed = script[script.index("git ls-files -z --") : script.index("| grep -zvE")].replace("\\\n", " ").split()[4:]
    for name, prefixes in builder.INPUTS.items():
        for prefix in prefixes:
            assert any(prefix.rstrip("/") == path or prefix.startswith(path + "/") for path in listed), (name, prefix)
    assert f"> {builder.BUILD_INPUTS}" in script and "build_inputs\n" in script

    answers = iter(["aaa  ./run_ui.py\nbbb  ./executor/ea-ansible\nccc  ./build_inputs.sha256\n",
                    "ddd  Dockerfile\neee  executor/ea-ansible\nfff  requirements.txt\n"])  # fmt: skip
    monkeypatch.setattr(builder, "_docker", lambda *argv, **kwargs: SimpleNamespace(returncode=0, stdout=next(answers), stderr=""))
    tree = builder.tree_of("rezztor/eagent:x")
    assert tree == {"run_ui.py": "aaa", "executor/ea-ansible": "bbb", "build_inputs.sha256": "ccc",
                    "Dockerfile": "ddd", "requirements.txt": "fff"}  # fmt: skip
    later = {**tree, "requirements.txt": "changed", "build_inputs.sha256": "changed"}
    assert builder.classify(builder.changed_paths(tree, later), set())["images"] == {"eagent": "image"}
    # The four files the appliance's own disk holds are in the image, where the package takes them from.
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for source in builder.HOST:
        assert f"!{source}" in ignored and ignored.index(f"!{source}") > ignored.index("deploy/"), source


def test_the_private_key_is_never_written_inside_the_repository(tmp_path):
    with pytest.raises(SystemExit):
        builder._refuse_inside_repo(ROOT / "deploy" / "iso" / "keys" / "release.pem", "the private key")
    private, public = builder.generate_keypair(b"a passphrase of some length")
    assert "ENCRYPTED" in private and "PUBLIC KEY" in public
    with pytest.raises(SystemExit):
        builder.sign(b"x", private.encode(), b"wrong")
    assert len(builder.sign(b"x", private.encode(), b"a passphrase of some length")) == 89


# --- check ---------------------------------------------------------------------


@needs_tools
def test_a_good_file_is_shown_and_nothing_else_changes(machine, tmp_path):
    code, said = run(machine, "check", str(package(machine, tmp_path)))
    state = state_of(machine)
    assert code == 0 and "Update 2026.10.12 for release 2026.10.08" in said and "Signed by Rezztor (release-test)" in said
    assert state["phase"] == "checked" and state["package"]["key"] == "release-test" and state["package"]["notes"] == ["A fix."]
    assert state["installed"] == {"version": "2026.10.08", "base": "2026.10.08", "updated_at": None}
    assert state["keys"] == ["release-test"] and state["update_key"].startswith("age1")
    assert machine.calls == [] and (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV


@needs_tools
def test_one_changed_byte_in_the_manifest_is_refused(machine, tmp_path):
    good = package(machine, tmp_path).read_bytes()
    at = good.index(b'"version": "2026.10.12"')
    bad = tmp_path / "changed.rezupd"
    bad.write_bytes(good[:at] + b'"version": "2026.10.13"' + good[at + 23 :])
    code, said = run(machine, "check", str(bad))
    assert code == 1 and "its signature does not match" in said
    state = state_of(machine)
    assert state["phase"] == "idle" and state["error"]["code"] == "bad_signature" and state["package"] is None
    assert not (machine.root / "var/lib/rezztor/updates/work/package.rezupd").exists()


@needs_tools
def test_a_file_signed_with_a_key_this_machine_does_not_know_is_refused(machine, tmp_path):
    stranger, _ = builder.generate_keypair()
    code, said = run(machine, "check", str(package(machine, tmp_path, private=stranger.encode())))
    assert code == 1 and "its signature does not match" in said


@needs_tools
def test_a_machine_with_no_release_key_accepts_nothing(machine, tmp_path):
    good = package(machine, tmp_path)
    (machine.root / "opt/rezztor/keys/release-test.pub").unlink()
    code, said = run(machine, "check", str(good))
    assert code == 1 and "its signature does not match" in said
    assert "none: this machine accepts no update" in run(machine, "status")[1]


@needs_tools
def test_an_update_adds_a_release_key_once_it_runs_and_the_new_key_then_signs_the_next(machine, tmp_path):
    keys = machine.root / "opt/rezztor/keys"
    kept = (keys / "release-test.pub").read_text()
    new_private, new_public = builder.generate_keypair()
    _, other_public = builder.generate_keypair()
    texts = {"keys/release-new.pub": new_public, "keys/release-test.pub": other_public}
    run(machine, "install", str(package(machine, tmp_path, host=["keys/release-new.pub", "keys/release-test.pub"], texts=texts)))
    assert state_of(machine)["phase"] == "staged" and not (keys / "release-new.pub").exists()  # not before it runs
    code, said = run(machine, "restart")
    assert code == 0, said
    assert (keys / "release-new.pub").read_text() == new_public
    assert (keys / "release-test.pub").read_text() == kept  # never replaced
    assert sorted(machine.key_names()) == ["release-new", "release-test"]
    machine.healthy.add("2026.10.13")
    later = package(machine, tmp_path, private=new_private.encode(), name="later.rezupd", version="2026.10.13", min_from="2026.10.08")
    code, said = run(machine, "check", str(later))
    assert code == 0, said


@needs_tools
def test_a_key_from_an_update_that_was_taken_back_is_not_added(machine, tmp_path):
    machine.healthy = {"2026.10.08"}
    _, new_public = builder.generate_keypair()
    run(machine, "install", str(package(machine, tmp_path, host=["keys/release-new.pub"], texts={"keys/release-new.pub": new_public})))
    code, _ = run(machine, "restart")
    assert code == 1 and machine.key_names() == ["release-test"]


@needs_tools
def test_what_is_not_a_package_is_refused_before_anything_is_read(machine, tmp_path):
    for name, content in (("text.rezupd", b"hello"), ("empty.rezupd", b"")):
        (tmp_path / name).write_bytes(content)
        code, said = run(machine, "check", str(tmp_path / name))
        assert code == 1 and "not a Rezztor update" in said
    wrong_order = tmp_path / "order.rezupd"
    with tarfile.open(wrong_order, "w") as tar:
        for member in ("manifest.sig", "manifest.json", "payload.age"):
            info = tarfile.TarInfo(member)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    assert "not a Rezztor update" in run(machine, "check", str(wrong_order))[1]
    link = tmp_path / "link.rezupd"
    link.symlink_to(package(machine, tmp_path))
    code, said = run(machine, "check", str(link))
    assert code == 1 and "could not be read" in said  # never through a link


@needs_tools
def test_an_older_file_is_refused_with_both_versions_named(machine, tmp_path):
    code, said = run(machine, "check", str(package(machine, tmp_path, version="2026.10.07")))
    assert code == 1 and "This update is 2026.10.07. You run 2026.10.08, which is newer. There is no way back" in said


# --- install -------------------------------------------------------------------


@needs_tools
def test_install_builds_the_new_version_beside_the_running_one(machine, tmp_path):
    code, said = run(machine, "install", str(package(machine, tmp_path)))
    state = state_of(machine)
    assert code == 0 and state["phase"] == "staged" and {step["state"] for step in state["steps"]} == {"done"}
    assert machine.named("build") == [("build", "rezztor/eagent:2026.10.08", "rezztor/eagent:2026.10.12",
                                       ["eagent/build_info.json", "eagent/run_ui.py"])]  # fmt: skip
    assert machine.named("tag") == [("tag", f"rezztor/{name}:2026.10.08", f"rezztor/{name}:2026.10.12")
                                    for name in ("eagent-runner", "eagent-ansible", "eagent-cloud")]  # fmt: skip
    # Nothing of the running stack was touched.
    assert not machine.named("compose") and (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV
    work = machine.root / "var/lib/rezztor/updates/work"
    assert not (work / "payload.tar").exists() and not (work / "payload.age").exists()


@needs_tools
def test_install_checks_the_update_path_again_and_refuses_a_skipped_update(machine, tmp_path):
    # Checked while the machine ran the update before it; by the time of the install it runs an older one again.
    env, state_file = machine.root / "var/lib/rezztor/stack/.env", machine.root / "var/lib/rezztor/updates/state/state.json"
    env.write_text(ENV.replace("REZZTOR_VERSION=2026.10.08", "REZZTOR_VERSION=2026.10.10"))
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"installed": {"version": "2026.10.10", "base": "2026.10.08", "updated_at": None}}))
    code, _ = run(machine, "check", str(package(machine, tmp_path, min_from="2026.10.10")))
    assert code == 0 and state_of(machine)["phase"] == "checked"
    env.write_text(ENV)
    state = state_of(machine)
    state["installed"] = {"version": "2026.10.08", "base": "2026.10.08", "updated_at": None}
    state_file.write_text(json.dumps(state))
    code, said = run(machine, "install")
    state = state_of(machine)
    assert code == 1 and state["error"]["code"] == "too_old" and "2026.10.10" in said
    assert not machine.named("build") and not machine.named("load")


@needs_tools
def test_a_file_encrypted_for_another_machine_cannot_be_opened_and_says_which_key_to_send(machine, tmp_path):
    other = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True).stdout
    theirs = re.search(r"age1[0-9a-z]+", other).group(0)
    code, said = run(machine, "install", str(package(machine, tmp_path, recipients=[theirs])))
    assert code == 1 and "cannot open the file" in said and machine.update_key() in said
    state = state_of(machine)
    assert state["phase"] == "checked" and state["error"]["code"] == "not_for_us" and not machine.named("build")


@needs_tools
def test_a_payload_that_is_not_the_signed_one_is_refused_before_it_is_unpacked(machine, tmp_path):
    """The manifest is public and the appliance's key is public, so anybody can wrap other content in
    a genuine manifest and signature. The checksum inside the signed manifest is what stops it."""
    genuine = package(machine, tmp_path)
    work = tmp_path / "forged"
    work.mkdir()
    with tarfile.open(genuine) as tar:
        tar.extractall(work, filter="data")
    evil = work / "payload.tar"
    with tarfile.open(evil, "w") as tar:
        info = tarfile.TarInfo("app/eagent.tar.xz")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))
    subprocess.run(["age", "-r", machine.update_key(), "-o", str(work / "payload.age"), str(evil)], check=True)
    forged = tmp_path / "forged.rezupd"
    with tarfile.open(forged, "w") as tar:
        for member in ("manifest.json", "manifest.sig", "payload.age"):
            tar.add(work / member, arcname=member)
    code, said = run(machine, "install", str(forged))
    assert code == 1 and "does not match its signed checksum" in said
    assert not machine.named("build") and not machine.named("load") and state_of(machine)["phase"] == "checked"
    assert not (machine.root / "var/lib/rezztor/updates/work/unpacked").exists()


@needs_tools
def test_install_refuses_when_the_disk_is_too_full(machine, tmp_path):
    machine.free = lambda: 1 << 20
    code, said = run(machine, "install", str(package(machine, tmp_path)))
    assert code == 1 and "are needed and 1.0 MB are free" in said and not machine.named("build")


@needs_tools
def test_a_base_image_that_is_missing_stops_the_install_and_leaves_no_new_image_name(machine, tmp_path):
    machine.images.clear()
    code, said = run(machine, "install", str(package(machine, tmp_path)))
    assert code == 1 and "could not be prepared" in said and "Nothing changed" in said
    assert state_of(machine)["phase"] == "checked" and len(machine.named("untag")) == 4


# --- texts and screens, without a restart --------------------------------------


def test_the_host_decides_itself_whether_only_texts_and_screens_differ():
    old = {"prompts/a.md": "1", "frontend/dist/assets/old.js": "1", "run_ui.py": "1", "build_info.json": "1"}
    new = {"prompts/a.md": "2", "frontend/dist/assets/new.js": "1", "run_ui.py": "1", "build_info.json": "2"}
    assert tool.live_plan(old, new) == (["build_info.json", "frontend/dist/assets/new.js", "prompts/a.md"], ["frontend/dist/assets/old.js"])
    assert tool.live_plan(old, {**new, "run_ui.py": "2"}) is None
    assert tool.live_plan(old, {**new, "helpers/new.py": "1"}) is None
    assert tool.live_plan({**old, "plugins/x/gone.py": "1"}, new) is None  # a code file that must go needs the restart
    assert tool.live_plan({}, new) is None and tool.live_plan(old, {}) is None  # a tree that could not be read


TEXTS_ONLY = {"prompts/a.md": "2", "run_ui.py": "1"}


@needs_tools
def test_an_update_of_texts_only_is_in_force_at_once_and_nothing_is_restarted(machine, tmp_path):
    machine.trees["2026.10.12"] = dict(TEXTS_ONLY)
    code, said = run(machine, "install", str(package(machine, tmp_path, restart="none")))
    state = state_of(machine)
    assert code == 0 and "Updated to 2026.10.12. No restart was needed." in said
    assert state["phase"] == "idle" and state["installed"]["version"] == "2026.10.12" and state["result"]["outcome"] == "installed"
    assert machine.named("copy_live") == [("copy_live", "rezztor/eagent:2026.10.12", ["prompts/a.md"], [])]
    assert not machine.named("compose") and not machine.named("dump") and not machine.named("untag")
    # A container made again later must start from the new image, so .env names it.
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV.replace("2026.10.08", "2026.10.12")


@needs_tools
def test_when_more_than_texts_differs_on_this_machine_the_update_waits_for_a_restart(machine, tmp_path):
    """The package says no restart; the machine looks for itself. Here a code file differs too."""
    code, said = run(machine, "install", str(package(machine, tmp_path, restart="none")))
    state = state_of(machine)
    assert state["phase"] == "staged" and state["error"]["code"] == "needs_restart" and not machine.named("copy_live")
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV and "To switch: sudo rezztor-update restart" in said
    assert run(machine, "restart")[0] == 0 and state_of(machine)["installed"]["version"] == "2026.10.12"


@needs_tools
def test_a_copy_that_fails_half_way_ends_in_a_restart_not_in_a_mixed_version(machine, tmp_path):
    machine.trees["2026.10.12"], machine.copy_problem = dict(TEXTS_ONLY), "the files could not be copied into the running application"
    run(machine, "install", str(package(machine, tmp_path, restart="none")))
    assert state_of(machine)["phase"] == "staged" and (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV


@needs_tools
def test_a_machine_that_already_has_the_code_takes_the_next_package_without_a_restart(machine, tmp_path):
    """Every package is built on the base release, so it says "restart" as soon as one earlier update
    changed code. A machine that has that update differs from the new one only in texts, and sees it."""
    machine.trees["2026.10.12"] = dict(TEXTS_ONLY)
    code, said = run(machine, "install", str(package(machine, tmp_path)))  # the package says: application
    assert code == 0 and "No restart was needed." in said and state_of(machine)["installed"]["version"] == "2026.10.12"
    assert not machine.named("compose") and state_of(machine)["error"] is None


@needs_tools
def test_a_package_that_changes_a_host_file_is_never_applied_without_the_restart(machine, tmp_path):
    machine.trees["2026.10.12"] = dict(TEXTS_ONLY)
    run(machine, "install", str(package(machine, tmp_path, host=["stack/docker-compose.yml"])))
    assert state_of(machine)["phase"] == "staged" and not machine.named("copy_live") and state_of(machine)["error"] is None


# --- restart -------------------------------------------------------------------


@needs_tools
def test_restart_changes_one_line_of_env_and_ends_on_the_new_version(machine, tmp_path):
    run(machine, "install", str(package(machine, tmp_path, host=["stack/docker-compose.yml"])))
    code, said = run(machine, "restart")
    stack = machine.root / "var/lib/rezztor/stack"
    assert code == 0 and "Updated to 2026.10.12." in said
    assert (stack / ".env").read_text() == ENV.replace("2026.10.08", "2026.10.12")
    assert (stack / "docker-compose.yml").read_text() == "services: {new: true}\n"
    state = state_of(machine)
    assert state["phase"] == "idle" and state["package"] is None and state["manifest"] is None
    assert state["installed"]["version"] == "2026.10.12" and state["installed"]["base"] == "2026.10.08"
    assert state["result"]["outcome"] == "installed" and state["history"][0]["by"] == "console"
    # The application stops, the database is dumped, and only then does anything change.
    order = [call[:3] for call in machine.calls if call[0] in ("compose", "dump")]
    assert order == [("compose", "stop", "eagent"), ("dump", "/var/lib/rezztor/updates/work/before/database.sql"), ("compose", "up", "-d")]
    assert not machine.named("restore") and not machine.named("untag")
    # The copy of .env made for going back holds every secret; it does not stay.
    assert not (machine.root / "var/lib/rezztor/updates/work/before/.env").exists()


@needs_tools
def test_a_version_that_does_not_answer_is_taken_back_and_everything_is_as_before(machine, tmp_path):
    machine.healthy = {"2026.10.08"}
    run(machine, "install", str(package(machine, tmp_path, host=["stack/docker-compose.yml"])))
    code, said = run(machine, "restart")
    stack = machine.root / "var/lib/rezztor/stack"
    assert code == 1 and "did not answer within 10 minutes" in said and "The appliance returned to 2026.10.08. Nothing was lost." in said
    assert (stack / ".env").read_text() == ENV and (stack / "docker-compose.yml").read_text() == "services: {old: true}\n"
    state = state_of(machine)
    assert state["phase"] == "idle" and state["installed"]["version"] == "2026.10.08"
    assert state["result"]["outcome"] == "rolled_back" and state["history"][0]["outcome"] == "rolled_back"
    assert ("compose", "up", "-d", "--wait", "postgres") in machine.calls and len(machine.named("untag")) == 4
    assert not machine.named("restore")  # the database did not change, so it is not put back


@needs_tools
def test_going_back_restores_the_database_when_the_new_version_changed_it(machine, tmp_path):
    """Whether the package said so or not: the number of the newest database change is read before and after."""
    machine.healthy = {"2026.10.08"}
    run(machine, "install", str(package(machine, tmp_path)))
    compose = machine.compose

    def migrate_on_start(*args, timeout=0):
        if args == ("up", "-d") and "2026.10.12" in (machine.root / "var/lib/rezztor/stack/.env").read_text():
            machine.schema_now = 25
        return compose(*args)

    machine.compose = migrate_on_start
    assert run(machine, "restart")[0] == 1
    assert machine.named("restore") == [("restore", "/var/lib/rezztor/updates/work/before/database.sql")]
    assert state_of(machine)["result"]["outcome"] == "rolled_back"
    log = (machine.root / "var/log/rezztor/update.log").read_text()
    assert "the database was at change 24 before and is at 25 now" in log and "put back from the dump" in log
    assert "keep-me" not in log and "also-keep" not in log  # nothing of .env reaches the log


@needs_tools
def test_no_backup_means_no_switch(machine, tmp_path):
    machine.dump_problem = "the disk is full"
    run(machine, "install", str(package(machine, tmp_path)))
    code, said = run(machine, "restart")
    assert code == 1 and "could not be backed up, so nothing was changed: the disk is full" in said
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV and state_of(machine)["phase"] == "staged"
    assert machine.named("compose")[-1] == ("compose", "up", "-d")  # the application is started again


@needs_tools
def test_env_is_never_written_from_nothing(machine, tmp_path):
    """.env holds every secret. A tool that cannot read it, or its own copy of it, must not write one."""
    run(machine, "install", str(package(machine, tmp_path)))
    env = machine.root / "var/lib/rezztor/stack/.env"
    env.rename(env.with_name("moved"))
    code, said = run(machine, "restart")
    assert code == 1 and "could not be read. Nothing was changed." in said and not env.exists() and not machine.named("compose")
    env.with_name("moved").rename(env)
    # The new version does not answer, and the copy made before the restart has gone missing.
    machine.healthy = {"2026.10.08"}
    dump = machine.dump

    def lose_the_copy(path):
        (machine.root / "var/lib/rezztor/updates/work/before/.env").unlink()
        return dump(path)

    machine.dump = lose_the_copy
    code, said = run(machine, "restart")
    assert code == 1 and "the copy of .env made before the restart is missing" in said
    assert env.read_text() == ENV.replace("2026.10.08", "2026.10.12")  # left as it was, never emptied


@needs_tools
def test_a_tool_that_breaks_in_the_middle_takes_the_restart_back_in_the_same_run(machine, tmp_path):
    machine.healthy = {"2026.10.08"}
    run(machine, "install", str(package(machine, tmp_path)))
    compose = machine.compose

    def break_on_start(*args, timeout=0):
        if args == ("up", "-d") and "2026.10.12" in (machine.root / "var/lib/rezztor/stack/.env").read_text():
            raise OSError("the disk went away")
        return compose(*args)

    machine.compose = break_on_start
    code, said = run(machine, "restart")
    state = state_of(machine)
    assert code == 1 and "stopped with an error" in said and state["phase"] == "idle" and state["result"]["outcome"] == "rolled_back"
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV


@needs_tools
def test_a_backup_that_breaks_does_not_leave_the_product_stopped(machine, tmp_path):
    """The application is stopped for the dump. When the dump broke -- raised,
    rather than answered that it failed -- the tool went back, found that the
    package says the database changes and that no dump exists, called that a
    problem, and skipped the one call that starts the stack again. Nothing had
    been switched and the database had not been touched: the product was simply
    left down, with the update thrown away."""
    run(machine, "install", str(package(machine, tmp_path, database_change=True)))

    def broken(path):
        raise OSError("no space left on device")

    machine.dump = broken
    code, said = run(machine, "restart")

    assert code == 1
    assert machine.named("compose")[-1] == ("compose", "up", "-d"), "the stack was left stopped"
    assert machine.named("restore") == []
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV
    assert "no backup of it" not in (state_of(machine)["result"] or {}).get("text", "")


@needs_tools
def test_a_request_that_cannot_be_taken_does_not_stay_in_the_inbox(machine, monkeypatch):
    """`serve` removed the request after it had read it, and a failure while
    reading it -- a full disk -- skipped that. The request stayed, the unit that
    watches for it started the tool again and again, and the application
    refuses a new request while the old one is there."""
    inbox = _request(machine, id="a1b2c3d4", action="discard", by="admin")

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tool.shutil, "copyfileobj", full)
    run(machine, "serve")

    assert not (inbox / "request.json").exists()


def test_a_file_is_copied_up_to_the_size_that_was_checked_and_no_further(machine, tmp_path, monkeypatch):
    """`_take` looked at the size once and then copied to the end of the file. The
    inbox is the application's to write: a file that grows while it is read, or
    one whose holes read as zeros, was copied whole into root's folder."""
    source = tmp_path / "grows.rezupd"
    source.write_bytes(b"a" * 5000)
    real = os.fstat

    def smaller(fd):
        info = real(fd)
        return os.stat_result((*info[:6], 1000, *info[7:])) if info.st_size == 5000 else info

    monkeypatch.setattr(tool.os, "fstat", smaller)
    assert tool._take(str(source), tool.WORK + "/taken") == 1000
    assert len((machine.root / "var/lib/rezztor/updates/work/taken").read_bytes()) == 1000


@needs_tools
def test_a_package_with_holes_in_it_is_refused(machine, tmp_path):
    """A sparse member is a few bytes in the file and as many zeros as it likes on
    disk: 10 KB became 256 MB in root's folder, before the signature was looked at."""
    if not shutil.which("tar"):
        pytest.skip("needs GNU tar to make a sparse member")
    good = package(machine, tmp_path)
    parts = tmp_path / "parts"
    parts.mkdir()
    with tarfile.open(good) as tar:
        tar.extractall(parts, filter="data")
    with open(parts / "payload.age", "r+b") as payload:
        payload.truncate(64 << 20)  # a hole of 64 MiB
    holed = tmp_path / "holed.rezupd"
    subprocess.run(["tar", "--sparse", "-cf", str(holed), "-C", str(parts), "manifest.json", "manifest.sig", "payload.age"], check=True)
    assert holed.stat().st_size < 1 << 20

    code, said = run(machine, "check", str(holed))

    assert code == 1 and "not a Rezztor update" in said
    work = machine.root / "var/lib/rezztor/updates/work"
    assert sum(f.stat().st_size for f in work.rglob("*") if f.is_file()) < 4 << 20


@needs_tools
def test_a_second_file_waits_until_the_first_is_restarted_or_removed(machine, tmp_path):
    first = package(machine, tmp_path)
    run(machine, "install", str(first))
    code, said = run(machine, "check", str(first))
    assert code == 1 and "2026.10.12 is installed and waits for a restart" in said and state_of(machine)["phase"] == "staged"
    assert run(machine, "discard")[0] == 0 and state_of(machine)["phase"] == "idle"
    assert len(machine.named("untag")) == 4  # the image names the install made are gone


@needs_tools
def test_the_image_of_two_updates_ago_is_removed_and_the_base_never(machine, tmp_path):
    for version in ("2026.10.12", "2026.10.14", "2026.10.16"):
        machine.healthy.add(version)
        run(machine, "install", str(package(machine, tmp_path, name=f"{version}.rezupd", version=version)))
        assert run(machine, "restart")[0] == 0
    assert {call[1].rsplit(":", 1)[1] for call in machine.named("untag")} == {"2026.10.12"}
    assert state_of(machine)["kept"] == ["2026.10.08", "2026.10.14", "2026.10.16"]


@needs_tools
def test_a_power_cut_during_the_restart_ends_on_the_old_version(machine, tmp_path):
    run(machine, "install", str(package(machine, tmp_path)))
    state_file = machine.root / "var/lib/rezztor/updates/state/state.json"
    state = json.loads(state_file.read_text())
    state["phase"] = "restarting"
    state_file.write_text(json.dumps(state))
    before = machine.root / "var/lib/rezztor/updates/work/before"
    before.mkdir(parents=True)
    (before / ".env").write_text(ENV)
    (machine.root / "var/lib/rezztor/stack/.env").write_text(ENV.replace("2026.10.08", "2026.10.12"))
    machine.healthy = {"2026.10.08"}
    run(machine, "serve")
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV
    state = state_of(machine)
    assert state["phase"] == "idle" and state["result"]["outcome"] == "rolled_back" and "was interrupted" in state["result"]["text"]


# --- the web application's request ---------------------------------------------


def _request(machine, **request):
    inbox = machine.root / "var/lib/rezztor/updates/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "request.json").write_text(json.dumps(request))
    return inbox


@needs_tools
def test_the_web_applications_requests_end_in_the_same_functions(machine, tmp_path):
    inbox = _request(machine, id="a1b2c3d4", action="check", by="admin")
    shutil.copy(package(machine, tmp_path), inbox / "package.rezupd")
    run(machine, "serve")
    state = state_of(machine)
    assert state["request"] == "a1b2c3d4" and state["phase"] == "checked" and not list(inbox.iterdir())
    _request(machine, id="a1b2c3d5", action="install", by="admin")
    run(machine, "serve")
    assert state_of(machine)["phase"] == "staged"
    _request(machine, id="a1b2c3d6", action="restart", by="admin; rm -rf /")
    run(machine, "serve")
    state = state_of(machine)
    assert state["installed"]["version"] == "2026.10.12" and state["history"][0]["by"] == "adminrm-rf"


@needs_tools
def test_a_refusal_reaches_the_page_as_a_sentence(machine, tmp_path):
    inbox = _request(machine, id="a1b2c3d4", action="check", by="admin")
    (inbox / "package.rezupd").write_bytes(b"not a package")
    run(machine, "serve")
    state = state_of(machine)
    assert state["phase"] == "idle" and state["error"] == {"code": "not_a_package", "text": "This file is not a Rezztor update. Nothing was installed."}
    assert not (inbox / "package.rezupd").exists()


def test_a_request_that_is_not_one_of_the_four_words_does_nothing(machine):
    for request in ({"id": "a1b2c3d4", "action": "shell", "by": "x"}, {"id": "../../x", "action": "restart"}, ["list"], "text"):
        inbox = _request(machine, **request) if isinstance(request, dict) else _request(machine)
        if not isinstance(request, dict):
            (inbox / "request.json").write_text(json.dumps(request))
        run(machine, "serve")
        assert state_of(machine)["request"] is None and machine.calls == [] and not (inbox / "request.json").exists()
    target = machine.tmp / "secret"
    target.write_text('{"id": "a1b2c3d4", "action": "restart"}')
    (inbox / "request.json").symlink_to(target)
    run(machine, "serve")
    assert state_of(machine)["request"] is None  # never through a link


@needs_tools
def test_the_console_finds_the_file_shows_it_and_does_all_three_steps(machine, tmp_path):
    found = package(machine, tmp_path)
    machine.media = lambda: [(str(found), found.stat().st_size)]
    machine.unmount = lambda: None
    code, said = run(machine)
    assert code == 0 and "1) update.rezupd" in said and "Install it? [y/N]" in said and "Restart the application now? [y/N]" in said
    assert "Updated to 2026.10.12." in said and state_of(machine)["installed"]["version"] == "2026.10.12"
    machine.media = lambda: []
    said = run(machine)[1]
    assert "No file ending in .rezupd was found" in said and "Updated to" not in said  # not the last update's result again


# --- the real docker, where there is one ---------------------------------------


def test_the_new_image_has_the_new_files_and_the_base_images_own_command(tmp_path, monkeypatch):
    image = "rezztor-test-netplan:trixie"
    if not shutil.which("docker") or subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        pytest.skip(f"needs Docker and the image {image}: the image is built on top of a real one")
    monkeypatch.setattr(tool, "ROOT", "")
    tree = tmp_path / "eagent.tar.xz"
    with lzma.open(tree, "wb") as packed, tarfile.open(fileobj=packed, mode="w|") as tar:
        for path, text in (("eagent/run_ui.py", b"new\n"), ("eagent/prompts/a.md", b"prompt\n")):
            info = tarfile.TarInfo(path)
            info.size, info.uid, info.gid = len(text), 1000, 1000
            tar.addfile(info, io.BytesIO(text))
    host, base, target = tool.Host(), "rezztor-test-update:base", "rezztor-test-update:new"
    docker = lambda *argv: subprocess.run(["docker", *argv], capture_output=True, text=True)  # noqa: E731
    try:
        # A base with an old tree: one file that must go, and the user's folder that must stay.
        docker("rm", "-f", "rezztor-test-update")
        made = docker("run", "--network", "none", "--name", "rezztor-test-update", image, "sh", "-c",
                      "mkdir -p /eagent/usr && echo old > /eagent/gone.py && echo mine > /eagent/usr/kept")  # fmt: skip
        assert made.returncode == 0, made.stderr
        docker("commit", "--change", 'CMD ["/exe/start", "$BRANCH"]', "--change", "ENV KEEP=1", "rezztor-test-update", base)
        assert host.build(base, str(tree), target) is None
        assert host.image(target)["Cmd"] == ["/exe/start", "$BRANCH"] and "KEEP=1" in host.image(target)["Env"]
        seen = docker("run", "--rm", "--network", "none", target, "sh", "-c",
                      "cat /eagent/run_ui.py /eagent/usr/kept; ls /eagent; stat -c %u /eagent/prompts/a.md")  # fmt: skip
        assert seen.stdout.split() == ["new", "mine", "prompts", "run_ui.py", "usr", "1000"], seen.stdout + seen.stderr
        assert host.build("rezztor-test-update:absent", str(tree), target) == "the image rezztor-test-update:absent is not on this machine"
        # A file that is not what it says ends in a sentence, not in a tar that waits for ever.
        (tmp_path / "not-xz.tar.xz").write_bytes(b"plain bytes, never compressed" * 4096)
        assert host.build(base, str(tmp_path / "not-xz.tar.xz"), target) is not None and host.image(target) is not None
        # The builder's own packing, read by the host: the tree of an image, without the user's folder.
        assert set(builder.tree_of(target)) == {"run_ui.py", "prompts/a.md"}
        builder.pack_tree(target, tmp_path / "packed.tar.xz")
        with lzma.open(tmp_path / "packed.tar.xz") as plain, tarfile.open(fileobj=plain, mode="r|") as tar:
            assert sorted(member.name for member in tar if member.isreg()) == ["eagent/prompts/a.md", "eagent/run_ui.py"]
        assert host.build(base, str(tmp_path / "packed.tar.xz"), "rezztor-test-update:again") is None
        assert host.tree(image="rezztor-test-update:again") == host.tree(image=target)
        # Texts into a running container: one file changed, one added, one gone, the rest untouched.
        assert host.tree(image=target) == {"run_ui.py": tool.hashlib.sha256(b"new\n").hexdigest(),
                                           "prompts/a.md": tool.hashlib.sha256(b"prompt\n").hexdigest()}  # fmt: skip
        docker("rm", "-f", "rezztor-test-update-live")
        live = docker("run", "-d", "--network", "none", "--name", "rezztor-test-update-live", image, "sh", "-c",
                      "mkdir -p /eagent/prompts /eagent/usr && echo old > /eagent/prompts/a.md && echo x > /eagent/prompts/gone.md"
                      " && echo new > /eagent/run_ui.py && echo mine > /eagent/usr/kept && chown -R 1000:1000 /eagent && sleep 600")  # fmt: skip
        assert live.returncode == 0, live.stderr
        for _ in range(50):
            if "prompts/gone.md" in host.tree(container="rezztor-test-update-live"):
                break
            tool.time.sleep(0.1)
        plan = tool.live_plan(host.tree(container="rezztor-test-update-live"), host.tree(image=target))
        assert plan == (["prompts/a.md"], ["prompts/gone.md"])
        assert host.copy_live(target, *plan, container="rezztor-test-update-live") is None
        assert host.tree(container="rezztor-test-update-live") == host.tree(image=target)
        assert docker("exec", "rezztor-test-update-live", "cat", "/eagent/usr/kept").stdout == "mine\n"
        # An image that starts through an entrypoint keeps it, and its command.
        docker("commit", "--change", 'ENTRYPOINT ["/usr/bin/env"]', "--change", 'CMD ["true"]', "rezztor-test-update", base)
        assert host.build(base, str(tree), target) is None
        assert (host.image(target)["Entrypoint"], host.image(target)["Cmd"]) == (["/usr/bin/env"], ["true"])
    finally:
        docker("rm", "-f", "rezztor-test-update", "rezztor-update-build", "rezztor-test-update-live")
        docker("rmi", base, target, "rezztor-test-update:again")


# --- the support bundle --------------------------------------------------------

SECRET_ENV = ENV + "REZZTOR_ADMIN_PASSWORD=Sup3r-S3cret-Value\nREZZTOR_AUDIT_KEY=0f9e8d7c6b5a49382716\nREZZTOR_PORT=50080\n"
LEAKY_LOG = """2026-10-15 login ok for admin with Sup3r-S3cret-Value
audit mac key 0f9e8d7c6b5a49382716 loaded
request token=abc123xyz&next=/app
{"password": "hunter2-hunter2", "password_changed": true}
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.x
proxy http://proxyuser:proxypass@proxy.example.com:3128
-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----
AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQY
port 50080 listening
"""
LEAKED = ("Sup3r-S3cret-Value", "0f9e8d7c6b5a49382716", "abc123xyz", "hunter2-hunter2", "eyJhbGciOiJIUzI1NiJ9",
          "proxypass", "MC4CAQAwBQYDK2VwBCIE", "AGE-SECRET-KEY-1QYQ")  # fmt: skip


def _bundle(machine):
    folder = machine.root / "var/lib/rezztor/updates/state/bundles"
    (only,) = [path for path in folder.iterdir() if path.name.endswith(".tar.gz")]
    with tarfile.open(only) as tar:
        return only, {member.name.split("/", 1)[1]: (tar.extractfile(member).read().decode(), member.mode) for member in tar.getmembers()}


def test_the_page_asks_for_a_bundle_and_gets_one_with_every_secret_blanked(machine):
    (machine.root / "var/lib/rezztor/stack/.env").write_text(SECRET_ENV)
    machine.parts = [("containers/rezztor-eagent.log", LEAKY_LOG)]
    _request(machine, id="b0b0b0b0b0b0b0b0", action="bundle", by="alice")
    assert run(machine, "serve")[0] == 0
    state = state_of(machine)
    assert state["request"] == "b0b0b0b0b0b0b0b0" and state["phase"] == "idle" and state["error"] is None
    assert state["bundle_error"] is None and len(state["bundles"]) == 1

    path, files = _bundle(machine)
    assert tool.BUNDLE_NAME.fullmatch(path.name) and state["bundles"][0]["file"] == path.name
    assert sorted(files) == ["README.txt", "REDACTED.txt", "containers/rezztor-eagent.log", "status.txt"]
    assert all(mode == 0o644 for _, mode in files.values()) and oct(path.stat().st_mode)[-3:] == "644"
    log = files["containers/rezztor-eagent.log"][0]
    everything = "".join(text for text, _ in files.values())
    assert not [secret for secret in LEAKED if secret in everything], everything
    assert "[removed: REZZTOR_ADMIN_PASSWORD]" in log and "[removed: REZZTOR_AUDIT_KEY]" in log
    # What is not a secret stays readable: support has to read the log.
    assert "port 50080 listening" in log and '"password_changed": true' in log and "proxy.example.com:3128" in log
    report = files["REDACTED.txt"][0]
    assert "containers/rezztor-eagent.log: the value of REZZTOR_ADMIN_PASSWORD, 1" in report and "private key, 1" in report
    assert "It reaches Rezztor only when you send it." in files["README.txt"][0]
    assert "support bundle" in (machine.root / "var/log/rezztor/update.log").read_text()


def test_a_bundle_that_cannot_be_written_is_said_and_leaves_an_update_alone(machine, monkeypatch):
    state_file = machine.root / "var/lib/rezztor/updates/state/state.json"
    run(machine, "status")
    state = json.loads(state_file.read_text())
    state["error"] = {"code": "x", "text": "an update's own refusal"}
    state_file.write_text(json.dumps(state))

    def full(host, by):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tool, "make_bundle", full)
    _request(machine, id="c0c0c0c0c0c0c0c0", action="bundle", by="alice")
    run(machine, "serve")
    state = state_of(machine)
    assert state["bundle_error"] == "The support bundle could not be made: No space left on device."
    assert state["error"]["text"] == "an update's own refusal" and state["request"] == "c0c0c0c0c0c0c0c0"


def test_old_bundles_go_after_seven_days_and_only_five_are_kept(machine):
    folder = machine.root / "var/lib/rezztor/updates/state/bundles"
    folder.mkdir(parents=True)
    clock = 1_790_000_000
    for day in range(8):
        path = folder / f"rezztor-support-box-202610{day + 1:02d}-120000.tar.gz"
        path.write_bytes(b"x")
        os.utime(path, (clock - (7 - day) * 86400 - 60,) * 2)
    (folder / "rezztor-support-box-20261009-120000.tar.gz.part").write_bytes(b"half")
    (folder / "not-a-bundle.txt").write_text("left alone")
    tool.ROOT = str(machine.root)
    tool.prune_bundles(clock)
    left = sorted(path.name for path in folder.iterdir())
    assert left == ["not-a-bundle.txt"] + [f"rezztor-support-box-202610{day:02d}-120000.tar.gz" for day in (4, 5, 6, 7, 8)]


def test_the_console_writes_the_bundle_to_a_stick_or_says_where_else_to_get_it(machine):
    machine.medium = "/dev/sdb1"
    code, said = run(machine, "bundle", "usb")
    assert code == 0 and "Support bundle made: rezztor-support-" in said and "Written to /dev/sdb1." in said
    assert machine.named("write_to_medium")[0][1].endswith(".tar.gz")
    machine.medium = None
    code, said = run(machine, "bundle", "usb")
    assert code == 1 and "No USB stick or disk to write to was found" in said and "Settings, Support bundle" in said
    assert len(state_of(machine)["bundles"]) >= 1
    with pytest.raises(SystemExit):
        run(machine, "bundle", "/etc/passwd")


def test_redaction_leaves_a_line_without_a_secret_as_it_was():
    text = "GET /api/health 200\nkeys release-2026a, release-2026b\n"
    assert tool.redact(text, {}) == (text, {})


# --- the update service --------------------------------------------------------
# A stand-in for Rezztor's update service (hello, session, the list, the file), over real HTTPS with a CA made in
# the test, answered by the real tool through its real network code. Only a UAT machine may be pointed at it: a
# release machine always asks update.rezztor.com, which a test never reaches.

UAT_ENV = ENV.replace("2026.10.08", "2026.10.08-uat")


def _plain_key(version):
    return tuple(int(part) for part in version.removesuffix("-uat").split("."))


class Service:
    def __init__(self, tmp_path, key):
        self.key, self.packages, self.calls, self.challenges, self.tokens = key, [], [], {}, set()
        self.plain = None  # what the challenge holds; None: 32 random bytes in hex, as the real service
        self.cut = None  # stop the first answer of a file after this many bytes
        self.downloadable, self.error = True, None
        cert, private = tmp_path / "service.pem", tmp_path / "service.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(private), "-out", str(cert), "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)  # fmt: skip
        self.ca = cert.read_text()
        service = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                service.calls.append(("POST", self.path, body))
                if self.path == "/v1/hello":
                    if body.get("update_key") != service.key:
                        return self.reply(404, {"error": "unknown_installation"})
                    plain = service.plain if service.plain is not None else secrets.token_hex(32)
                    armored = subprocess.run(["age", "-a", "-r", service.key], input=plain.encode(), capture_output=True, check=True)
                    ident = secrets.token_hex(8)
                    service.challenges[ident] = plain
                    return self.reply(200, {"id": ident, "challenge": armored.stdout.decode()})
                if service.challenges.pop(body.get("id"), None) != body.get("answer"):
                    return self.reply(403, {"error": "bad_answer"})
                token = secrets.token_hex(16)
                service.tokens.add(token)
                return self.reply(200, {"token": token, "expires_in": 600})

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                service.calls.append(("GET", url.path, dict(urllib.parse.parse_qsl(url.query)) or self.headers.get("Range")))
                if self.headers.get("Authorization", "").removeprefix("Bearer ") not in service.tokens:
                    return self.reply(401, {"error": "no_session"})
                if url.path == "/v1/updates":
                    after = dict(urllib.parse.parse_qsl(url.query))["from"]
                    left = [entry for entry, _ in service.packages if _plain_key(entry["version"]) > _plain_key(after)]
                    # As the portal does: only the first entry is made for this installation, so only it has a SHA-256.
                    left = [{**entry, "downloadable": index == 0 and service.downloadable, **({} if index == 0 else {"sha256": None})}
                            for index, entry in enumerate(left)]  # fmt: skip
                    return self.reply(200, {"plan": "paid", "licence": "active", "core": "2026.10.08-uat", "updates": left,
                                            "error": service.error})  # fmt: skip
                version = url.path.removeprefix("/v1/updates/").removesuffix("/file")
                data = next(data for entry, data in service.packages if entry["version"] == version)
                start = int(re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range") or "bytes=0-").group(1))
                self.send_response(206 if start else 200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                cut, service.cut = service.cut, None
                self.wfile.write(data[start:cut] if cut else data[start:])
                self.close_connection = True

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, private)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.url = f"https://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def offer(self, path, **entry):
        data = path.read_bytes()
        self.packages.append(({"version": path.name.removesuffix(".rezupd"), "base": "2026.10.08-uat", "min_from": "2026.10.08-uat",
                               "bridge": False, "released_at": "2026-10-12T09:00:00Z", "security": False, "size": len(data),
                               "sha256": hashlib.sha256(data).hexdigest(), "notes": ["A fix."],
                               "available_at": "2026-10-12T09:00:00Z", **entry}, data))  # fmt: skip

    def paths(self):
        return [(method, path) for method, path, _ in self.calls]


@pytest.fixture
def uat(machine, tmp_path, monkeypatch):
    """A UAT machine (ADR 0085), the only kind that may be pointed at a stand-in service, and that service."""
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    (machine.root / "var/lib/rezztor/stack/.env").write_text(UAT_ENV)
    machine.images, machine.healthy = {"2026.10.08-uat"}, {"2026.10.08-uat", "2026.10.12-uat", "2026.10.14-uat"}
    machine.service = Service(tmp_path, machine.update_key())
    yield machine
    machine.service.httpd.shutdown()


def uat_package(machine, tmp_path, version="2026.10.12-uat", min_from="2026.10.08-uat", **changes):
    return package(machine, tmp_path, name=f"{version}.rezupd", version=version, base="2026.10.08-uat", min_from=min_from, **changes)


def ask(machine, action, **fields):
    _request(machine, id="a1b2c3d4", action=action, by="admin", server=machine.service.url, ca_pem=machine.service.ca, **fields)
    run(machine, "serve")
    return state_of(machine)


@needs_tools
def test_refresh_says_hello_answers_the_challenge_and_writes_the_list(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path))
    state = ask(uat, "refresh")
    updates = state["updates"]
    assert updates["server"] == uat.service.url and updates["error"] is None and updates["checked_at"]
    assert (updates["plan"], updates["licence"], updates["core"]) == ("paid", "active", "2026.10.08-uat")
    entry = updates["available"][0]
    assert entry["version"] == "2026.10.12-uat" and entry["downloadable"] is True and entry["notes"] == ["A fix."]
    assert set(entry) == {"version", "base", "min_from", "bridge", "released_at", "security", "size", "sha256", "notes",
                          "available_at", "downloadable", "os"}  # fmt: skip
    assert entry["os"] is None  # the service says nothing of Debian: nothing is shown
    assert uat.service.paths() == [("POST", "/v1/hello"), ("POST", "/v1/session"), ("GET", "/v1/updates")]
    assert uat.service.calls[0][2] == {"update_key": uat.update_key()}
    assert uat.service.calls[2][2] == {"from": "2026.10.08-uat", "base": "2026.10.08-uat"}
    # The list is not the update: nothing else in the state moved, and no secret of the session was kept.
    assert state["phase"] == "idle" and state["error"] is None and state["request"] == "a1b2c3d4"
    answer, token = uat.service.calls[1][2]["answer"], next(iter(uat.service.tokens))
    for text in ((uat.root / "var/lib/rezztor/updates/state/state.json").read_text(), (uat.root / "var/log/rezztor/update.log").read_text()):
        assert answer not in text and token not in text
    # The address and CA the UAT machine was given last serve a request that names none.
    _request(uat, id="a1b2c3d5", action="refresh", by="admin")
    run(uat, "serve")
    assert state_of(uat)["updates"]["server"] == uat.service.url and state_of(uat)["updates"]["error"] is None


@needs_tools
@pytest.mark.parametrize("plain", ["A" * 64, "0" * 63, "0" * 64 + "\n", "0" * 65, "the licence root key"])
def test_a_challenge_that_is_not_64_lowercase_hex_is_never_answered(uat, plain):
    """The machine's key decrypts whatever it is given: answering anything but a fresh challenge would make it
    a decryption oracle for whoever controls the service's address."""
    uat.service.plain = plain
    updates = ask(uat, "refresh")["updates"]
    assert updates["error"]["code"] == "bad_challenge" and updates["available"] == []
    assert uat.service.paths() == [("POST", "/v1/hello")]


@needs_tools
def test_an_installation_the_service_does_not_know_is_told_to_register_its_key(uat):
    uat.service.key = "age1" + "q" * 58
    error = ask(uat, "refresh")["updates"]["error"]
    assert error["code"] == "unknown_installation" and uat.update_key() in error["text"]


@needs_tools
def test_a_release_machine_asks_rezztors_service_whatever_the_request_says(machine, tmp_path):
    """The address and CA in a request are for UAT only: the web application is not trusted to choose where a
    release machine fetches from, or which CA it believes."""
    seen = []

    def offline(request, context, proxy, timeout):
        seen.append((request.full_url, context, proxy))
        raise urllib.error.URLError("no network in a test")

    machine.open_url = offline
    machine.service = Service(tmp_path, machine.update_key())
    try:
        updates = ask(machine, "refresh")["updates"]
    finally:
        machine.service.httpd.shutdown()
    assert updates["server"] == tool.SERVER == "https://update.rezztor.com" and updates["error"]["code"] == "unreachable"
    assert [url for url, _, _ in seen] == ["https://update.rezztor.com/v1/hello"] and machine.service.calls == []
    assert not any("127.0.0.1" in str(cert.get("subject")) for cert in seen[0][1].get_ca_certs())
    assert not (machine.root / "var/lib/rezztor/updates/work/uat-server.json").exists()


@needs_tools
def test_only_the_first_update_is_downloaded_and_only_when_it_may_be(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path))
    uat.service.offer(uat_package(uat, tmp_path, version="2026.10.14-uat", min_from="2026.10.12-uat"), min_from="2026.10.12-uat")
    state = ask(uat, "download", version="2026.10.14-uat")
    assert state["error"]["code"] == "not_next" and "2026.10.12-uat comes first" in state["error"]["text"]
    uat.service.downloadable = False
    state = ask(uat, "download", version="2026.10.12-uat")
    assert state["error"]["code"] == "not_available" and "from 2026-10-12" in state["error"]["text"]
    assert not any(path.endswith("/file") for _, path in uat.service.paths())
    uat.service.downloadable = True
    state = ask(uat, "download", version="2026.10.12-uat")
    assert state["error"] is None and state["phase"] == "checked" and state["package"]["version"] == "2026.10.12-uat"
    assert state["download"] is None and state["updates"]["available"][0]["version"] == "2026.10.12-uat"
    assert [path for _, path in uat.service.paths() if path.endswith("/file")] == ["/v1/updates/2026.10.12-uat/file"]
    assert not list((uat.root / "var/lib/rezztor/updates/work/download").iterdir())
    # From here it is an uploaded file: install, restart.
    _request(uat, id="a1b2c3d5", action="install", by="admin")
    run(uat, "serve")
    assert state_of(uat)["phase"] == "staged"


@needs_tools
def test_a_download_that_is_not_the_listed_file_is_refused_and_deleted(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path), sha256="0" * 64)
    state = ask(uat, "download", version="2026.10.12-uat")
    assert state["error"]["code"] == "bad_download" and state["phase"] == "idle" and state["package"] is None
    assert not list((uat.root / "var/lib/rezztor/updates/work/download").iterdir())
    assert not (uat.root / "var/lib/rezztor/updates/work/package.rezupd").exists()


@needs_tools
def test_a_download_that_stopped_goes_on_where_it_stopped(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path))
    uat.service.cut = 1000
    state = ask(uat, "download", version="2026.10.12-uat")
    assert state["error"]["code"] == "download_incomplete" and "Download again" in state["error"]["text"]
    assert state["phase"] == "idle" and state["download"] is None
    assert (uat.root / "var/lib/rezztor/updates/work/download/package.part").stat().st_size == 1000
    state = ask(uat, "download", version="2026.10.12-uat")
    assert state["phase"] == "checked" and state["error"] is None
    assert [extra for _, path, extra in uat.service.calls if path.endswith("/file")] == [None, "bytes=1000-"]


@needs_tools
def test_a_download_cut_by_a_power_failure_is_idle_at_the_next_start_and_keeps_what_arrived(uat):
    state_file = uat.root / "var/lib/rezztor/updates/state/state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({"phase": "downloading", "download": {"version": "2026.10.12-uat", "received": 10, "size": 20}}))
    run(uat, "status")
    assert state_of(uat)["phase"] == "idle" and state_of(uat)["download"] is None


class Proxy:
    """A proxy that tunnels CONNECT, and writes down what it was asked."""

    def __init__(self):
        self.seen = []
        proxy = self

        class Tunnel(socketserver.BaseRequestHandler):
            def handle(self):
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                line = head.split(b"\r\n")[0].decode()
                proxy.seen.append(line)
                host, port = line.split()[1].rsplit(":", 1)
                with socket.create_connection((host, int(port))) as upstream:
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    ends = [self.request, upstream]
                    while True:
                        ready, _, _ = select.select(ends, [], [], 5)
                        if not ready:
                            return
                        for end in ready:
                            data = end.recv(65536)
                            if not data:
                                return
                            (upstream if end is self.request else self.request).sendall(data)

        socketserver.ThreadingTCPServer.daemon_threads = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Tunnel)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@needs_tools
def test_the_proxy_of_the_stacks_env_is_used_and_no_proxy_is_honoured(uat, tmp_path):
    proxy = Proxy()
    env = uat.root / "var/lib/rezztor/stack/.env"
    try:
        env.write_text(UAT_ENV + f"HTTPS_PROXY={proxy.url}\nNO_PROXY=172.31.248.0/21,devices.example\n")
        assert ask(uat, "refresh")["updates"]["error"] is None
        assert proxy.seen and all(line.startswith(f"CONNECT 127.0.0.1:{uat.service.httpd.server_port} ") for line in proxy.seen)
        before = len(proxy.seen)
        env.write_text(UAT_ENV + f"HTTPS_PROXY={proxy.url}\nNO_PROXY=10.0.0.0/8,127.0.0.0/8\n")
        assert ask(uat, "refresh")["updates"]["error"] is None and len(proxy.seen) == before
    finally:
        proxy.server.shutdown()
    assert tool._no_proxy("update.rezztor.com", "example.com, .rezztor.com") and tool._no_proxy("x.y", "*")
    assert not tool._no_proxy("update.rezztor.com", "rezztor.co,10.0.0.0/8") and not tool._no_proxy("10.1.1.1", "")


@needs_tools
def test_update_all_walks_the_chain_across_application_restarts(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path))
    uat.service.offer(uat_package(uat, tmp_path, version="2026.10.14-uat", min_from="2026.10.12-uat"), min_from="2026.10.12-uat")
    walked, compose = [], uat.compose

    def restarting(*args, timeout=0):  # what the page sees while the application is down
        if args == ("up", "-d"):
            walked.append(state_of(uat)["progress"])
        return compose(*args)

    uat.compose = restarting
    state = ask(uat, "update_all")
    assert state["error"] is None and state["installed"]["version"] == "2026.10.14-uat" and state["phase"] == "idle"
    assert walked == [{"done": 0, "total": 2, "current": "2026.10.12-uat"}, {"done": 1, "total": 2, "current": "2026.10.14-uat"}]
    assert [(entry["version"], entry["outcome"]) for entry in state["history"]] == [("2026.10.14-uat", "installed"), ("2026.10.12-uat", "installed")]
    assert state["progress"] is None and state["updates"]["available"] == [] and state["updates"]["error"] is None
    assert (uat.root / "var/lib/rezztor/stack/.env").read_text() == UAT_ENV.replace("2026.10.08-uat", "2026.10.14-uat")
    assert [path for _, path in uat.service.paths() if path.endswith("/file")] == ["/v1/updates/2026.10.12-uat/file",
                                                                                  "/v1/updates/2026.10.14-uat/file"]  # fmt: skip


@needs_tools
def test_update_all_stops_at_the_first_update_that_fails(uat, tmp_path):
    uat.healthy.discard("2026.10.14-uat")
    uat.service.offer(uat_package(uat, tmp_path))
    uat.service.offer(uat_package(uat, tmp_path, version="2026.10.14-uat", min_from="2026.10.12-uat"), min_from="2026.10.12-uat")
    state = ask(uat, "update_all")
    assert state["installed"]["version"] == "2026.10.12-uat" and state["result"]["outcome"] == "rolled_back"
    assert state["error"]["code"] == "rolled_back" and state["error"]["text"].startswith("1 of 2 updates installed, then: ")
    assert "returned to 2026.10.12-uat" in state["error"]["text"] and state["progress"] is None


@needs_tools
def test_update_all_goes_on_in_the_rezztor_update_an_update_brought(uat, tmp_path):
    (uat.root / "usr/local/sbin").mkdir(parents=True)
    uat.service.offer(uat_package(uat, tmp_path, host=["host/rezztor-update"], texts={"host/rezztor-update": "# the new tool\n"}))
    uat.service.offer(uat_package(uat, tmp_path, version="2026.10.14-uat", min_from="2026.10.12-uat"), min_from="2026.10.12-uat")
    state = ask(uat, "update_all")
    assert state["installed"]["version"] == "2026.10.12-uat" and state["progress"] == {"done": 1, "total": 2, "current": None}
    assert (uat.root / "usr/local/sbin/rezztor-update").read_text() == "# the new tool\n"
    request = json.loads((uat.root / "var/lib/rezztor/updates/inbox/request.json").read_text())
    assert request == {"id": "a1b2c3d4", "action": "update_all", "by": "admin"}
    run(uat, "serve")  # the path unit starts the new tool for the request it put back
    state = state_of(uat)
    assert state["installed"]["version"] == "2026.10.14-uat" and state["progress"] is None and state["error"] is None


@needs_tools
def test_a_bad_list_from_the_service_is_not_believed(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path), size=-1)
    assert ask(uat, "refresh")["updates"]["error"]["code"] == "bad_service_answer"
    uat.service.packages.clear()
    uat.service.offer(uat_package(uat, tmp_path), os={"packages": "13", "reboot": True})
    assert ask(uat, "refresh")["updates"]["error"]["code"] == "bad_service_answer"
    uat.service.packages.clear()
    uat.service.offer(uat_package(uat, tmp_path), os={"packages": 13, "reboot": True})  # ADR 0092: shown on the list
    assert ask(uat, "refresh")["updates"]["available"][0]["os"] == {"packages": 13, "reboot": True}
    uat.service.packages.clear()
    # A downloadable entry must say the SHA-256 of the file it will be; a later one may not know it yet.
    uat.service.offer(uat_package(uat, tmp_path), sha256=None)
    assert ask(uat, "refresh")["updates"]["error"]["code"] == "bad_service_answer"
    uat.service.packages.clear()
    uat.service.error = "chain_gap"
    updates = ask(uat, "refresh")["updates"]
    assert updates["error"]["code"] == "chain_gap" and "missing" in updates["error"]["text"]
    state = ask(uat, "update_all")
    assert state["error"] is None and state["progress"] is None  # nothing listed: nothing to walk


# --- the builder: the chain ----------------------------------------------------


def test_a_package_names_the_one_before_it_unless_it_is_the_first_of_its_core():
    rule = builder.min_from_of
    assert rule("2026.10.19", "2026.10.21", "2026.10.20", False) == "2026.10.20"
    assert rule("2026.10.19", "2026.10.20", None, True) == "2026.10.19"
    assert rule("2027.01.15", "2027.01.15", "2027.01.08", False) == "2027.01.08"  # a bridge from the old core's last
    assert rule("2026.10.19-uat", "2026.10.21-uat", "2026.10.20-uat", False) == "2026.10.20-uat"
    for refused in (("2026.10.19", "2026.10.21", None, False),          # no --min-from, not --first
                    ("2027.01.15", "2027.01.15", None, False),          # a bridge names the old core's last package
                    ("2027.01.15", "2027.01.15", None, True),           # a bridge is not a first package
                    ("2026.10.19", "2026.10.21", "2026.10.20", True),   # --first and --min-from
                    ("2026.10.19", "2026.10.21", "2026.10.21", False),  # min_from is older than the package
                    ("2026.10.19", "2026.10.21", "2026.10.18", False),  # and not older than its core
                    ("2026.10.19-uat", "2026.10.21-uat", "2026.10.20", False),
                    ("2026.10.19", "2026.10.21", "latest", False)):  # fmt: skip
        with pytest.raises(SystemExit):
            rule(*refused)
    args = builder.build_parser().parse_args(["package", "--base", "2026.10.19", "--version", "2026.10.20", "--first",
                                              "--key", "k", "--recipients", "r", "--out", "o"])  # fmt: skip
    assert args.first is True and args.min_from is None


def test_a_bridge_is_compared_with_the_last_package_of_the_old_core(monkeypatch):
    """What travels to the host and whether the database changes are decided against what the machines run when
    they take the bridge, not against the new core itself (which would find nothing changed)."""
    read = []
    monkeypatch.setattr(builder, "tree_of", lambda image: read.append(image) or {})
    args = SimpleNamespace(base="2027.01.15", version="2027.01.15", min_from="2027.01.08")
    with pytest.raises(SystemExit):
        builder.build_package(args, b"", None)  # stops at the missing build_inputs list, after both reads
    assert read == ["rezztor/eagent:2027.01.08", "rezztor/eagent:2027.01.15"]


# --- Debian's updates, the guard and the units (ADR 0092) ------------------------

KERNEL = [("linux-image-6.12.113+deb13-amd64", None, "6.12.113-1"), ("linux-image-amd64", "6.12.107-1", "6.12.113-1")]
CURL = [("curl", "8.14.1-2+deb13u5", "8.14.1-2+deb13u7")]


def _debian(**changes):
    item = {"name": "curl", "from": "8.14.1-2+deb13u5", "to": "8.14.1-2+deb13u7", "file": "os/curl_8.14.1-2+deb13u7_amd64.deb"}
    return {"snapshot": "20261020T000000Z", "packages": [item], "reboot": False, **changes}


def test_a_manifest_with_debian_updates_is_read_and_one_outside_the_rules_is_not():
    m = tool.read_manifest(_manifest(format=4, os=_debian(), host=["host/rezztor-guard", "units/rezztor-update.path"]))
    assert m["os"]["packages"][0]["name"] == "curl" and tool.view(m, "k", 1)["os"]["packages"] == [
        {"name": "curl", "from": "8.14.1-2+deb13u5", "to": "8.14.1-2+deb13u7"}]
    item = _debian()["packages"][0]
    for system in (_debian(snapshot="2026-10-20"), _debian(reboot="yes"), _debian(packages=[]),
                   _debian(packages=[{**item, "file": "os/../../etc/passwd.deb"}]), _debian(packages=[{**item, "file": "curl.deb"}]),
                   _debian(packages=[{**item, "name": "Curl;rm"}]), _debian(packages=[{**item, "to": "1 2"}]),
                   _debian(packages=[item, item]), "everything"):  # fmt: skip
        with pytest.raises(tool.Refused):
            tool.read_manifest(_manifest(format=4, os=system))
    with pytest.raises(tool.Refused):  # format 3 is what a tool from before ADR 0092 takes: no Debian part in it
        tool.read_manifest(_manifest(os=_debian()))
    # Both lists of what may reach the host are the same, and only the fixed units are on them.
    assert {name for name in tool.HOST_FILES if name.startswith("units/")} == {
        f"units/{unit}" for unit in ("rezztor-guard.service", "rezztor-guard-runner.service", "rezztor-firewall.service",
                                     "rezztor-load-images.service", "rezztor-setup.service", "rezztor-update.service",
                                     "rezztor-update.path")}  # fmt: skip
    assert tool.REBOOT.pattern == builder.REBOOT.pattern and tool.FORMAT_3_HOST == builder.FORMAT_3_HOST


@needs_tools
def test_debian_updates_are_installed_only_after_the_new_version_answers(machine, tmp_path):
    run(machine, "install", str(package(machine, tmp_path, debian=CURL)))
    assert not machine.named("apt_upgrade")  # installing prepares; the operating system is not touched
    code, said = run(machine, "restart")
    order = [call[:2] for call in machine.calls if call[0] in ("compose", "apt_upgrade")]
    assert order == [("compose", "stop"), ("compose", "up"), ("apt_upgrade", "/var/lib/rezztor/updates/work/unpacked/os")]
    assert machine.named("apt_upgrade")[0][2] == ["Packages", "curl_8.14.1-2+deb13u7_amd64.deb"]
    state = state_of(machine)
    assert code == 0 and state["installed"]["version"] == "2026.10.12" and "1 Debian package was updated." in said
    assert state["os"]["applied"] == 1 and state["os"]["error"] is None and state["os"]["reboot_needed"] is False
    assert state["os"]["packages"] == [{"name": "curl", "from": "8.14.1-2+deb13u5", "to": "8.14.1-2+deb13u7"}]
    assert state["result"]["outcome"] == "installed" and not (machine.root / "var/lib/rezztor/updates/work/unpacked").exists()
    # The next package carries the same update and more: what this machine has already is not counted again.
    machine.healthy.add("2026.10.14")
    run(machine, "install", str(package(machine, tmp_path, name="next.rezupd", version="2026.10.14", min_from="2026.10.12",
                                        debian=CURL + [("libc6", "2.41-12+deb13u4", "2.41-12+deb13u5")])))  # fmt: skip
    run(machine, "restart")
    state = state_of(machine)
    assert state["os"]["applied"] == 1 and state["os"]["packages"][0]["name"] == "libc6" and state["os"]["reboot_needed"] is True


@needs_tools
def test_debian_updates_are_not_installed_when_the_new_version_is_taken_back(machine, tmp_path):
    machine.healthy = {"2026.10.08"}
    run(machine, "install", str(package(machine, tmp_path, debian=KERNEL)))
    code, said = run(machine, "restart")
    state = state_of(machine)
    assert code == 1 and state["result"]["outcome"] == "rolled_back" and not machine.named("apt_upgrade")
    assert state["os"] is None and machine.dpkg == Machine.dpkg


@needs_tools
def test_an_apt_failure_keeps_the_new_version_and_says_so(machine, tmp_path):
    machine.apt_problem = "apt-get upgrade: E: Sub-process /usr/bin/dpkg returned an error code (1)"
    run(machine, "install", str(package(machine, tmp_path, debian=CURL)))
    code, said = run(machine, "restart")
    state = state_of(machine)
    assert code == 0 and state["installed"]["version"] == "2026.10.12" and state["result"]["outcome"] == "installed"
    assert state["os"]["error"]["code"] == "os_failed" and "dpkg returned an error" in state["os"]["error"]["text"]
    assert "Version 2026.10.12 runs. The next update carries them again." in state["result"]["text"]
    assert (machine.root / "var/lib/rezztor/stack/.env").read_text() == ENV.replace("2026.10.08", "2026.10.12")


@needs_tools
def test_the_appliance_needs_a_restart_for_a_new_kernel_or_the_flag_until_it_had_one(machine, tmp_path):
    boot = machine.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("first-boot\n")
    run(machine, "install", str(package(machine, tmp_path, debian=KERNEL)))
    code, said = run(machine, "restart")
    state = state_of(machine)
    assert state["os"]["reboot_needed"] is True and state["os"]["applied"] == 2 and "Restart the appliance to finish." in said
    run(machine, "status")
    assert state_of(machine)["os"]["reboot_needed"] is True  # the same boot: still needed
    boot.write_text("second-boot\n")
    run(machine, "status")
    assert state_of(machine)["os"]["reboot_needed"] is False and state_of(machine)["os"]["applied"] == 2
    # The last update's text no longer asks for the restart it had (UAT 2026-10-21, after .53).
    assert state_of(machine)["result"]["text"].endswith("Debian packages were updated. The appliance was restarted since.")
    # A package Debian installed that leaves /run/reboot-required asks for it as well.
    (machine.root / "run").mkdir()
    (machine.root / "run/reboot-required").write_text("*** System restart required ***\n")
    code, said = run(machine, "status")
    assert state_of(machine)["os"]["reboot_needed"] is True and "sudo rezztor-update reboot" in said


@needs_tools
def test_the_machine_decides_from_what_changed_not_from_what_the_package_carries(machine, tmp_path):
    """Every package carries every Debian update since its core. A kernel this machine already runs is no reason
    for another restart."""
    machine.dpkg = {**Machine.dpkg, "linux-image-amd64": "6.12.113-1", "linux-image-6.12.113+deb13-amd64": "6.12.113-1"}
    path = package(machine, tmp_path, debian=KERNEL + CURL)
    run(machine, "install", str(path))
    assert state_of(machine)["package"]["os"]["reboot"] is True  # what the page shows before
    run(machine, "restart")
    state = state_of(machine)
    assert state["os"]["applied"] == 1 and state["os"]["reboot_needed"] is False


GUARD_UNITS = ["host/rezztor-guard", "units/rezztor-guard-runner.service", "units/rezztor-guard.service"]


def _old_guard(machine):
    (machine.root / "opt/rezztor/rezztor-guard").write_text("# the old guard\n")
    (machine.root / "etc/systemd/system").mkdir(parents=True)
    for unit in ("rezztor-guard.service", "rezztor-guard-runner.service"):
        (machine.root / f"etc/systemd/system/{unit}").write_text(f"# the old {unit}\n")


@needs_tools
def test_the_guard_and_its_units_are_installed_and_loaded_before_the_containers_start(machine, tmp_path):
    _old_guard(machine)
    texts = {name: f"# the new {name}\n" for name in GUARD_UNITS}
    run(machine, "install", str(package(machine, tmp_path, format=4, host=GUARD_UNITS, texts=texts)))
    code, said = run(machine, "restart")
    assert code == 0 and state_of(machine)["installed"]["version"] == "2026.10.12"
    guard = machine.root / "opt/rezztor/rezztor-guard"
    assert guard.read_text() == "# the new host/rezztor-guard\n" and guard.stat().st_mode & 0o777 == 0o755
    unit = machine.root / "etc/systemd/system/rezztor-guard.service"
    assert unit.read_text() == "# the new units/rezztor-guard.service\n" and unit.stat().st_mode & 0o777 == 0o644
    order = [call[1:] if call[0] == "systemctl" else call[:2] for call in machine.calls if call[0] in ("systemctl", "compose")]
    assert order == [("compose", "stop"), ("daemon-reload",), ("reload-or-restart", "rezztor-guard.service"),
                     ("restart", "rezztor-guard-runner.service"), ("compose", "up")]  # fmt: skip


@needs_tools
def test_the_old_guard_and_units_come_back_with_the_old_version(machine, tmp_path):
    _old_guard(machine)
    machine.healthy = {"2026.10.08"}
    run(machine, "install", str(package(machine, tmp_path, format=4, host=GUARD_UNITS)))
    code, said = run(machine, "restart")
    assert code == 1 and state_of(machine)["result"]["outcome"] == "rolled_back"
    assert (machine.root / "opt/rezztor/rezztor-guard").read_text() == "# the old guard\n"
    assert (machine.root / "opt/rezztor/rezztor-guard").stat().st_mode & 0o777 == 0o755
    assert (machine.root / "etc/systemd/system/rezztor-guard.service").read_text() == "# the old rezztor-guard.service\n"
    # Loaded twice: the new rules, then the old ones again.
    assert machine.named("systemctl").count(("systemctl", "reload-or-restart", "rezztor-guard.service")) == 2
    assert machine.named("systemctl").count(("systemctl", "daemon-reload")) == 2


@needs_tools
def test_a_guard_that_does_not_load_takes_the_update_back(machine, tmp_path):
    _old_guard(machine)
    machine.systemctl_problems = {("reload-or-restart", "rezztor-guard.service"): "Job failed. See journalctl -xe."}
    run(machine, "install", str(package(machine, tmp_path, format=4, host=["host/rezztor-guard"])))
    code, said = run(machine, "restart")
    assert code == 1 and "the network guard did not load its rules" in said
    assert (machine.root / "opt/rezztor/rezztor-guard").read_text() == "# the old guard\n"
    assert state_of(machine)["installed"]["version"] == "2026.10.08"


@needs_tools
def test_the_page_restarts_the_appliance_only_when_it_needs_it_and_the_console_when_asked(machine, tmp_path):
    _request(machine, id="a1b2c3d4", action="reboot", by="admin")
    run(machine, "serve")
    state = state_of(machine)
    assert state["error"]["code"] == "no_reboot" and not machine.named("reboot")
    run(machine, "install", str(package(machine, tmp_path, debian=KERNEL)))
    _request(machine, id="a1b2c3d5", action="reboot", by="admin")
    run(machine, "serve")
    assert state_of(machine)["error"]["code"] == "no_reboot"  # an update waits; its Debian part is not in yet
    run(machine, "restart")
    _request(machine, id="a1b2c3d6", action="reboot", by="admin")
    run(machine, "serve")
    assert machine.named("reboot") == [("reboot",)] and state_of(machine)["error"] is None
    assert "restart of the appliance asked by admin" in (machine.root / "var/log/rezztor/update.log").read_text()
    machine.calls.clear()
    code, said = run(machine, "reboot")  # the console asks whatever the state says
    assert code == 0 and machine.named("reboot") == [("reboot",)]


@needs_tools
def test_update_all_leaves_the_restart_of_the_appliance_to_the_end(uat, tmp_path):
    uat.service.offer(uat_package(uat, tmp_path, debian=KERNEL))
    later = KERNEL + [("libc6", "2.41-12+deb13u4", "2.41-12+deb13u5")]
    uat.service.offer(uat_package(uat, tmp_path, version="2026.10.14-uat", min_from="2026.10.12-uat", debian=later),
                      min_from="2026.10.12-uat")  # fmt: skip
    state = ask(uat, "update_all")
    assert state["error"] is None and state["installed"]["version"] == "2026.10.14-uat" and state["progress"] is None
    assert len(uat.named("apt_upgrade")) == 2 and not uat.named("reboot")
    assert state["os"]["version"] == "2026.10.14-uat" and state["os"]["reboot_needed"] is True
    assert [item["name"] for item in state["os"]["packages"]] == ["libc6"]


def test_apt_reads_the_packages_folder_and_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "ROOT", str(tmp_path))
    ran = []
    monkeypatch.setattr(tool, "_run", lambda *argv, **kwargs: ran.append((argv, kwargs.get("env"))) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert tool.Host().apt_upgrade(tool.WORK + "/unpacked/os") is None
    (configure, _), (update, _), (upgrade, env) = ran
    assert configure == ("dpkg", "--configure", "-a", "--force-confold", "--force-confdef")
    work = f"{tmp_path}/var/lib/rezztor/updates/work/apt"
    for argv in (update, upgrade):
        assert argv[0] == "apt-get" and f"Dir::Etc::sourcelist={work}/sources.list" in argv and "Dir::Etc::sourceparts=/dev/null" in argv
        assert f"Dir::State::Lists={work}/lists" in argv and f"Dir::Cache={work}/cache" in argv
    assert upgrade[-3:] == ("-y", "--with-new-pkgs", "upgrade") and "Dpkg::Options::=--force-confold" in upgrade
    assert env["DEBIAN_FRONTEND"] == "noninteractive" and not os.path.exists(work)  # the list is gone afterwards
    ran.clear()
    monkeypatch.setattr(tool, "_run", lambda *argv, **kwargs: SimpleNamespace(
        returncode=0, stdout="ii \tcurl\t8.14.1-2+deb13u7\nrc \told\t1.0\nii \tlibc6\t2.41-12+deb13u4\n", stderr=""))
    assert tool.Host().dpkg_versions() == {"curl": "8.14.1-2+deb13u7", "libc6": "2.41-12+deb13u4"}


def test_a_failed_apt_run_reports_apts_error_lines_not_a_tail(monkeypatch, tmp_path):
    """Seen in UAT (package .52, a .deb cut in half): the last 300 characters began mid-checksum and never said
    which file or "Hash Sum mismatch"."""
    monkeypatch.setattr(tool, "ROOT", str(tmp_path))
    output = ("Get:1 file:/os ./ curl 8.14.1-2+deb13u7 [254 kB]\n" + "  - SHA1:" + "ab" * 200 + " [weak]\n"
              "E: Failed to fetch file:/os/./curl_8.14.1-2+deb13u7_amd64.deb  Hash Sum mismatch\n"
              "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?\n")
    results = iter([SimpleNamespace(returncode=0, stdout="", stderr="")] * 2 + [SimpleNamespace(returncode=100, stdout="", stderr=output)])
    monkeypatch.setattr(tool, "_run", lambda *argv, **kwargs: next(results))
    error = tool.Host().apt_upgrade(tool.WORK + "/unpacked/os")
    assert error.startswith("apt-get upgrade: E: Failed to fetch file:/os/./curl_8.14.1-2+deb13u7_amd64.deb  Hash Sum mismatch")
    assert "SHA1" not in error and len(error) <= len("apt-get upgrade: ") + 300
    assert tool._apt_error("no error lines here") == "no error lines here"


def test_a_tool_from_before_format_4_refuses_it_in_words():
    """The tool on every appliance installed before ADR 0092 reads format 3 only. It must refuse a package with a
    Debian part, the guard or units, not install the rest of it: the builder writes 4 for those, and 3 otherwise."""
    old = subprocess.run(["git", "-C", str(ROOT), "show", "6c964c6:deploy/iso/host/rezztor-update"], capture_output=True, text=True)
    if old.returncode:
        pytest.skip("the tool of 6c964c6 is not in this clone's history")
    path = ROOT / "tmp" / "rezztor-update-6c964c6"
    path.parent.mkdir(exist_ok=True)
    path.write_text(old.stdout)
    try:
        before = _load("rezztor_update_6c964c6", path)
    finally:
        path.unlink()
    with pytest.raises(before.Refused) as refused:
        before.read_manifest(_manifest(format=4, os=_debian()))
    assert refused.value.code == "format" and "newer update tool" in refused.value.text
    assert before.read_manifest(_manifest())["format"] == 3


# --- the builder: Debian's updates (ADR 0092) ------------------------------------

AVAILABLE = """Package: curl
Version: 8.14.1-2+deb13u5
Architecture: amd64
Depends: libcurl4t64 (= 8.14.1-2+deb13u5)
Description: command line tool for transferring data with URL syntax
 curl is a command line tool.
Filename: pool/main/c/curl/curl_8.14.1-2+deb13u5_amd64.deb
Size: 270000
SHA256: aaaa

Package: curl
Version: 8.14.1-2+deb13u7
Architecture: amd64
Filename: pool/main/c/curl/curl_8.14.1-2+deb13u7_amd64.deb
"""


def test_the_status_of_a_fresh_core_is_the_archives_own_stanzas_of_its_versions():
    status, missing = builder.dpkg_status("curl\t8.14.1-2+deb13u5\tamd64\nage\t1.2.1-1+b5\tamd64\n", AVAILABLE)
    first, second = builder.stanzas(status)
    assert first["Package"] == "curl" and first["Status"] == "install ok installed" and first["Depends"].startswith("libcurl4t64")
    assert first["Description"].endswith("\n curl is a command line tool.") and "Filename" not in first and "SHA256" not in first
    assert second == {"Package": "age", "Status": "install ok installed", "Version": "1.2.1-1+b5", "Architecture": "amd64",
                      "Maintainer": "the core's package list", "Description": "installed by the core's ISO"} and missing == 1  # fmt: skip
    for bad in ("curl 8.14.1 amd64\n", "curl\t8.14.1\n", "curl\t$(id)\tamd64\n", ""):
        with pytest.raises(SystemExit):
            builder.dpkg_status(bad)
    # The UAT core's list, read from the installed appliance, is one the builder takes.
    listed = (ROOT / "deploy/iso/cores/2026.10.19-uat.installed").read_text()
    assert len(builder.stanzas(builder.dpkg_status(listed)[0])) == len(listed.splitlines()) > 200


def test_docker_gives_only_the_pinned_versions_and_debian_comes_from_the_snapshot():
    preferences = builder.docker_preferences("docker-ce=5:29.8.2-1~debian.13~trixie containerd.io=2.3.6-1~debian.13~trixie")
    assert "Package: *\nPin: origin download.docker.com\nPin-Priority: -1" in preferences
    assert "Package: docker-ce\nPin: version 5:29.8.2-1~debian.13~trixie\nPin-Priority: 990" in preferences
    sources = builder.os_sources("20261020T000000Z")
    for suite in ("debian/20261020T000000Z/ trixie main", "debian/20261020T000000Z/ trixie-updates main",
                  "debian-security/20261020T000000Z/ trixie-security main"):  # fmt: skip
        assert f"https://snapshot.debian.org/archive/{suite}" in sources
    pins = builder.read_pins()
    assert pins["TOOLS_BASE"].startswith("debian:trixie-slim@sha256:") and "docker-ce=" in pins["OS_PACKAGE_PINS"]
    assert "--with-new-pkgs upgrade" in builder.RESOLVE and "--download-only" in builder.RESOLVE


def _fake_resolve(monkeypatch, simulate, debs):
    ran = []

    def in_tools(work, pins, script):
        ran.append(script)
        if script == builder.PREPARE:
            (work / "available").write_text(AVAILABLE)
            return
        (work / "simulate.txt").write_text(simulate)
        index = []
        for name, version in debs:
            file = f"{name}_{version.replace(':', '%3a')}_amd64.deb"
            (work / "os" / file).write_bytes(b"deb")
            index.append(f"Package: {name}\nVersion: {version}\nArchitecture: amd64\nFilename: ./{file}\nSize: 3\n")
        (work / "os" / "Packages").write_text("\n".join(index))

    monkeypatch.setattr(builder, "_in_tools", in_tools)
    return ran


def test_the_os_part_carries_what_apt_would_upgrade_on_a_fresh_core(monkeypatch, tmp_path):
    simulate = ("NOTE: This is only a simulation!\n"
                "Inst linux-image-6.12.113+deb13-amd64 (6.12.113-1 Debian-Security:13/stable-security [amd64])\n"
                "Inst linux-image-amd64 [6.12.107-1] (6.12.113-1 Debian-Security:13/stable-security [amd64])\n"
                "Inst curl [8.14.1-2+deb13u5] (8.14.1-2+deb13u7 Debian-Security:13/stable-security [amd64])\n"
                "Conf curl (8.14.1-2+deb13u7 Debian-Security:13/stable-security [amd64])\n")  # fmt: skip
    debs = [("linux-image-6.12.113+deb13-amd64", "6.12.113-1"), ("linux-image-amd64", "6.12.113-1"), ("curl", "8.14.1-2+deb13u7")]
    ran = _fake_resolve(monkeypatch, simulate, debs)
    part, parts = builder.os_part("2026.10.19-uat", "20261020T000000Z", tmp_path)
    assert ran == [builder.PREPARE, builder.RESOLVE]
    assert part["snapshot"] == "20261020T000000Z" and part["reboot"] is True
    assert part["packages"][0] == {"name": "linux-image-6.12.113+deb13-amd64", "from": None, "to": "6.12.113-1",
                                   "file": "os/linux-image-6.12.113+deb13-amd64_6.12.113-1_amd64.deb"}  # fmt: skip
    assert sorted(parts) == ["os/Packages", *sorted(item["file"] for item in part["packages"])]
    # The status apt resolved against holds the core's packages with the archive's stanza where it has one.
    assert builder.stanzas((tmp_path / "list-0" / "status").read_text())[0]["Package"] == "adduser"
    assert builder.packages_index(parts["os/Packages"].read_text()) == {(name, version): item["file"].removeprefix("os/")
                                                                     for (name, version), item in zip(debs, part["packages"])}  # fmt: skip
    assert "kernel linux-image-6.12.113+deb13-amd64 6.12.113-1" in builder.os_summary(part, parts)
    # The tool reads what the builder writes.
    m = tool.read_manifest(_manifest(format=4, os=part))
    assert len(tool.view(m, "k", 1)["os"]["packages"]) == 3


def test_the_os_part_is_left_out_when_nothing_is_newer_and_refused_when_a_file_is_missing(monkeypatch, tmp_path):
    _fake_resolve(monkeypatch, "Reading package lists...\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n", [])
    assert builder.os_part("2026.10.19-uat", "20261020T000000Z", tmp_path / "a") == (None, {})
    _fake_resolve(monkeypatch, "Inst curl [8.14.1-2+deb13u5] (8.14.1-2+deb13u7 Debian:13.7/stable [amd64])\n", [])
    with pytest.raises(SystemExit):
        builder.os_part("2026.10.19-uat", "20261020T000000Z", tmp_path / "b")
    for core, snapshot in (("2026.10.18", "20261020T000000Z"), ("2026.10.19-uat", "2026-10-20")):
        with pytest.raises(SystemExit):
            builder.os_part(core, snapshot, tmp_path / "c")


def _built(monkeypatch, tmp_path, changed, system=None, **options):
    """build_package with the images and the signing replaced: (manifest, parts)."""
    base, version = "2026.10.19-uat", "2026.10.21-uat"
    trees = {f"rezztor/eagent:{base}": {builder.BUILD_INPUTS: "1", **{path: "old" for path in changed}},
             f"rezztor/eagent:{version}": {builder.BUILD_INPUTS: "1", **{path: "new" for path in changed}}}  # fmt: skip
    monkeypatch.setattr(builder, "tree_of", lambda image: trees[image])

    def from_image(image, command, out):
        out.write(json.dumps({"version": version, "licence": "uat"}).encode() if "build_info" in command else b"file\n")

    monkeypatch.setattr(builder, "_from_image", from_image)
    monkeypatch.setattr(builder, "pack_tree", lambda image, target: target.write_bytes(b"tree"))
    monkeypatch.setattr(builder, "read_recipients", lambda path: ["age1x"])
    def os_part(core, snapshot, work):
        work.mkdir()
        (work / "Packages").write_text("Package: curl\n")
        return system, {"os/Packages": work / "Packages"} if system else {}

    monkeypatch.setattr(builder, "os_part", os_part)
    written = {}

    def write_package(out, manifest, parts, *rest):
        out.write_bytes(b"package")
        written.update(manifest=manifest, parts=parts)
        return manifest

    monkeypatch.setattr(builder, "write_package", write_package)
    settings = {"os_snapshot": None, "os_core": None, "format": None, **options}
    args = SimpleNamespace(base=base, version=version, min_from=base, start_minutes=None, force_image=None, note=["n"],
                           recipients="r", out=str(tmp_path), **settings)  # fmt: skip
    builder.os_choices(args)
    builder.build_package(args, b"", None)
    return written["manifest"], written["parts"]


def test_the_builder_writes_format_4_only_for_what_needs_it(monkeypatch, tmp_path):
    manifest, parts = _built(monkeypatch, tmp_path, {"helpers/db.py", "deploy/iso/host/rezztor-update"})
    assert manifest["format"] == 3 and manifest["host"] == ["host/rezztor-update"] and "os" not in manifest
    guard = {"helpers/db.py", "deploy/customer/rezztor-guard", "deploy/iso/host/rezztor-update.path"}
    manifest, parts = _built(monkeypatch, tmp_path, guard)
    assert manifest["format"] == 4 and manifest["host"] == ["host/rezztor-guard", "units/rezztor-update.path"]
    system = {"snapshot": "20261020T000000Z", "packages": [{"name": "curl", "from": "1", "to": "2", "file": "os/curl_2_amd64.deb"}], "reboot": False}
    manifest, parts = _built(monkeypatch, tmp_path, {"helpers/db.py"}, system, os_snapshot="20261020T000000Z")
    assert manifest["format"] == 4 and manifest["os"] == system and "os/Packages" in parts


def test_format_3_is_for_the_old_tool_and_holds_back_what_it_cannot_take(monkeypatch, tmp_path):
    guard = {"helpers/db.py", "deploy/customer/rezztor-guard", "deploy/iso/host/rezztor-update"}
    manifest, parts = _built(monkeypatch, tmp_path, guard, format=3)
    assert manifest["format"] == 3 and manifest["host"] == ["host/rezztor-update"] and "host/rezztor-guard" not in parts
    tool.read_manifest(_manifest(host=manifest["host"]))
    refused = (dict(os_snapshot="20261020T000000Z", format=3, base="2026.10.19", version="2026.10.21", os_core=None),
               dict(os_snapshot="20261020T000000Z", format=None, base="2027.01.15", version="2027.01.15", os_core=None),
               dict(os_snapshot=None, format=None, base="2026.10.19", version="2026.10.21", os_core="2026.10.19"))  # fmt: skip
    for args in refused:
        with pytest.raises(SystemExit):
            builder.os_choices(SimpleNamespace(**args))
    builder.os_choices(SimpleNamespace(os_snapshot="20261020T000000Z", format=None, base="2027.01.15", version="2027.01.15",
                                       os_core="2026.10.19"))  # a bridge names the old core  # fmt: skip


def test_every_package_list_of_a_core_is_resolved_and_a_package_goes_in_once(monkeypatch, tmp_path):
    """A BIOS and a UEFI install of one ISO differ (grub-pc or GRUB for EFI): each list is resolved, and what both
    need travels once."""
    cores = tmp_path / "cores"
    cores.mkdir()
    (cores / "2027.01.15.installed").write_text("curl\t8.14.1-2+deb13u5\tamd64\ngrub-pc\t2.12-9+deb13u2\tamd64\n")
    (cores / "2027.01.15.uefi.installed").write_text("curl\t8.14.1-2+deb13u5\tamd64\ngrub-efi-amd64\t2.12-9+deb13u2\tamd64\n")
    (cores / "2027.01.150.installed").write_text("not\tthis\tcore\n")
    monkeypatch.setattr(builder, "CORES", cores)
    assert [path.name for path in builder.core_lists("2027.01.15")] == ["2027.01.15.installed", "2027.01.15.uefi.installed"]

    def in_tools(work, pins, script):
        if script == builder.PREPARE:
            (work / "available").write_text("")
            return
        grub = "grub-efi-amd64" if "grub-efi" in (work / "status").read_text() else "grub-pc"
        lines, index = [], []
        for name in ("curl", grub):
            version = "8.14.1-2+deb13u7" if name == "curl" else "2.12-9+deb13u3"
            lines.append(f"Inst {name} [old] ({version} Debian-Security:13/stable-security [amd64])")
            (work / "os" / f"{name}_{version}_amd64.deb").write_bytes(b"deb")
            index.append(f"Package: {name}\nVersion: {version}\nFilename: ./{name}_{version}_amd64.deb\n")
        (work / "simulate.txt").write_text("\n".join(lines) + "\n")
        (work / "os" / "Packages").write_text("\n".join(index))

    monkeypatch.setattr(builder, "_in_tools", in_tools)
    part, parts = builder.os_part("2027.01.15", "20270115T000000Z", tmp_path / "work")
    assert [item["name"] for item in part["packages"]] == ["curl", "grub-pc", "grub-efi-amd64"] and part["reboot"] is False
    assert sorted(name for name, _ in builder.packages_index(parts["os/Packages"].read_text())) == ["curl", "grub-efi-amd64", "grub-pc"]

#!/usr/bin/env python3
"""Rezztor EAgent release signing — Rezztor's side, never shipped.

Two subcommands:

  keypair  Generate an Ed25519 release keypair. The private key is written,
           encrypted with a passphrase, to a path outside this repository (the
           tool refuses anything inside it) and must be kept secret AND backed
           up: without it, no appliance that trusts only this key can ever be
           updated again. The public key goes in deploy/iso/keys/<name>.pub;
           its file name is the key's name, and every appliance installed from
           an ISO built after that trusts it.

  package  Make an update package for installed appliances: one file,
           rezztor-eagent-update-<version>.rezupd, a plain tar of manifest.json,
           manifest.sig and payload.age. It compares the application image of
           the new version with the one of the base release and packs what
           differs: the application's own files as a tree (about 10 MB), a whole
           image only where its inputs changed. The host tool
           deploy/iso/host/rezztor-update is the other half and the reader of
           every field written here.

  python scripts/sign_release.py keypair --private-out /path/to/keys/release-2026a.pem \\
      --public-out deploy/iso/keys/release-2026a.pub

  BUILD_VERSION=2026.10.12 scripts/build_local.sh app && docker tag rezztor/eagent:latest rezztor/eagent:2026.10.12
  python scripts/sign_release.py package --base 2026.10.08 --version 2026.10.12 --min-from 2026.10.10 \\
      --key /path/to/keys/release-2026a.pem --recipients /path/to/recipients.txt \\
      --note "Models: the context length of Ollama Cloud models is now found." --out ~/rezztor-updates

  os-updates  Say which Debian updates a package would carry for machines installed from a core, from
           Debian's snapshot archive at a given time; signs nothing (ADR 0092).

  python scripts/sign_release.py os-updates --core 2026.10.19-uat --snapshot 20261020T000000Z

A package may carry Debian's updates since its core (--os-snapshot, ADR 0092): the tools container
(deploy/iso/tools.Dockerfile on the pinned TOOLS_BASE) resolves `apt-get upgrade --with-new-pkgs` for a
machine that has exactly the core's packages (deploy/iso/cores/<core>.installed) against trixie, -updates and
-security as snapshot.debian.org had them then, and Docker's repository at the versions pins.env names. The
.debs and their Packages index travel under os/. A package that carries them, the guard or a unit is format 4;
any other is format 3, which the tool of an appliance installed before ADR 0092 also takes. --format 3 makes the
package that brings the new tool to such a machine (no Debian part; the guard and the units wait for the next).

Packages are a strict chain (the release model of 2026-10-20): `--min-from` is the package before this one.
The first package of a core says `--first` instead (min_from is then the core). A bridge, a new core delivered
as a package (`--base` = `--version`, every image whole), still names `--min-from`: the last package of the old
core, whose image it is compared with. scripts/build_release.sh runs this from a tag.

The recipients file holds one update key (age1...) a line, as each appliance
shows it on its Updates page; a line starting with # is a comment. Encrypting
needs the `age` program on this machine.

Why the package is signed, and why it is built on the base release, is ADR 0080.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

# A package that carries Debian updates, the guard or a unit is format 4 (ADR 0092); one that carries none of
# them is written as format 3, which a tool from before ADR 0092 still takes. Such a tool refuses format 4 in
# words ("made for a newer update tool"), so the package that brings the new tool to it is made with --format 3.
FORMAT = 4
FORMAT_3_HOST = ("stack/docker-compose.yml", "host/rezztor-update", "host/rezztor-setup", "host/rezztor-network")
PRODUCT = "rezztor-eagent"
IMAGES = ("eagent", "eagent-runner", "eagent-ansible", "eagent-cloud")
# What each image is built from, as the paths lie in the application's tree (which is the repository).
# A change under one of these means the image itself changed and travels whole. For the application
# these are the inputs that land outside /eagent; everything else of it travels as files.
# tests/test_update_tool.py compares the lists with the COPY lines of the Dockerfiles.
INPUTS = {
    "eagent": ("requirements.txt", "Dockerfile", "docker/base/", "docker/run/", "docker/compile_python.py"),
    "eagent-runner": ("docker/runner/", "docker/run/fs/exe/node_eval.js"),
    "eagent-ansible": (
        "executor/",
        "plugins/_ea_ansible/__init__.py",
        "plugins/_ea_ansible/helpers/",
        "plugins/_ea_ansible/catalogue.yaml",
        "plugins/_ea_ansible/playbooks/",
        "docker/compile_python.py",
    ),
    "eagent-cloud": (
        "cloud_executor/",
        "executor/secret_env.py",
        "executor/credentials.py",
        "executor/store.py",
        "plugins/_ea_azure/helpers/prices.py",
        "docker/compile_python.py",
    ),
}
# Files of the tree that also live on the host, and the name the host tool knows them by.
HOST = {
    "deploy/customer/docker-compose.yml": "stack/docker-compose.yml",
    "deploy/iso/host/rezztor-update": "host/rezztor-update",
    "deploy/iso/host/rezztor-setup": "host/rezztor-setup",
    "deploy/iso/host/rezztor-network": "host/rezztor-network",
    # The network guard and the systemd units (ADR 0092), from a fixed list. The host tool holds the same list.
    "deploy/customer/rezztor-guard": "host/rezztor-guard",
    "deploy/customer/rezztor-guard.service": "units/rezztor-guard.service",
    "deploy/customer/rezztor-guard-runner.service": "units/rezztor-guard-runner.service",
    "deploy/iso/host/rezztor-firewall.service": "units/rezztor-firewall.service",
    "deploy/iso/host/rezztor-load-images.service": "units/rezztor-load-images.service",
    "deploy/iso/host/rezztor-setup.service": "units/rezztor-setup.service",
    "deploy/iso/host/rezztor-update.service": "units/rezztor-update.service",
    "deploy/iso/host/rezztor-update.path": "units/rezztor-update.path",
}
# A release key added to the tree since the base travels to the host as keys/<name>.pub, and the host
# adds it under a name it does not have. A key removed or changed here is not sent: the host never
# replaces or removes one (ADR 0080). Support keys (keys/support/) are not release keys.
NEW_KEY = re.compile(r"deploy/iso/keys/([a-z0-9][a-z0-9-]{0,62}\.pub)")
# Read from disk at every use, or never read by the running product: a change only here needs no
# restart. Anything else does. When in doubt a path is not in this list. The host tool holds the same
# list and decides again, from what really differs on the machine.
LIVE = ("prompts/", "frontend/dist/", "frontend/src/", "docs/", "build_info.json")
# The hashes of what the images were built from, written by scripts/build_local.sh. The image carries
# no Dockerfile, no docker/ and no requirements file, so this list is where a change of one is seen.
BUILD_INPUTS = "build_inputs.sha256"
TREE = "cd /eagent && find . -path ./usr -prune -o -type f -print0 | xargs -0 sha256sum"


def generate_keypair(passphrase: bytes | None = None) -> tuple[str, str]:
    """A new Ed25519 keypair as (private PEM, public PEM); the private half encrypted when a passphrase is given."""
    key = Ed25519PrivateKey.generate()
    lock = serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption()
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, lock).decode("utf-8")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    return private_pem, public_pem


def _refuse_inside_repo(path: Path, what: str) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        sys.exit(f"refusing to write {what} inside the repository: {resolved}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sign(body: bytes, private_pem: bytes, passphrase: bytes | None = None) -> bytes:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        key = load_pem_private_key(private_pem, password=passphrase)
    except (TypeError, ValueError):
        sys.exit("the release key could not be opened: wrong passphrase, or not a private key")
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("the release key is not an Ed25519 private key")
    return base64.b64encode(key.sign(body)) + b"\n"


# --- what changed, and what that means -----------------------------------------


def changed_paths(base: dict[str, str], new: dict[str, str]) -> set[str]:
    """Paths added, removed or different between two trees given as {path: sha256}."""
    return {path for path in base.keys() | new.keys() if base.get(path) != new.get(path)}


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or (prefix.endswith("/") and path.startswith(prefix)) for prefix in prefixes)


def classify(changed: set[str], added: set[str], force_image: tuple[str, ...] = ()) -> dict:
    """From the changed paths to the package's shape: which image travels how, which host files,
    whether the database changes and whether a restart is needed. Pure, and the only place that decides."""
    images = {}
    for name in IMAGES:
        if name in force_image or any(_under(path, INPUTS[name]) for path in changed):
            images[name] = "image"
    if "eagent" not in images and changed:
        images["eagent"] = "files"
    host = sorted(HOST[path] for path in changed if path in HOST)
    host += sorted(f"keys/{key[1]}" for path in added if (key := NEW_KEY.fullmatch(path)))
    database = any(path.startswith("helpers/schema/") for path in added)
    live = bool(changed) and not host and not database and images == {"eagent": "files"} and all(_under(p, LIVE) for p in changed)
    return {"images": images, "host": host, "database_change": database, "restart": "none" if live else "application"}


# --- docker --------------------------------------------------------------------


def _docker(*argv: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *argv], check=False, **kwargs)


def tree_of(image: str) -> dict[str, str]:
    """{path: sha256} of the application's tree inside an image."""
    done = _docker("run", "--rm", "--network", "none", "--entrypoint", "sh", image, "-c", TREE, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"cannot read the application tree of {image}: {done.stderr.strip()[-300:] or 'is the image on this machine?'}")
    tree = {}
    for line in done.stdout.splitlines():
        digest, _, path = line.partition("  ")
        tree[path.removeprefix("./")] = digest
    # What the image was built from and does not carry, each under its path in the repository. A path
    # the image does carry keeps the hash of what is in it.
    inputs = _docker("run", "--rm", "--network", "none", "--entrypoint", "sh", image, "-c",
                     f"cat /eagent/{BUILD_INPUTS} 2>/dev/null", capture_output=True, text=True)  # fmt: skip
    for line in inputs.stdout.splitlines():
        digest, _, path = line.partition("  ")
        tree.setdefault(path, digest)
    return tree


def _from_image(image: str, command: str, out) -> None:
    """Run `command` in the image and copy what it prints into `out`. Through a pipe, not as the
    command's own output file: `out` may be a compressor, and its file number is the file under it."""
    run = subprocess.Popen(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", image, "-c", command],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    shutil.copyfileobj(run.stdout, out, 1 << 20)
    if run.wait():
        sys.exit(f"`{command}` failed in {image}: is the image on this machine?")


def pack_tree(image: str, target: Path) -> None:
    """The application's files of an image as the xz tar the host tool unpacks into the base image.
    Read back before anything is signed: a file the appliance cannot open must not leave this machine."""
    with lzma.open(target, "wb", preset=6) as handle:
        _from_image(image, "tar -C / --exclude=eagent/usr --numeric-owner -cf - eagent", handle)
    with lzma.open(target, "rb") as plain, tarfile.open(fileobj=plain, mode="r|") as tar:
        names = {member.name for member in tar if member.isreg()}
    if "eagent/run_ui.py" not in names or any(name.startswith("eagent/usr/") for name in names):
        sys.exit(f"the application tree of {image} is not what the appliance expects")


# --- Debian updates (ADR 0092) -------------------------------------------------
# A core's ISO installs Debian from its point release and nothing newer. A package carries every Debian update
# since then, from a snapshot of Debian's archive on a named date, so the same arguments give the same packages.
# The machine applies them from the payload, offline, after the new version of the application answers.

CORES = REPO_ROOT / "deploy" / "iso" / "cores"
PINS = REPO_ROOT / "deploy" / "iso" / "pins.env"
TOOLS_DOCKERFILE = REPO_ROOT / "deploy" / "iso" / "tools.Dockerfile"
TOOLS_IMAGE = "rezztor-iso-tools:local"
SNAPSHOT = re.compile(r"\d{8}T\d{6}Z")
# A change of one of these takes a restart of the appliance to be in force. The host tool holds the same rule.
REBOOT = re.compile(r"linux-image-.+|libc6|systemd|dbus|dbus-daemon")
# Inst NAME [OLD] (NEW SUITE [ARCH]): apt-get's simulation, one line a package it would install.
INST = re.compile(r"Inst (\S+?)(?::\S+)? (?:\[(\S+)\] )?\((\S+) ")
# Run in the tools container, twice. /work holds status, sources.list and preferences. PREPARE reads the archive's
# lists into /work/lists and writes every stanza in them to /work/available; RESOLVE then simulates and downloads
# the upgrade against /work/status (made from the core's list and those stanzas): /work/simulate.txt and /work/os.
APT = """apt-get -q -o Dir::Etc::sourcelist=/work/sources.list -o Dir::Etc::sourceparts=/dev/null
     -o Dir::Etc::preferences=/work/preferences -o Dir::Etc::preferencesparts=/dev/null
     -o Dir::State::status=/work/status -o Dir::State::Lists=/work/lists -o Dir::Cache=/tmp/cache
     -o Dir::Cache::archives=/work/os -o APT::Architecture=amd64 -o APT::Install-Recommends=false
     -o Acquire::Check-Valid-Until=false"""
PREPARE = r"""
curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.asc
fpr=$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: '$1 == "fpr" {print $10}' | head -1)
[ "$fpr" = "$DOCKER_KEY_FPR" ] || { echo "error: Docker signing key is $fpr, not the pinned one" >&2; exit 1; }
gpg --dearmor < /tmp/docker.asc > /work/docker.gpg
mkdir -p /work/lists/partial /tmp/cache
$APT update
$APT indextargets --format '$(FILENAME)' 'Created-By: Packages' | while read -r list; do
    if [ -f "$list" ]; then /usr/lib/apt/apt-helper cat-file "$list"; echo; fi
done > /work/available
"""
RESOLVE = r"""
mkdir -p /work/os/partial /tmp/cache
$APT -s --with-new-pkgs upgrade > /work/simulate.txt
$APT -y --download-only --with-new-pkgs upgrade
cd /work/os && rm -rf partial lock
apt-ftparchive packages . > Packages
"""
# What a status file does not carry of an archive's stanza.
NOT_IN_STATUS = ("Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512", "Description-md5")


def read_pins(path: Path = PINS) -> dict[str, str]:
    """The KEY=value lines of pins.env, as scripts/build_iso.sh sources them."""
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            pins[key] = value.strip().strip("'\"")
    return pins


def stanzas(text: str) -> list[dict[str, str]]:
    """The stanzas of a Packages or status file, each as {field: value}, continuation lines kept."""
    found = []
    for block in text.split("\n\n"):
        fields, last = {}, None
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and last:
                fields[last] += "\n" + line
            elif ": " in line or line.endswith(":"):
                last, _, value = line.partition(":")
                fields[last] = value.strip()
        if "Package" in fields:
            found.append(fields)
    return found


def dpkg_status(installed: str, available: str = "") -> tuple[str, int]:
    """A dpkg status file for a machine that has exactly the packages of a core's `.installed` list
    (Package, Version, Architecture, tab-separated), and how many of them the archive's lists did not hold.

    Each package gets the archive's own stanza of its installed version, so that apt sees that very version
    installed: with a bare one apt takes the archive's same version for another, and would reinstall it."""
    archive = {}
    for fields in stanzas(available):
        archive.setdefault((fields["Package"], fields.get("Version"), fields.get("Architecture")), fields)
    out, missing = [], 0
    for number, line in enumerate(installed.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not all(re.fullmatch(r"[A-Za-z0-9.+~:_-]+", field) for field in fields):
            sys.exit(f"the core's package list, line {number}, is not Package, Version and Architecture: {line[:80]!r}")
        name, version, arch = fields
        known = archive.get((name, version, arch))
        missing += known is None
        known = known or {"Package": name, "Version": version, "Architecture": arch, "Maintainer": "the core's package list",
                          "Description": "installed by the core's ISO"}  # fmt: skip
        stanza = {"Package": name, "Status": "install ok installed",
                  **{key: value for key, value in known.items() if key not in ("Package", *NOT_IN_STATUS)}}  # fmt: skip
        out.append("".join(f"{key}: {value}\n" for key, value in stanza.items()))
    if not out:
        sys.exit("the core's package list is empty")
    return "\n".join(out), missing


def os_sources(snapshot: str) -> str:
    """trixie, -updates and -security as Debian's snapshot archive had them at `snapshot`, and Docker's repository."""
    debian = "[signed-by=/usr/share/keyrings/debian-archive-keyring.gpg]"
    return (
        f"deb {debian} https://snapshot.debian.org/archive/debian/{snapshot}/ trixie main\n"
        f"deb {debian} https://snapshot.debian.org/archive/debian/{snapshot}/ trixie-updates main\n"
        f"deb {debian} https://snapshot.debian.org/archive/debian-security/{snapshot}/ trixie-security main\n"
        "deb [arch=amd64 signed-by=/work/docker.gpg] https://download.docker.com/linux/debian trixie stable\n"
    )


def docker_preferences(pins: str) -> str:
    """Docker's repository gives nothing but the versions pins.env names (OS_PACKAGE_PINS, name=version)."""
    lines = ["Package: *", "Pin: origin download.docker.com", "Pin-Priority: -1", ""]
    for pin in pins.split():
        name, _, version = pin.partition("=")
        lines += [f"Package: {name}", f"Pin: version {version}", "Pin-Priority: 990", ""]
    return "\n".join(lines)


def simulated(text: str) -> list[dict]:
    """[{name, from, to}] of apt-get's simulation, in its order; `from` is None for a package new to the machine."""
    return [{"name": m[1], "from": m[2], "to": m[3]} for line in text.splitlines() if (m := INST.match(line))]


def packages_index(text: str) -> dict[tuple[str, str], str]:
    """{(name, version): file name} of an apt-ftparchive Packages file."""
    return {(f["Package"], f["Version"]): f["Filename"].removeprefix("./") for f in stanzas(text)}


def _in_tools(work: Path, pins: dict[str, str], script: str) -> None:
    """Run `script` in the tools container (deploy/iso/tools.Dockerfile on the pinned TOOLS_BASE) with `work` as /work."""
    with open(TOOLS_DOCKERFILE, "rb") as dockerfile:
        built = subprocess.run(["docker", "build", "-q", "-t", TOOLS_IMAGE, "--build-arg", f"TOOLS_BASE={pins['TOOLS_BASE']}", "-"],
                               stdin=dockerfile, capture_output=True, text=True, check=False)  # fmt: skip
    if built.returncode:
        sys.exit(f"the tools container could not be built: {built.stderr.strip()[-300:]}")
    done = _docker("run", "--rm", "-e", f"DOCKER_KEY_FPR={pins['DOCKER_KEY_FPR']}", "-e", f"APT={APT}", "-v", f"{work}:/work",
                   TOOLS_IMAGE, "bash", "-euo", "pipefail", "-c", f"{script}\nchown -R {os.getuid()}:{os.getgid()} /work")  # fmt: skip
    if done.returncode:
        sys.exit("the Debian updates could not be resolved (see the output above)")


def core_lists(core: str) -> list[Path]:
    """The package lists of a core: `<core>.installed` (a BIOS install) and `<core>.<kind>.installed` for an install
    that differs (`uefi`: GRUB for EFI instead of grub-pc). Each machine has the packages of one of them."""
    return sorted(CORES.glob(f"{core}.installed")) + sorted(CORES.glob(f"{core}.*.installed"))


def _resolve_list(listed: Path, snapshot: str, work: Path, pins: dict[str, str]) -> list[dict]:
    """[{name, from, to, file}] that apt upgrades on a machine with exactly the packages of `listed`; the .debs
    and their Packages index in work/os."""
    installed = listed.read_text(encoding="utf-8")
    (work / "os").mkdir(parents=True, exist_ok=True)
    (work / "status").write_text(dpkg_status(installed)[0])
    (work / "sources.list").write_text(os_sources(snapshot))
    (work / "preferences").write_text(docker_preferences(pins["OS_PACKAGE_PINS"]))
    _in_tools(work, pins, PREPARE)
    status, missing = dpkg_status(installed, (work / "available").read_text(encoding="utf-8", errors="replace"))
    if missing:
        print(f"  note: {missing} packages of {listed.name} are not in the archive at {snapshot}; apt knows only their versions")
    (work / "status").write_text(status)
    _in_tools(work, pins, RESOLVE)
    planned = simulated((work / "simulate.txt").read_text())
    index = packages_index((work / "os" / "Packages").read_text()) if planned else {}
    debs = {path.name for path in (work / "os").glob("*.deb")}
    packages = []
    for item in planned:
        name = index.get((item["name"], item["to"]))
        if name is None or name not in debs:
            sys.exit(f"apt would install {item['name']} {item['to']} ({listed.name}), and its file was not downloaded")
        packages.append({**item, "file": f"os/{name}"})
    if len(debs) != len(packages):
        sys.exit(f"{len(debs)} files were downloaded for {len(packages)} packages ({listed.name})")
    return packages


def os_part(core: str, snapshot: str, work: Path) -> tuple[dict | None, dict[str, Path]]:
    """The manifest's `os` and the payload's files under os/, for machines installed from `core`: every update of
    each of its package lists, once. (None, {}) when Debian has nothing newer than the core's own packages."""
    if not SNAPSHOT.fullmatch(snapshot):
        sys.exit(f"--os-snapshot is a time of Debian's snapshot archive, YYYYMMDDTHHMMSSZ, not {snapshot!r}")
    lists = core_lists(core)
    if not lists:
        sys.exit(f"deploy/iso/cores/{core}.installed is missing: the packages a fresh install of {core} has "
                 "(deploy/iso/AGENTS.md says how it is made)")  # fmt: skip
    pins = read_pins()
    packages, parts, index = [], {}, []
    for number, listed in enumerate(lists):
        folder = work / f"list-{number}"
        for item in _resolve_list(listed, snapshot, folder, pins):
            if item["file"] not in parts:
                packages.append(item)
                parts[item["file"]] = folder / item["file"]
                index += [s for s in (folder / "os" / "Packages").read_text().split("\n\n")
                          if f"Filename: ./{item['file'].removeprefix('os/')}\n" in s + "\n"]  # fmt: skip
    if not packages:
        return None, {}
    (work / "Packages").write_text("\n\n".join(stanza.strip("\n") for stanza in index) + "\n")
    part = {"snapshot": snapshot, "packages": packages, "reboot": any(REBOOT.fullmatch(p["name"]) for p in packages)}
    return part, {"os/Packages": work / "Packages", **parts}


def os_summary(part: dict | None, parts: dict[str, Path]) -> str:
    if part is None:
        return "nothing newer than the core"
    size = sum(path.stat().st_size for path in parts.values())
    kernel = [f"{p['name']} {p['to']}" for p in part["packages"] if p["name"].startswith("linux-image-") and p["name"] != "linux-image-amd64"]
    return (f"{len(part['packages'])} packages, {size / (1 << 20):.1f} MB (snapshot {part['snapshot']})"
            + (f", kernel {', '.join(kernel)}" if kernel else ", the kernel unchanged")
            + (", restart of the appliance" if part["reboot"] else ""))  # fmt: skip


# --- the package ---------------------------------------------------------------


def _add(tar: tarfile.TarFile, name: str, data: bytes | Path) -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mode, info.mtime = 0o644, int(time.time())
    if isinstance(data, bytes):
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    else:
        info.size = data.stat().st_size
        with open(data, "rb") as handle:
            tar.addfile(info, handle)


def write_package(
    out: Path,
    manifest: dict,
    parts: dict[str, Path],
    private_pem: bytes,
    passphrase: bytes | None,
    recipients: list[str],
    work: Path,
) -> dict:
    """Pack `parts` ({name in the payload: file}) into the payload, encrypt it to `recipients`, sign the
    manifest (which gets the payload's size and checksum) and write the package. Returns the manifest."""
    if not recipients:
        sys.exit("no recipient: the package would open on no appliance")
    payload = work / "payload.tar"
    # PAX, because an image can pass the 8 GB a ustar header can record.
    with tarfile.open(payload, "w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(parts):
            _add(tar, name, parts[name])
    manifest = {**manifest, "payload": {"size": payload.stat().st_size, "sha256": _sha256(payload)}}
    sealed = work / "payload.age"
    sealed.unlink(missing_ok=True)
    argv = ["age", "-o", str(sealed)]
    for recipient in recipients:
        argv += ["-r", recipient]
    try:
        done = subprocess.run([*argv, str(payload)], capture_output=True, text=True, check=False)
    except OSError:
        sys.exit("the program `age` is needed to encrypt the package and is not on this machine")
    if done.returncode:
        sys.exit(f"age: {done.stderr.strip()[-300:]}")
    body = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    signature = sign(body, private_pem, passphrase)
    partial = out.with_name(out.name + ".partial")
    with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as tar:
        _add(tar, "manifest.json", body)
        _add(tar, "manifest.sig", signature)
        _add(tar, "payload.age", sealed)
    partial.replace(out)
    return manifest


def read_recipients(path: Path) -> list[str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    keys = [line for line in lines if line and not line.startswith("#")]
    for key in keys:
        if not key.startswith("age1") or len(key) != 62:
            sys.exit(f"{path}: not an update key: {key[:20]}")
    return keys


def _version_key(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(\.\d{1,2})?", version.removesuffix("-uat")):
        sys.exit(f"not a version: {version!r} (YYYY.MM.DD, a second one the same day .2; -uat for a UAT image)")
    return tuple(int(part) for part in version.removesuffix("-uat").split("."))


def min_from_of(base: str, version: str, min_from: str | None, first: bool) -> str:
    """The package's min_from: the package before it, so that machines take packages strictly in order.

    The first package of a core (`first`) has the core. A bridge (base == version: a new core as a package) has
    the last package of the old core, which must be named. Any other package must name the one before it."""
    bridge = base == version
    if first:
        if bridge:
            sys.exit("a bridge is not the first package of a core: give --min-from, the last package of the old core")
        if min_from:
            sys.exit("--first means min_from is the core (--base); leave --min-from out")
        min_from = base
    elif not min_from:
        sys.exit("--min-from is needed: the package before this one (for a bridge, the last package of the old core). "
                 "The first package of a core says --first instead.")  # fmt: skip
    if len({name.endswith("-uat") for name in (base, version, min_from)}) != 1:
        sys.exit("--base, --version and --min-from all end in -uat, or none does (ADR 0085)")
    if not _version_key(min_from) < _version_key(version):
        sys.exit(f"--min-from {min_from} must be older than --version {version}")
    if not bridge and _version_key(min_from) < _version_key(base):
        sys.exit(f"--min-from {min_from} is older than the core {base} the package is built on")
    return min_from


def refuse_stamp(stamp: dict, version: str) -> None:
    """Exit unless an image with this build stamp may be packaged as `version`.

    A release package is an Enterprise image (`licence`: enterprise). A UAT image is only as a -uat version,
    which only a UAT machine takes (ADR 0085), and a -uat version only from a UAT image. A Community image
    (no such line, or the old open lab stamp) is never packaged.
    """
    licence = stamp.get("licence")
    if licence == "enterprise" and not version.endswith("-uat"):
        return
    if licence == "uat" and version.endswith("-uat"):
        return
    sys.exit(f"rezztor/eagent:{version} is not an Enterprise image, or a UAT build and version that disagree (ADR 0085). "
             "A release package is an Enterprise image. A Community image is never packaged. "
             "A UAT image is packaged only as a version that ends in -uat.")


def build_package(args: argparse.Namespace, private_pem: bytes, passphrase: bytes | None) -> Path:
    base, version = args.base, args.version
    full = base == version
    # A bridge is compared with what the machines run when they take it, the last package of the old core:
    # that decides the host files, the database and new release keys. Its images travel whole anyway.
    since = args.min_from if full else base
    old, new = tree_of(f"rezztor/eagent:{since}"), tree_of(f"rezztor/eagent:{version}")
    if BUILD_INPUTS not in new:
        sys.exit(
            f"rezztor/eagent:{version} has no {BUILD_INPUTS}: build it with scripts/build_local.sh app. Without "
            "the list, a change of its dependencies or of another image's sources would go into no package."
        )
    with tempfile.TemporaryDirectory(prefix="rezztor-update-") as tmp:
        work = Path(tmp)
        with open(work / "build_info.json", "wb") as handle:
            _from_image(f"rezztor/eagent:{version}", "cat /eagent/build_info.json", handle)
        stamp = json.loads((work / "build_info.json").read_text())
        refuse_stamp(stamp, version)
        stamped = stamp.get("version")
        if stamped != version:
            # The host waits for /api/health to answer with the new version, and this is where it comes from.
            sys.exit(
                f"rezztor/eagent:{version} says it is version {stamped!r}. Build it with BUILD_VERSION={version} "
                "(scripts/build_local.sh app), tag it, and run this again."
            )
        changed = changed_paths(old, new)
        shape = classify(changed, {path for path in changed if path not in old}, tuple(args.force_image or ()))
        if full:
            shape["images"] = {name: "image" for name in IMAGES}
            shape["restart"] = "application"
        held = []
        if args.format == 3:
            # For machines whose tool is older than ADR 0092: what it cannot take waits for the next package,
            # which carries it, as every package carries everything since its core.
            held = [name for name in shape["host"] if name not in FORMAT_3_HOST and not name.startswith("keys/")]
            shape["host"] = [name for name in shape["host"] if name not in held]
        parts, images = {}, {}
        for name, mode in shape["images"].items():
            if mode == "files":
                target = work / "eagent.tar.xz"
                pack_tree(f"rezztor/eagent:{version}", target)
                images[name] = {"mode": "files", "file": "app/eagent.tar.xz"}
            else:
                target = work / f"{name}.tar.gz"
                with gzip.open(target, "wb", compresslevel=6) as handle:
                    save = subprocess.Popen(["docker", "save", f"rezztor/{name}:{version}"], stdout=subprocess.PIPE)
                    shutil.copyfileobj(save.stdout, handle, 1 << 20)
                    if save.wait():
                        sys.exit(f"docker save rezztor/{name}:{version} failed: is the image on this machine?")
                images[name] = {"mode": "image", "file": f"images/{name}.tar.gz"}
            parts[images[name]["file"]] = target
        sources = {known: source for source, known in HOST.items()}
        for known in shape["host"]:
            sources.setdefault(known, f"deploy/iso/{known}")
            target = work / known.replace("/", "_")
            with open(target, "wb") as handle:
                _from_image(f"rezztor/eagent:{version}", f"cat /eagent/{sources[known]}", handle)
            parts[known] = target
        system, system_parts = (None, {})
        if args.os_snapshot:
            system, system_parts = os_part(args.os_core or base, args.os_snapshot, work / "debian")
            parts.update(system_parts)
        modern = system is not None or any(name not in FORMAT_3_HOST and not name.startswith("keys/") for name in shape["host"])
        manifest = {
            "format": FORMAT if modern or args.format == 4 else 3,
            "product": PRODUCT,
            "version": version,
            "base": base,
            "min_from": args.min_from,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "restart": shape["restart"],
            "database_change": shape["database_change"],
            "notes": list(args.note or []),
            "images": images,
            "host": shape["host"],
        }
        if args.start_minutes:
            manifest["start_minutes"] = args.start_minutes
        if system:
            manifest["os"] = system
        debian = os_summary(system, system_parts) if args.os_snapshot else None
        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        package = out / f"{PRODUCT}-update-{version}.rezupd"
        manifest = write_package(package, manifest, parts, private_pem, passphrase, read_recipients(Path(args.recipients).expanduser()), work)
    Path(f"{package}.sha256").write_text(f"{_sha256(package)}  {package.name}\n")
    print(f"{package}  ({package.stat().st_size / (1 << 20):.1f} MB)")
    print(f"  {len(changed)} files differ from {since}{'  (a bridge: every image whole)' if full else ''}")
    for name in IMAGES:
        print(f"  {name:15} {images[name]['mode'] if name in images else 'as in ' + base}")
    print(f"  host files      {', '.join(manifest['host']) or 'none'}")
    if held:
        print(f"  held back       {', '.join(held)} (--format 3: the next package carries them)")
    if debian:
        print(f"  debian          {debian}")
    print(f"  database        {'changes' if manifest['database_change'] else 'no change'}")
    print(f"  restart         {manifest['restart']}")
    print(f"  format          {manifest['format']}{'' if manifest['format'] == FORMAT else ' (a tool from before ADR 0092 takes it too)'}")
    print(Path(f"{package}.sha256").read_text().strip())
    return package


# --- the command line ----------------------------------------------------------


def _new_passphrase() -> bytes:
    first = getpass.getpass("Passphrase for the new release key (at least 15 characters): ")
    if len(first) < 15:
        sys.exit("the passphrase is shorter than 15 characters")
    if getpass.getpass("Again: ") != first:
        sys.exit("the two entries are not the same")
    return first.encode()


def cmd_keypair(args: argparse.Namespace) -> int:
    private_path = _refuse_inside_repo(Path(args.private_out), "the private key")
    if private_path.exists() and not args.force:
        sys.exit(f"{private_path} already exists; pass --force to overwrite it (and lose the old key)")
    private_pem, public_pem = generate_keypair(_new_passphrase())
    private_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path.touch(mode=0o600)
    private_path.write_text(private_pem)
    private_path.chmod(0o600)
    if args.public_out:
        public_path = Path(args.public_out)
        public_path.parent.mkdir(parents=True, exist_ok=True)
        public_path.write_text(public_pem)
        print(f"public key written to {public_path}")
    else:
        print(public_pem)
    print(f"private key written to {private_path} (mode 0600, encrypted with the passphrase)")
    print("Back the private key up offline now, and keep the passphrase somewhere else. Without")
    print("both, no appliance that trusts only this public key can ever be updated again.")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    key = Path(args.key).expanduser()
    if not key.is_file():
        sys.exit(f"no release key at {key}; see `{Path(__file__).name} keypair --help`")
    args.min_from = min_from_of(args.base, args.version, args.min_from, args.first)
    os_choices(args)
    private_pem = key.read_bytes()
    passphrase = None
    if b"ENCRYPTED" in private_pem:
        passphrase = getpass.getpass("Passphrase of the release key: ").encode()
    sign(b"", private_pem, passphrase)  # a wrong passphrase stops here, before the images are read
    build_package(args, private_pem, passphrase)
    return 0


def os_choices(args: argparse.Namespace) -> None:
    """Exit when the Debian part asked for cannot be made. A bridge's machines run the old core's Debian, so a
    bridge names that core; a package for a tool older than ADR 0092 (--format 3) carries no Debian part."""
    if args.format not in (None, 3, 4):
        sys.exit("--format (or REZZTOR_PACKAGE_FORMAT) is 3 or 4")
    if args.os_snapshot and args.format == 3:
        sys.exit("--format 3 is for machines whose update tool takes no Debian updates: leave --os-snapshot out")
    if args.os_snapshot and args.base == args.version and not args.os_core:
        sys.exit("a bridge's machines run the old core's Debian: name it with --os-core")
    if args.os_core and (not args.os_snapshot or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(-uat)?", args.os_core)):
        sys.exit("--os-core is a core version (YYYY.MM.DD, -uat for UAT), and goes with --os-snapshot")


def cmd_os_updates(args: argparse.Namespace) -> int:
    """What a package built now would carry of Debian for machines installed from `--core`. Signs nothing."""
    with tempfile.TemporaryDirectory(prefix="rezztor-debian-") as tmp:
        part, parts = os_part(args.core, args.snapshot, Path(tmp))
        print(f"core {args.core}, snapshot {args.snapshot}: {os_summary(part, parts)}")
        for item in (part or {}).get("packages", []):
            print(f"  {item['name']:40} {item['from'] or '(new)':32} -> {item['to']}")
        if args.keep and part:
            keep = Path(args.keep).expanduser()
            keep.mkdir(parents=True, exist_ok=True)
            for name, path in parts.items():
                shutil.copyfile(path, keep / Path(name).name)
            print(f"files kept in {keep}")
    return 0


PURPOSES = {"support": b"rezztor-support-1", "recover": b"rezztor-recover-1"}


def support_code(challenge: str, private_pem: bytes, passphrase: bytes | None, hours: float, now: float | None = None,
                 purpose: str = "support") -> str:  # fmt: skip
    """The code that opens a root shell on the one machine showing `challenge` (ADR 0083), or with
    purpose `recover` lets its console password be set again. The writer of what
    deploy/iso/host/rezztor-support reads: 4 bytes of expiry and the signature over
    `rezztor-support-1|<challenge>|<expiry>` (or `rezztor-recover-1|...`), in base32, in groups of five for
    the person typing it."""
    expires = int((time.time() if now is None else now) + hours * 3600)
    body = PURPOSES[purpose] + b"|" + challenge.encode() + b"|" + str(expires).encode()
    raw = expires.to_bytes(4, "big") + base64.b64decode(sign(body, private_pem, passphrase))
    text = base64.b32encode(raw).decode().rstrip("=")
    return " ".join(text[at : at + 5] for at in range(0, len(text), 5))


def cmd_support_code(args: argparse.Namespace) -> int:
    import re

    challenge = args.challenge.strip().upper()
    if not re.fullmatch(r"RZ-[0-9A-F]{8}-[A-Z2-7]{8}", challenge):
        sys.exit("a challenge reads RZ-, eight characters, a dash and eight more; type it as the console shows it")
    if not 0 < args.hours <= 168:
        sys.exit("a code is good for at most 168 hours")
    key = Path(args.key).expanduser()
    if not key.is_file():
        sys.exit(f"no support key at {key}; make one with `{Path(__file__).name} keypair`")
    private_pem = key.read_bytes()
    passphrase = getpass.getpass("Passphrase of the support key: ").encode() if b"ENCRYPTED" in private_pem else None
    print(f"Challenge  {challenge}")
    print(f"Good for   {args.hours:g} hours from now, once")
    print(f"Code       {support_code(challenge, private_pem, passphrase, args.hours, purpose=args.purpose)}")
    if args.purpose == "recover":
        print("It sets a new console password on that machine, and does nothing else. Give it to whoever is at its console.")
    else:
        print("Whoever types it is root on that machine until the shell is left. Give it for a session you are in.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    kp = sub.add_parser("keypair", help="generate an Ed25519 release keypair")
    kp.add_argument("--private-out", required=True, help="path for the private key (outside the repository)")
    kp.add_argument("--public-out", help="path for the public key, deploy/iso/keys/<name>.pub (default: stdout)")
    kp.add_argument("--force", action="store_true", help="overwrite an existing private key")
    kp.set_defaults(func=cmd_keypair)

    package = sub.add_parser("package", help="make an update package from two local images")
    package.add_argument("--base", required=True, help="the core the package is built on, YYYY.MM.DD; for a bridge, "
                         "the new core itself (= --version)")  # fmt: skip
    package.add_argument("--version", required=True, help="the version after the update, YYYY.MM.DD (-uat added for a UAT image)")
    package.add_argument("--key", required=True, help="the release private key")
    package.add_argument("--recipients", required=True, help="a file of update keys (age1...), one a line")
    package.add_argument("--out", required=True, help="the folder to write the package into")
    package.add_argument("--note", action="append", help="one line for the administrator; may be given several times")
    package.add_argument("--min-from", help="the package before this one, the oldest version that may take it; for a "
                         "bridge, the last package of the old core")  # fmt: skip
    package.add_argument("--first", action="store_true", help="the first package of a core: min_from is the core")
    package.add_argument("--start-minutes", type=int, choices=range(1, 61), metavar="1..60",
                         help="how long the new version may take to answer before the old one comes back (default: 10)")  # fmt: skip
    package.add_argument("--force-image", action="append", choices=IMAGES,
                         help="carry this image whole even when its inputs did not change")  # fmt: skip
    package.add_argument("--os-snapshot", metavar="YYYYMMDDTHHMMSSZ", default=os.environ.get("REZZTOR_OS_SNAPSHOT") or None,
                         help="carry every Debian update since the core, as Debian's snapshot archive had them at this "
                         "time (ADR 0092); the same time gives the same packages (default: $REZZTOR_OS_SNAPSHOT)")  # fmt: skip
    package.add_argument("--os-core", help="the core whose installed packages the Debian updates are reckoned from "
                         "(default --base; a bridge names the old core)")  # fmt: skip
    package.add_argument("--format", type=int, choices=(3, 4), default=int(os.environ.get("REZZTOR_PACKAGE_FORMAT") or 0) or None,
                         help="3: for machines whose update tool is older than ADR 0092 (no Debian part; the guard and "
                         "units wait for the next package). Default: $REZZTOR_PACKAGE_FORMAT, else 4 when the package needs it, else 3")  # fmt: skip
    package.set_defaults(func=cmd_package)

    debian = sub.add_parser("os-updates", help="say which Debian updates a package would carry now; signs nothing")
    debian.add_argument("--core", required=True, help="the core, as named in deploy/iso/cores/<core>.installed")
    debian.add_argument("--snapshot", required=True, metavar="YYYYMMDDTHHMMSSZ", help="the time of Debian's snapshot archive")
    debian.add_argument("--keep", help="copy the .debs and their Packages index into this folder")
    debian.set_defaults(func=cmd_os_updates)

    support = sub.add_parser("support-code", help="make the code that opens a root shell on one closed appliance")
    support.add_argument("--key", required=True, help="the support private key (made with `keypair`; its public half "
                         "goes to deploy/iso/keys/support/<name>.pub)")  # fmt: skip
    support.add_argument("--challenge", required=True, help="the challenge the appliance's console shows, RZ-...")
    support.add_argument("--hours", type=float, default=8, help="how long the code is good for (default 8, at most 168)")
    support.add_argument("--purpose", choices=tuple(PURPOSES), default="support",
                         help="support: a root shell from the menu (default); recover: a new console password, at the "
                         "login rezrecover")  # fmt: skip
    support.set_defaults(func=cmd_support_code)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

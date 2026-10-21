#!/usr/bin/env bash
# Check the payload of a finished ISO without a virtual machine.
#
#   deploy/iso/test/check-iso-payload.sh <iso>
#
# Reads /rezztor back out of the ISO (so files over 4 GB are proven readable),
# checks every file against both checksum lists, then opens every image
# tarball and checks each layer and manifest inside it against its own
# SHA-256 name. An image that passes loads with `docker load`. Prints the
# image names each tarball restores. Needs the tools container that
# scripts/build_iso.sh makes, python3, and room for one copy of the payload.

set -euo pipefail
ISO="$(realpath "${1:?usage: check-iso-payload.sh <iso>}")"
X="${REZZTOR_ISO_WORK:-$HOME/iso-build}/work/check"
rm -rf "$X" && mkdir -p "$X"
trap 'rm -rf "$X"' EXIT

docker run --rm -v "$ISO:/iso:ro" -v "$X:/x" rezztor-iso-tools:local bash -c \
    "xorriso -indev /iso -osirrox on -extract /rezztor /x/rezztor > /dev/null 2>&1; chown -R $(id -u):$(id -g) /x; chmod -R u+rwX /x"
cd "$X/rezztor"
sha256sum -c --quiet SHA256SUMS
(cd bundle && sha256sum -c --quiet SHA256SUMS)
echo "every file on the ISO matches SHA256SUMS ($(wc -l < SHA256SUMS) entries) and bundle/SHA256SUMS ($(wc -l < bundle/SHA256SUMS) entries)"

python3 - bundle/images/*.tar.gz <<'PY'
import hashlib, json, os, sys, tarfile

for path in sys.argv[1:]:
    blobs, index = 0, {}
    with tarfile.open(path, "r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            data = tar.extractfile(member)
            if member.name.startswith("blobs/sha256/"):
                digest = hashlib.sha256()
                while chunk := data.read(1 << 20):
                    digest.update(chunk)
                if digest.hexdigest() != os.path.basename(member.name):
                    sys.exit(f"{path}: {member.name} is damaged")
                blobs += 1
            elif member.name == "index.json":
                index = json.load(data)
    names = [m.get("annotations", {}).get("io.containerd.image.name", "?") for m in index.get("manifests", [])]
    size = os.path.getsize(path) / 2**30
    print(f"{os.path.basename(path)}: {size:.2f} GB, {blobs} blobs intact, restores {', '.join(names)}")
PY

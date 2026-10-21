#!/usr/bin/env bash
# Make the one-time file for a machine that was installed before the update tool
# existed (ADR 0080). Rezztor's side, never shipped in an image.
#
#   scripts/build_update_bootstrap.sh OUT_FOLDER
#
# It writes rezztor-update-bootstrap-<date>.tar and its SHA-256. The tar holds
# what a new ISO installs by itself: the tool, its two units, the release keys
# in deploy/iso/keys, Debian's `age` package, and install.sh. The administrator
# copies it to the appliance (USB stick or SSH), compares the SHA-256 with the
# one Rezztor sent, and runs, once:
#
#   tar -xf rezztor-update-bootstrap-<date>.tar && sudo sh rezztor-update-bootstrap/install.sh
#
# After that the machine takes update packages; the first one brings the
# Updates page and the console menu entry.
#
# The age package is the one the ISO carries: it is taken from the ISO build's
# package folder (~/iso-build/cache/os, made by scripts/build_iso.sh) and must
# match deploy/iso/os-packages.sha256.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

OUT=${1:?usage: build_update_bootstrap.sh OUT_FOLDER}
WORK="${REZZTOR_ISO_WORK:-$HOME/iso-build}"
NAME=rezztor-update-bootstrap

# REZZTOR_UPDATE_KEYS: another folder of *.pub files, for the test in deploy/iso/test/test-update.sh,
# which makes a key of its own. A file for a customer is built without it.
KEYS="${REZZTOR_UPDATE_KEYS:-deploy/iso/keys}"

shopt -s nullglob
keys=("$KEYS"/*.pub)
(( ${#keys[@]} )) || { echo "error: no release key in $KEYS (see deploy/iso/keys/README.md)" >&2; exit 1; }
debs=("$WORK"/cache/os/age_*.deb)
(( ${#debs[@]} == 1 )) || { echo "error: expected one age package in $WORK/cache/os; run scripts/build_iso.sh first" >&2; exit 1; }
grep -qxF "$(sha256sum < "${debs[0]}" | cut -d' ' -f1)  $(basename "${debs[0]}")" deploy/iso/os-packages.sha256 \
    || { echo "error: $(basename "${debs[0]}") is not the package deploy/iso/os-packages.sha256 names" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$NAME"/{host,keys,os}
cp deploy/iso/host/rezztor-update deploy/iso/host/rezztor-update.path deploy/iso/host/rezztor-update.service \
    deploy/iso/host/install-update-tool.sh "$stage/$NAME/host/"
cp "${keys[@]}" "$stage/$NAME/keys/"
cp "${debs[0]}" "$stage/$NAME/os/"
cat > "$stage/$NAME/install.sh" <<'INSTALL'
#!/bin/sh
# Run once, as root, on a machine installed before the update tool existed.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Run this with sudo." >&2; exit 1; }
[ -d /var/lib/rezztor/stack ] || { echo "This is not a machine installed from the Rezztor EAgent ISO." >&2; exit 1; }
sha256sum -c --quiet SHA256SUMS
sh host/install-update-tool.sh "$PWD"
systemctl daemon-reload
systemctl start rezztor-update.path
/usr/local/sbin/rezztor-update status
echo
echo "The update tool is installed. Send the update key above to Rezztor."
echo "To apply an update file: sudo rezztor-update"
INSTALL
(cd "$stage/$NAME" && find . -type f ! -name SHA256SUMS -printf '%P\0' | sort -z | xargs -0 sha256sum > SHA256SUMS)

mkdir -p "$OUT"
tar_file="$OUT/$NAME-$(date -u +%Y%m%d).tar"
tar -C "$stage" --owner=0 --group=0 --numeric-owner -cf "$tar_file" "$NAME"
(cd "$OUT" && sha256sum "$(basename "$tar_file")" > "$(basename "$tar_file").sha256")
echo "$tar_file"
echo "  release keys: $(basename -a "${keys[@]}" | tr '\n' ' ')"
cat "$tar_file.sha256"

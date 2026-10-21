#!/bin/sh
# Put the update tool on this machine (ADR 0080): the tool, its two units, the
# release keys it trusts, and `age` when Debian's package is not installed yet.
#
#   sh install-update-tool.sh FOLDER
#
# FOLDER holds host/ (the tool and the units), keys/ (*.pub) and os/ (the age
# package). install-target.sh runs this on a new machine, with the ISO's
# payload as the folder; the bootstrap file made by
# scripts/build_update_bootstrap.sh runs it once on a machine installed before
# the tool existed. Run it again and nothing changes.
#
# A release key is only ever added. One that is already here under the same
# name and differs stops the script: a key is never replaced or removed.

set -eu
P=${1:?usage: install-update-tool.sh FOLDER}

for key in "$P"/keys/*.pub; do
    [ -f "$key" ] || continue
    have=/opt/rezztor/keys/$(basename "$key")
    if [ -f "$have" ] && ! cmp -s "$key" "$have"; then
        echo "error: $have is already on this machine and is a different key. Nothing was changed." >&2
        exit 1
    fi
done

if ! command -v age > /dev/null 2>&1; then
    dpkg -i "$P"/os/age_*.deb
fi
install -m 0755 "$P/host/rezztor-update" /usr/local/sbin/
install -m 0644 "$P/host/rezztor-update.path" "$P/host/rezztor-update.service" /etc/systemd/system/
install -d -m 0755 /opt/rezztor/keys
for key in "$P"/keys/*.pub; do
    [ -f "$key" ] && install -m 0644 "$key" /opt/rezztor/keys/
done
# The application's container mounts state (to read) and inbox (to write);
# work is this tool's own. The application is uid 1000, not root, so the inbox
# is root:1000 0770 (the container's start makes an older one so too).
install -d -m 0755 /var/lib/rezztor/updates /var/lib/rezztor/updates/state
install -d -m 0700 /var/lib/rezztor/updates/work
install -d -m 0770 -o root -g 1000 /var/lib/rezztor/updates/inbox
systemctl enable rezztor-update.path rezztor-update.service

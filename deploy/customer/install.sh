#!/bin/sh
# Bootstrap for the Rezztor EAgent installer.
#
#   sh install.sh          (from the delivery bundle, with installer.sh beside it)
#
# This file exists because the real installer cannot be piped into a shell,
# and neither can any installer that asks a question. Two reasons, both fatal
# and both silent:
#
#   When a script is piped, stdin *is* the script. A `read` therefore consumes
#   the next line of the script rather than the user's answer -- so the prompt
#   returns a line of shell source, and execution resumes past it. Nothing
#   errors. Demonstrated before this file was written: the line after `read`
#   simply never ran.
#
#   `| sh` is not `| bash`. On Debian and Ubuntu /bin/sh is dash, which does
#   not have arrays, `[[ ]]`, or `printf -v`. The installer is bash and uses
#   all three.
#
# So this stays POSIX sh, asks nothing, and does one job: fetch the real
# installer, check it against the hash below, and run it under bash with a
# terminal attached. Verified rather than trusted -- which also means the one
# checksum Rezztor emails for this file covers the installer as well, and the
# installer in turn covers the Compose file.

set -eu

# No default host. The installer travels with this file in the delivery
# bundle, and how the product reaches a customer is deliberately undecided --
# so there is nothing to default to, and a URL baked in here would be a
# promise about an address nobody has chosen yet. Set REZZTOR_INSTALL_URL to
# fetch from somewhere instead.
BASE="${REZZTOR_INSTALL_URL:-}"
INSTALLER_SHA256="__INSTALLER_SHA__"

die() { printf '\nerror: %s\n\n' "$1" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required."
command -v bash >/dev/null 2>&1 || die "bash is required. Install it, then run this again."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required (coreutils)."

work="$(mktemp -d)" || die "Could not create a temporary directory."

# An offline bundle has the installer beside this file; use it rather than
# reaching for a network that may not be there.
if [ -f ./installer.sh ]; then
    cp ./installer.sh "$work/installer.sh"
elif [ -n "$BASE" ]; then
    curl -fsSL "$BASE/installer.sh" -o "$work/installer.sh" \
        || die "Could not download the installer from $BASE."
else
    die "installer.sh is not beside this script.
Run this from the directory Rezztor sent you, which contains both files,
or set REZZTOR_INSTALL_URL to a host serving them."
fi

if [ "$INSTALLER_SHA256" != "__INSTALLER_SHA__" ]; then
    actual="$(sha256sum "$work/installer.sh" | awk '{print $1}')"
    [ "$actual" = "$INSTALLER_SHA256" ] || die "The installer does not match the hash this bootstrap expects.
    expected $INSTALLER_SHA256
    got      $actual
Run it again. If it happens twice, stop and tell Rezztor."
fi

chmod +x "$work/installer.sh"

# The installer asks questions, so it needs a keyboard. When this script was
# piped, stdin is the pipe -- so hand it the terminal directly. Where there is
# no terminal at all (CI, a provisioning run) it goes ahead without one, and
# the installer refuses to invent secrets unless --unattended was passed.
if [ -t 0 ]; then
    exec bash "$work/installer.sh" "$@"
elif [ -e /dev/tty ] && (exec </dev/tty) 2>/dev/null; then
    exec bash "$work/installer.sh" "$@" </dev/tty
else
    exec bash "$work/installer.sh" "$@"
fi

#!/usr/bin/env bash
# Update a machine that test-iso.sh left running, and prove it. No person needed.
#
#   KEEP=1 deploy/iso/test/test-iso.sh <iso> bios      # the machine: SSH on, left running
#   deploy/iso/test/test-update.sh <version>           # e.g. 2026.10.10
#
# The machine was installed from an ISO that may not have the update tool, so
# this is the whole road of a machine already at a customer:
#   1. the one-time bootstrap file (scripts/build_update_bootstrap.sh), with a
#      release key made for this test; run twice, the second run changes nothing;
#   2. a file signed with a key the machine does not know is refused;
#   3. a package for <version>, made by scripts/sign_release.py from the images
#      rezztor/eagent:<base> and rezztor/eagent:<version> on this computer and
#      encrypted to the machine's own update key, is installed on the console;
#   4. the restart (skipped by the machine when only texts and screens are new
#      for it): /api/health answers with <version>, .env differs in one line,
#      every container runs, the guard's table is still loaded.
#
# Needs: what test-iso.sh needs, the program `age`, and the age package in the
# ISO build's package folder (scripts/build_iso.sh puts it there). The release
# key of this test is not a secret and stays in the work folder.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

VERSION="${1:?usage: test-update.sh <version>}"
W="${REZZTOR_ISO_WORK:-$HOME/iso-build}/test-bios"
U="$W/update"
PY="${PYTHON:-.venv/bin/python}"
P_HTTPS=19443; P_SSH=19022
URL="https://127.0.0.1:$P_HTTPS"
FAILED=0
[[ -x "$W/askpass.sh" ]] || { echo "no machine: run KEEP=1 deploy/iso/test/test-iso.sh <iso> bios first" >&2; exit 2; }
CONSOLE_PW="$("$W/askpass.sh")"

log()  { printf '[update %s] %s\n' "$(date +%T)" "$*"; }
pass() { log "PASS  $*"; }
fail() { log "FAIL  $*"; FAILED=1; }
ssh_vm() {
    SSH_ASKPASS="$W/askpass.sh" SSH_ASKPASS_REQUIRE=force setsid -w ssh -q -p "$P_SSH" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no rezadmin@127.0.0.1 "$@"
}
inside() { ssh_vm "echo $CONSOLE_PW | sudo -S -p '' sh -c '$1'" 2>&1; }
put() { ssh_vm "cat > '$2'" < "$1"; }

mkdir -p "$U/keys"
if [[ ! -f "$U/release-test.pem" ]]; then
    "$PY" -c 'import sys; sys.path.insert(0, "scripts"); import sign_release as s; a, b = s.generate_keypair(); open(sys.argv[1], "w").write(a); open(sys.argv[2], "w").write(b)' \
        "$U/release-test.pem" "$U/keys/release-test.pub"
    "$PY" -c 'import sys; sys.path.insert(0, "scripts"); import sign_release as s; open(sys.argv[1], "w").write(s.generate_keypair()[0])' "$U/stranger.pem"
fi

BASE="$(inside "sed -n s/^REZZTOR_VERSION=//p /var/lib/rezztor/stack/.env" | tr -d '\r\n')"
log "the machine runs $BASE; updating to $VERSION"

# 1. the bootstrap
rm -f "$U"/rezztor-update-bootstrap-*.tar*
REZZTOR_UPDATE_KEYS="$U/keys" scripts/build_update_bootstrap.sh "$U" > "$U/bootstrap.log"
BOOT="$(ls "$U"/rezztor-update-bootstrap-*.tar)"
put "$BOOT" /home/rezadmin/bootstrap.tar
inside "cd /home/rezadmin && rm -rf rezztor-update-bootstrap && tar -xf bootstrap.tar && sh rezztor-update-bootstrap/install.sh" > "$U/bootstrap-run.txt"
KEY="$(inside "rezztor-update key" | grep -o 'age1[0-9a-z]*' | head -1)"
if [[ ${#KEY} -eq 62 ]]; then pass "the bootstrap installed the tool; the machine's update key is $KEY"; else fail "no update key after the bootstrap (see $U/bootstrap-run.txt)"; exit 1; fi
before="$(inside "sha256sum /etc/rezztor/update/identity /opt/rezztor/keys/*.pub /usr/local/sbin/rezztor-update")"
inside "sh /home/rezadmin/rezztor-update-bootstrap/install.sh" > /dev/null
[[ "$(inside "sha256sum /etc/rezztor/update/identity /opt/rezztor/keys/*.pub /usr/local/sbin/rezztor-update")" == "$before" ]] \
    && pass "a second run of the bootstrap changed nothing (same key, same files)" || fail "a second run of the bootstrap changed a key or the tool"
units="$(inside "systemctl is-enabled rezztor-update.path rezztor-update.service; systemctl is-active rezztor-update.path" | tr '\r\n' '  ')"
[[ "$units" == "enabled enabled active "* ]] && pass "units: $units" || fail "units: $units"

# 2. a stranger's file
echo "$KEY" > "$U/recipients.txt"
rm -rf "$U/out" "$U/stranger"
"$PY" scripts/sign_release.py package --base "$BASE" --version "$VERSION" --key "$U/stranger.pem" --recipients "$U/recipients.txt" \
    --note "Signed with a key the machine does not know." --out "$U/stranger" > /dev/null
put "$U/stranger/rezztor-eagent-update-$VERSION.rezupd" /home/rezadmin/stranger.rezupd
refused="$(inside "rezztor-update check /home/rezadmin/stranger.rezupd; echo exit=\$?")"
if grep -q "its signature does not match" <<< "$refused" && grep -q "exit=1" <<< "$refused"; then
    pass "a file signed with another key is refused: its signature does not match"
else
    fail "a file signed with another key was not refused: $refused"
fi

# 3. the package, on the console
"$PY" scripts/sign_release.py package --base "$BASE" --version "$VERSION" --key "$U/release-test.pem" --recipients "$U/recipients.txt" \
    --note "The update tool, the Updates page, and the fixes since $BASE." --out "$U/out" | tee "$U/package.txt"
PKG="$U/out/rezztor-eagent-update-$VERSION.rezupd"
put "$PKG" /home/rezadmin/update.rezupd
env_before="$(inside "grep -v ^REZZTOR_VERSION= /var/lib/rezztor/stack/.env | sha256sum")"
START=$SECONDS
inside "rezztor-update install /home/rezadmin/update.rezupd" | tee "$U/install.txt"
log "installed beside the running version in $((SECONDS - START))s"
phase="$(inside "python3 -c \"import json; print(json.load(open(\\\"/var/lib/rezztor/updates/state/state.json\\\"))[\\\"phase\\\"])\"" | tr -d '\r\n')"
# 4. the restart, unless only texts and screens were new for this machine
RESTART=0
if [[ "$phase" == staged ]]; then
    pass "the update waits for a restart; the old version still answers: $(curl -ksf --max-time 5 "$URL/api/health" | head -c 60)"
    START=$SECONDS
    inside "rezztor-update restart" | tee "$U/restart.txt"
    RESTART=$((SECONDS - START))
elif grep -q "No restart was needed" "$U/install.txt"; then
    pass "only texts and screens were new for this machine: in force with no restart"
else
    fail "after install the phase is '$phase', not staged (see $U/install.txt)"
fi
health="$(curl -ksf --max-time 10 "$URL/api/health" || true)"
if grep -q "\"version\": \"$VERSION\"" <<< "$health"; then pass "/api/health answers with $VERSION ($( ((RESTART)) && echo "restart ${RESTART}s" || echo "no restart"))"; else fail "/api/health does not answer with $VERSION: ${health:0:200}"; fi
[[ "$(inside "grep -v ^REZZTOR_VERSION= /var/lib/rezztor/stack/.env | sha256sum")" == "$env_before" ]] \
    && [[ "$(inside "sed -n s/^REZZTOR_VERSION=//p /var/lib/rezztor/stack/.env" | tr -d '\r\n')" == "$VERSION" ]] \
    && pass ".env: REZZTOR_VERSION is $VERSION and every other line is as it was" || fail ".env changed in more than its version line"
down="$(inside "docker ps -a --format \"{{.Names}} {{.State}} {{.Image}}\"" | tr -d '\r' | grep -v ' running ' || true)"
[[ -z "$down" ]] && pass "every container runs: $(inside "docker ps --format {{.Image}}" | tr '\r\n' '  ')" || fail "not running: $down"
inside "nft list table inet rezztor_guard" | grep -q "table inet rezztor_guard" && pass "the guard's table is still loaded" || fail "the guard's table is gone"
code="$(curl -ks -o /dev/null -w '%{http_code}' --max-time 10 "$URL/login")"
[[ "$code" == 200 ]] && pass "the sign-in page loads: $code" || fail "the sign-in page: $code"
inside "rezztor-update status" | tee "$U/status.txt"

log "restart ${RESTART}s, package $(du -h "$PKG" | cut -f1)"
exit $FAILED

#!/usr/bin/env bash
# Install the ISO in QEMU/KVM and prove the product answers. No person needed.
#
#   deploy/iso/test/test-iso.sh <iso> uefi|bios
#
# Phase 1, install: boots the ISO's own boot loader with NO network card,
#   an empty 80 GB disk (a sparse raw file), and virtual hardware that
#   VMware also gives a guest (uefi: paravirtual SCSI; bios: SATA). QEMU's
#   model of the LSI Logic SAS controller was tried first for bios: Debian's
#   installer finds no disk on it in QEMU, which proves nothing about
#   VMware's own model of it. The uefi run installs on the paravirtual
#   controller and then starts the same disk on SATA, because the OVMF
#   firmware cannot start from a paravirtual SCSI disk (VMware's firmware
#   can). The erase confirmation is
#   answered by two kernel parameters typed into the boot loader by this
#   test; the ISO has no menu entry that erases a disk without asking.
# Phase 2, first boot: a network card (vmxnet3 or e1000, QEMU's own NAT) and
#   a small disk labelled REZZTOR-ANSWERS with answers.env. Waits for
#   https://<vm>/api/health, loads the sign-in page, signs in and asks whether
#   network automation and the cloud executor work with no token typed, checks that the
#   product's own port and (uefi run: SSH off) port 22 do not answer. The
#   bios run switches SSH on and uses it to look inside the machine.
#
# Needs qemu-system-x86_64, socat, mke2fs, sfdisk, curl, openssl, python3; for uefi the OVMF
# files. With /dev/kvm a run takes about half an hour with the real images.
# Without it QEMU emulates the processor, many times slower: build the ISO
# with REZZTOR_ISO_STANDIN=1 (tiny stand-in images) for such a machine.
# Environment: RAM (MiB, default 8192), CPUS (4), KEEP=1 to leave the machine
# running at the end, REINSTALL=1 to repeat phase 1, CLOSED=1 for an ISO built with a
# support key (the bios run then checks that SSH runs no command and, with
# SUPPORT_KEY=<private half>, opens a root shell with a signed code), SSH_ON=1 to switch SSH on in
# the uefi run too, REZZTOR_ISO_WORK (default ~/iso-build). Passwords are random per run and stay in the work folder.

set -euo pipefail

ISO="$(realpath "${1:?usage: test-iso.sh <iso> uefi|bios}")"
FW="${2:?usage: test-iso.sh <iso> uefi|bios}"
W="${REZZTOR_ISO_WORK:-$HOME/iso-build}/test-$FW"
RAM="${RAM:-8192}"
CPUS="${CPUS:-4}"
OVMF="${OVMF:-/usr/share/edk2/x64}"
TEST_ARGS="partman/confirm=true partman/confirm_nooverwrite=true"

case "$FW" in
# CTL and DISK_DEV: the disk while installing. SATA (each port of the machine's
# own controller holds one device) for every first boot.
uefi) BASE=18000; CTL=(-device pvscsi,id=scsi0); DISK_DEV="scsi-hd,bus=scsi0.0"; NIC=vmxnet3; SSH=off ;;
bios) BASE=19000; CTL=(); DISK_DEV="ide-hd,bus=ide.1"; NIC=e1000; SSH=on ;;
*) echo "the second argument is uefi or bios" >&2; exit 2 ;;
esac
# SSH_ON=1: the uefi run switches SSH on as well, to look inside (the packages a fresh UEFI install has, ADR 0092).
if [[ -n "${SSH_ON:-}" ]]; then SSH=on; fi
P_HTTPS=$((BASE + 443)); P_HTTP=$((BASE + 80)); P_SSH=$((BASE + 22)); P_APP=$((BASE + 50))

mkdir -p "$W/shots"
MON="$W/mon.sock"
FAILED=0
if [[ -w /dev/kvm ]]; then
    ACCEL=(-machine q35 -accel kvm -cpu host); LIMIT=3600; KEY_GAP=0.06
else
    ACCEL=(-machine q35 -accel tcg,thread=multi -cpu max); LIMIT=14400; KEY_GAP=0.2
fi

log()  { printf '[%s %s] %s\n' "$FW" "$(date +%T)" "$*"; }
pass() { log "PASS  $*"; }
fail() { log "FAIL  $*"; FAILED=1; }

mon() { printf '%s\n' "$@" | socat - "UNIX-CONNECT:$MON" > /dev/null; }

shot() {
    mon "screendump $W/shots/$1.ppm"
    sleep 1
    if command -v magick > /dev/null; then
        magick "$W/shots/$1.ppm" "$W/shots/$1.png" 2> /dev/null && rm -f "$W/shots/$1.ppm"
    fi
}

# Wait for a screen that shows something and has stopped changing: the boot
# menu, which has no countdown.
wait_for_menu() {
    local prev="" cur still=0
    while (( still < 5 )); do
        sleep 5
        mon "screendump $W/shots/.probe.ppm"
        sleep 1
        cur="$(sha256sum < "$W/shots/.probe.ppm")"
        # A screen of one colour (firmware not there yet) does not count.
        if [[ "$cur" == "$prev" && "$(tail -c +20 "$W/shots/.probe.ppm" | tr -d '\0' | head -c 1 | wc -c)" == 1 ]]; then
            still=$((still + 1))
        else
            still=0
        fi
        prev=$cur
    done
    rm -f "$W/shots/.probe.ppm"
}

# Type a string on the virtual keyboard (US layout).
type_text() {
    local text="$1" i c k
    for (( i = 0; i < ${#text}; i++ )); do
        c=${text:i:1}
        case "$c" in
        [a-z0-9]) k=$c ;;
        [A-Z]) k="shift-${c,,}" ;;
        ' ') k=spc ;; /) k=slash ;; =) k=equal ;; -) k=minus ;; _) k=shift-minus ;; .) k=dot ;;
        *) echo "type_text: no key for '$c'" >&2; exit 2 ;;
        esac
        echo "sendkey $k 40"
        sleep "$KEY_GAP"
    done | socat - "UNIX-CONNECT:$MON" > /dev/null
}

qemu() {
    local fw=()
    if [[ "$FW" == uefi ]]; then
        fw=(-drive "if=pflash,format=raw,readonly=on,file=$OVMF/OVMF_CODE.4m.fd"
            -drive "if=pflash,format=raw,file=$W/vars.fd")
    fi
    rm -f "$MON"
    qemu-system-x86_64 "${ACCEL[@]}" -smp "$CPUS" -m "$RAM" \
        -display none -vga std -monitor "unix:$MON,server,nowait" \
        "${CTL[@]}" "${fw[@]}" "$@" &
    QEMU_PID=$!
    while [[ ! -S "$MON" ]]; do
        if ! kill -0 "$QEMU_PID" 2> /dev/null; then fail "QEMU did not start (its message is above)"; exit 1; fi
        sleep 0.2
    done
}

# ---------------------------------------------------------------------------
# phase 1: install, with no network card
# ---------------------------------------------------------------------------

if [[ -n "${REINSTALL:-}" || ! -f "$W/installed" ]]; then
    rm -f "$W/installed" "$W/disk.raw"
    # Screenshots of an earlier run must never be read as this run's.
    rm -f "${W:?}/shots/"*.png "${W:?}/shots/"*.ppm
    # A sparse raw file, so the test needs no qemu-img: 80 GB to the guest,
    # only the written blocks on this machine.
    truncate -s 80G "$W/disk.raw"
    if [[ "$FW" == uefi ]]; then cp "$OVMF/OVMF_VARS.4m.fd" "$W/vars.fd"; fi
    log "install: booting $ISO with no network card"
    START=$SECONDS
    qemu -no-reboot -nic none \
        -drive "if=none,id=cd,media=cdrom,readonly=on,file=$ISO" -device ide-cd,drive=cd,bus=ide.0,bootindex=1 \
        -drive "if=none,id=d0,format=raw,discard=unmap,file=$W/disk.raw" \
        -device "$DISK_DEV,drive=d0,bootindex=2"
    wait_for_menu
    shot "$FW-01-boot-menu"
    if [[ "$FW" == bios ]]; then
        # isolinux: Tab opens the chosen entry's command line for editing.
        mon "sendkey tab"; sleep 2
        type_text " $TEST_ARGS"
        shot "$FW-01-boot-menu-test-parameters"
        mon "sendkey ret"
    else
        # GRUB: "e" opens the chosen entry in the editor. Its first line is
        # "setparams ...", then an empty one, then the "linux" line; the two
        # test parameters go at the end of that, and Ctrl-X boots.
        mon "sendkey e"; sleep 3
        mon "sendkey down"; sleep 1; mon "sendkey down"; sleep 1; mon "sendkey end"; sleep 1
        type_text " $TEST_ARGS"
        shot "$FW-01-boot-menu-test-parameters"
        mon "sendkey ctrl-x"
    fi
    n=0
    while kill -0 "$QEMU_PID" 2> /dev/null; do
        sleep 30
        n=$((n + 1))
        if kill -0 "$QEMU_PID" 2> /dev/null; then shot "$FW-02-install-$(printf '%02d' "$n")" || true; fi
        if (( SECONDS - START > LIMIT )); then
            fail "the install did not finish within $((LIMIT / 60)) minutes (see $W/shots)"
            kill "$QEMU_PID"; exit 1
        fi
    done
    INSTALL_SECONDS=$((SECONDS - START))
    # QEMU ending is not proof: it also ends when it is stopped or crashes.
    # The installer must have left a partitioned disk; phase 2 then proves
    # that the disk boots and holds the payload.
    if ! sfdisk -d "$W/disk.raw" 2> /dev/null | grep -q 'type='; then
        fail "the machine stopped after ${INSTALL_SECONDS}s and the disk has no partitions: the install did not finish (see $W/shots)"
        exit 1
    fi
    echo "$INSTALL_SECONDS" > "$W/installed"
    pass "the installer finished with no network in ${INSTALL_SECONDS}s and restarted; disk partitioned, $(du -h "$W/disk.raw" | cut -f1) written"
fi

# ---------------------------------------------------------------------------
# phase 2: first boot from the disk, setup from an answers disk
# ---------------------------------------------------------------------------

CONSOLE_PW="$(openssl rand -hex 12)"
ADMIN_PW="$(openssl rand -hex 12)"
rm -rf "$W/answers" && mkdir -p "$W/answers"
cat > "$W/answers/answers.env" <<EOF
KEYMAP=us
CONSOLE_PASSWORD=$CONSOLE_PW
NET_MODE=dhcp
HOSTNAME=rezztor-test-$FW
TIMEZONE=UTC
ADMIN_USER=admin
ADMIN_PASSWORD=$ADMIN_PW
SSH=$SSH
EOF
rm -f "$W/answers.img"
mke2fs -q -t ext4 -L REZZTOR-ANSWERS -d "$W/answers" "$W/answers.img" 16M
# The installed disk stays as it is; this run writes to a copy of it (shared
# blocks where the file system can, as btrfs and XFS do).
rm -f "$W/run.raw"
cp --reflink=auto --sparse=always "$W/disk.raw" "$W/run.raw"

# Two cards. The second sits on a network of its own with nothing behind it: the
# first boot must leave it alone, and the bios run adds it from the console menu.
log "first boot: two $NIC cards, setup from the answers disk"
START=$SECONDS
CTL=()
qemu \
    -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$P_HTTPS-:443,hostfwd=tcp:127.0.0.1:$P_HTTP-:80,hostfwd=tcp:127.0.0.1:$P_SSH-:22,hostfwd=tcp:127.0.0.1:$P_APP-:50080" \
    -device "$NIC,netdev=n0" \
    -netdev "user,id=n1,net=10.20.30.0/24,restrict=on" -device "$NIC,netdev=n1" \
    -drive "if=none,id=d0,format=raw,discard=unmap,file=$W/run.raw" -device ide-hd,bus=ide.1,drive=d0,bootindex=1 \
    -drive "if=none,id=d1,format=raw,file=$W/answers.img" -device ide-hd,bus=ide.2,drive=d1
URL="https://127.0.0.1:$P_HTTPS"
n=0
until HEALTH="$(curl -ksf --max-time 5 "$URL/api/health")"; do
    sleep 20
    n=$((n + 1))
    (( n % 3 )) || shot "$FW-03-first-boot-$(printf '%02d' $((n / 3)))" || true
    if ! kill -0 "$QEMU_PID" 2> /dev/null; then fail "the machine stopped"; exit 1; fi
    if (( SECONDS - START > LIMIT )); then fail "no answer on $URL/api/health within $((LIMIT / 60)) minutes (see $W/shots)"; exit 1; fi
done
SETUP_SECONDS=$((SECONDS - START))
pass "$URL/api/health answers after ${SETUP_SECONDS}s: $HEALTH"
sleep 20
shot "$FW-04-status-screen"

page="$(curl -ksL --max-time 20 -o /dev/null -w '%{http_code} %{url_effective}' "$URL/")"
# The product answers with its sign-in form; the stand-in image with a page of its own.
if [[ "$page" == 200* ]] && curl -ksL --max-time 20 "$URL/" | grep -qi -e 'name="password"' -e '<div id="root"'; then
    pass "the sign-in page loads over HTTPS: $page"
else
    fail "the sign-in page did not load: $page"
fi
# Network automation with nothing typed: sign in as the administrator and ask the product. The plugin must be
# on, both tokens must come from the deployment, and the executor must answer. Not with the stand-in images.
if [[ "$ISO" != *STANDIN* ]]; then
    JAR="$W/cookies"; rm -f "$JAR"
    tok="$(curl -ks --max-time 20 -c "$JAR" "$URL/login" | sed -n 's/.*name="login_form" value="\([^"]*\)".*/\1/p')"
    curl -ks --max-time 20 -b "$JAR" -c "$JAR" -o /dev/null --data-urlencode "username=admin" \
        --data-urlencode "password=$ADMIN_PW" --data-urlencode "login_form=$tok" \
        --data-urlencode "next=/app" "$URL/login?next=/app"
    csrf="$(curl -ks --max-time 20 -b "$JAR" -c "$JAR" -H "Origin: $URL" "$URL/api/csrf_token" | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
    automation="$(curl -ks --max-time 60 -b "$JAR" -H "Origin: $URL" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json' \
        -d '{"action": "test"}' "$URL/api/plugins/_ea_ansible/ansible_config" | python3 -c '
import json, sys
try:
    answer = json.load(sys.stdin)
except ValueError:
    answer = {}
config = answer.get("config") or {}
wanted = {"plugin on": answer.get("plugin_enabled") is True, "executor answers": answer.get("reachable") is True,
          "job token from the deployment": config.get("executor_token_origin") == "environment",
          "administration token from the deployment": config.get("admin_token_origin") == "environment"}
missing = [name for name, held in wanted.items() if not held]
print("NOT " + ", ".join(missing) + (": " + str(answer.get("detail") or answer.get("error") or "")) if missing else ", ".join(wanted))')"
    # The same for Azure changes: the product asks the cloud executor for its guard rails, which needs the
    # administration token of that executor. No key exists yet, so no change can run: that part is the customer's.
    azure="$(curl -ks --max-time 60 -b "$JAR" -H "Origin: $URL" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json' \
        -d '{"action": "get"}' "$URL/api/plugins/_ea_azure_change/azure_change_config" | python3 -c '
import json, sys
try:
    answer = json.load(sys.stdin)
except ValueError:
    answer = {}
held = answer.get("ok") is True and answer.get("configured") is True and isinstance(answer.get("guardrails"), dict)
print("the cloud executor answers the product" if held else "NOT answering: " + str(answer.get("error") or answer)[:300])')"
    rm -f "$JAR"
    if [[ -n "$automation" && "$automation" != NOT* ]]; then
        pass "network automation works with no token typed: $automation"
    else
        fail "network automation is not ready by itself: ${automation:-no answer}"
    fi
    if [[ -n "$azure" && "$azure" != NOT* ]]; then
        pass "Azure changes can be set up with no token typed: $azure"
    else
        fail "the cloud executor is not ready by itself: ${azure:-no answer}"
    fi
fi
redirect="$(curl -s --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' "http://127.0.0.1:$P_HTTP/")"
if [[ "$redirect" == "301 https://"* ]]; then pass "port 80 redirects: $redirect"; else fail "port 80: $redirect"; fi
log "certificate: $(openssl s_client -connect "127.0.0.1:$P_HTTPS" < /dev/null 2> /dev/null | openssl x509 -noout -fingerprint -sha256 -subject -ext subjectAltName | tr '\n' ' ')"

if curl -s --max-time 5 "http://127.0.0.1:$P_APP/api/health" > /dev/null; then
    fail "the product's own port 50080 answers from outside"
else
    pass "the product's own port 50080 does not answer from outside"
fi
banner="$(timeout 5 bash -c "exec 3<>/dev/tcp/127.0.0.1/$P_SSH; head -c 4 <&3" 2> /dev/null || true)"
if [[ "$SSH" == off ]]; then
    if [[ -z "$banner" ]]; then pass "port 22 does not answer (SSH is off)"; else fail "port 22 answers although SSH is off"; fi
elif [[ -n "${CLOSED:-}" ]]; then
    # A closed console (ADR 0083; the ISO was built with a support key). Nothing can look inside:
    # that is what is checked. A command sent over SSH must not run, and with the private half of
    # this build's throwaway key (SUPPORT_KEY) the one way in is walked through as a person would.
    if [[ "$banner" == SSH- ]]; then pass "port 22 answers (SSH switched on in the answers)"; else fail "SSH was switched on but port 22 does not answer"; fi
    printf '#!/bin/sh\necho %s\n' "$CONSOLE_PW" > "$W/askpass.sh"; chmod 700 "$W/askpass.sh"
    ran="$(SSH_ASKPASS="$W/askpass.sh" SSH_ASKPASS_REQUIRE=force setsid -w ssh -q -p "$P_SSH" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
        rezadmin@127.0.0.1 "echo ran-\$((6*7)); id" 2>&1 < /dev/null || true)"
    if [[ "$ran" == *ran-42* || "$ran" == *uid=* ]]; then
        fail "the console is closed and a command sent over SSH was run: $ran"
    else
        pass "a command sent over SSH as rezadmin is not run (closed console)"
    fi
    # The recovery account signs in on a local console only.
    printf '#!/bin/sh\necho rezrecover\n' > "$W/askpass-recover.sh"; chmod 700 "$W/askpass-recover.sh"
    ran="$(SSH_ASKPASS="$W/askpass-recover.sh" SSH_ASKPASS_REQUIRE=force setsid -w ssh -q -p "$P_SSH" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
        rezrecover@127.0.0.1 "echo ran-\$((6*7))" 2>&1 < /dev/null || true)"
    if [[ "$ran" == *ran-42* || "$ran" == *Challenge* ]]; then
        fail "the recovery account rezrecover signed in over SSH: $ran"
    else
        pass "the recovery account rezrecover cannot sign in over SSH"
    fi
    if [[ -n "${SUPPORT_KEY:-}" ]]; then
        if said="$(python3 "$(dirname "${BASH_SOURCE[0]}")/support-access.py" "$P_SSH" "$CONSOLE_PW" "$SUPPORT_KEY" 2>&1)"; then
            pass "$said"
        else
            fail "${said:-support access did not open}"
        fi
    fi
else
    if [[ "$banner" == SSH- ]]; then pass "port 22 answers (SSH switched on in the answers)"; else fail "SSH was switched on but port 22 does not answer"; fi
    # Look inside the machine, as the console account.
    printf '#!/bin/sh\necho %s\n' "$CONSOLE_PW" > "$W/askpass.sh"; chmod 700 "$W/askpass.sh"
    inside() {
        SSH_ASKPASS="$W/askpass.sh" SSH_ASKPASS_REQUIRE=force setsid -w ssh -q -p "$P_SSH" \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no \
            rezadmin@127.0.0.1 "echo $CONSOLE_PW | sudo -S -p '' sh -c '$1'" 2>&1
    }
    {
        echo "== docker ps";                inside "docker ps --format \"{{.Names}}: {{.Status}}\""
        echo "== listening sockets";        inside "ss -tulnH"
        echo "== firewall";                 inside "nft list table inet rezztor_guard"
        echo "== units";                    inside "systemctl is-active rezztor-firewall rezztor-guard rezztor-guard-runner nginx docker ssh; systemctl is-enabled rezztor-guard rezztor-guard-runner"
        echo "== memory (MiB) and disk";    inside "free -m; df -h /; docker system df"
        echo "== licence server";           inside "curl -sS -o /dev/null -m 15 -w \"%{http_code} from license.rezztor.com\n\" https://license.rezztor.com/ || true"
        echo "== boot image drivers";       inside "lsinitramfs /boot/initrd.img-* | grep -o -e vmw_pvscsi.ko -e vmxnet3.ko -e mptsas.ko -e mptspi.ko -e nvme.ko -e ahci.ko | sort -u | tr \"\\n\" \" \"; echo"
        echo "== ids";                      inside "cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub /etc/rezztor/tls/cert.pem; grep -c . /var/lib/rezztor/stack/.env"
    } > "$W/inside.txt"
    log "inside the machine: $W/inside.txt"
    # What a fresh install of this ISO has, for a core (ADR 0092): the bios run's list is
    # deploy/iso/cores/<version>.installed, the uefi run's <version>.uefi.installed. Every package of that core
    # reckons its Debian updates from these lists.
    inside 'dpkg-query -W -f="\${db:Status-Abbrev}|\${Package}\t\${Version}\t\${Architecture}\n" | grep "^ii" | cut -d"|" -f2 | sort' > "$W/installed.list"
    if [[ "$(wc -l < "$W/installed.list")" -gt 100 ]] && ! grep -qv $'^[^\t]*\t[^\t]*\t[^\t]*$' "$W/installed.list"; then
        pass "the packages of a fresh install: $W/installed.list ($(wc -l < "$W/installed.list")); for a core, commit it as deploy/iso/cores/<version>$([[ "$FW" == uefi ]] && echo .uefi).installed"
    else
        fail "the list of installed packages could not be read (see $W/installed.list)"
    fi
    # The journal is searched here and not on the machine: a search run there
    # would put the passwords into the journal itself, as sudo's command line.
    inside "journalctl -b --no-pager" > "$W/journal.txt"
    if grep -q -e "$CONSOLE_PW" -e "$ADMIN_PW" "$W/journal.txt"; then
        fail "a password is in the machine's journal"
    else
        pass "neither password is in the machine's journal ($(wc -l < "$W/journal.txt") lines)"
    fi
    rm -f "$W/journal.txt"
    # The status screen asks the product for its licence. Before activation that is Restricted; a LAB
    # build answers licensed without a licence (ADR 0082).
    want="Licence      Restricted: "; [[ "$ISO" == *LAB* ]] && want="Licence      licensed"
    licence="$(inside "rezztor-setup status" | grep "^Licence " || true)"
    if [[ "$licence" == "$want"* ]]; then pass "the status screen says: $licence"; else fail "the status screen's licence line is '${licence:-missing}', not '$want...'"; fi
    open_ports="$(inside "ss -tulnH" | awk '$5 !~ /^(127\.|\[::1\])/ && $5 !~ /:68$/ {sub(".*:", "", $5); print $5}' | sort -un | tr '\n' ' ')"
    if [[ "$open_ports" == "22 80 443 " ]]; then
        pass "listening on the network: only ports $open_ports"
    else
        fail "listening on the network: ports $open_ports(expected 22 80 443)"
    fi
    if grep -q '^inactive\|^failed' "$W/inside.txt"; then fail "a unit is not active (see inside.txt)"; else pass "units active: guard (both units), nginx, docker, ssh"; fi
    # Whose sign-in the product counts: the browser's (here 10.0.2.2, QEMU's address for this
    # computer), not the gateway of its own network, and not the address the browser claims.
    curl -ks --max-time 10 -o /dev/null -H 'X-Forwarded-For: 127.0.0.1' -d 'username=nobody&password=wrong' "$URL/login" || true
    seen="$(inside "docker logs --since 2m rezztor-eagent 2>&1 | grep \"POST /login\" | tail -n 1")"
    if [[ "$seen" == *" 10.0.2.2:"* ]]; then
        pass "the product counts a sign-in against the browser's own address, forged header or not"
    else
        fail "the product did not see the browser's address for a sign-in: ${seen:-no line}"
    fi
    # A file the browser asks for compressed arrives whole. With the web server's bridge to the
    # product missing from the image it arrived cut to ten bytes, and nothing here noticed.
    if curl -ks --compressed --max-time 20 -o "$W/font.bin" "$URL/app/fonts/plus-jakarta-sans-latin.woff2" && [[ "$(head -c 4 "$W/font.bin")" == wOF2 ]]; then
        pass "a file asked for compressed arrives whole ($(stat -c %s "$W/font.bin") bytes)"
    else
        fail "a file asked for compressed did not arrive whole"
    fi
    rm -f "$W/font.bin"
    # The second card, added from the console menu as a person types it: no web or SSH on it,
    # an address, a gateway, two networks behind that gateway, keep.
    second="$(inside "ls /sys/class/net" | grep '^e' | sed -n 2p)"
    if [[ -n "$second" && -z "$(inside "ip -4 -o addr show dev $second")" ]]; then
        pass "the first boot set up one interface and left the second card ($second) alone"
    else
        fail "the second card (${second:-none found}) has an address after the first boot"
    fi
    inside "printf \"2\\n\\n\\n10.20.30.10/24\\n10.20.30.2\\n172.16.0.0/24 10.100.100.0/24\\nkeep\\n0\\n\" | rezztor-network menu" > "$W/add-interface.txt"
    ways="$(inside "ip -4 route show default | wc -l; for a in 172.16.0.9 10.100.100.7 8.8.8.8; do ip -4 -o route get \$a; done; nft list set inet rezztor_guard dev_if")"
    if [[ "$(sed -n 1p <<< "$ways")" == 1 ]] && [[ "$(sed -n 2,3p <<< "$ways" | grep -c "via 10.20.30.2 dev $second ")" == 2 ]] \
        && ! sed -n 4p <<< "$ways" | grep -q "dev $second " && grep -q "\"$second\"" <<< "$ways"; then
        pass "Add an interface: one default route, 172.16.0.0/24 and 10.100.100.0/24 leave by $second through its gateway, and the guard knows it as a device network"
    else
        fail "Add an interface did not end as expected (see $W/add-interface.txt): $ways"
    fi
    if curl -ksf --max-time 10 "$URL/api/health" > /dev/null; then pass "the product still answers after the second interface was added"; else fail "the product does not answer after the second interface was added"; fi
fi

if [[ -n "${KEEP:-}" ]]; then
    log "left running (pid $QEMU_PID): $URL  admin / $ADMIN_PW   console rezadmin / $CONSOLE_PW"
else
    mon system_powerdown
    for _ in $(seq 60); do kill -0 "$QEMU_PID" 2> /dev/null || break; sleep 2; done
    kill "$QEMU_PID" 2> /dev/null || true
fi
log "install $(cat "$W/installed")s, first boot to healthy ${SETUP_SECONDS}s, RAM ${RAM} MiB, disk in use after the install $(du -h "$W/disk.raw" | cut -f1), after setup $(du -h "$W/run.raw" | cut -f1)"
exit $FAILED

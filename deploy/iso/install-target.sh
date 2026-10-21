#!/bin/sh
# Run by Debian's installer at the end (preseed/late_command), with no network.
#
# Stage 1 (this shell, the installer's own): copy /cdrom/rezztor to the disk.
# Stage 2 (--in-target, bash inside the installed system): check every file
# against its checksum, install Docker and the host tools from the copied
# package folder, and enable the units. Nothing is fetched, and nothing secret
# is made here: passwords and keys are made at first boot by rezztor-setup.
#
# Any failure ends the script with an error, and the installer shows it in red.

set -eu

if [ "${1:-}" != "--in-target" ]; then
    mkdir -p /target/var/lib/rezztor
    rm -rf /target/var/lib/rezztor/payload
    cp -a /cdrom/rezztor /target/var/lib/rezztor/payload
    exec in-target bash /var/lib/rezztor/payload/install-target.sh --in-target
fi

set -o pipefail
P=/var/lib/rezztor/payload
cd "$P"

# A damaged stick or a bad download stops here, before anything is installed.
sha256sum -c --quiet SHA256SUMS
(cd bundle && sha256sum -c --quiet SHA256SUMS)

# Packages come from the copied folder only. The installer's cdrom lines go,
# because the media will not be there after the reboot.
sed -i '/^deb cdrom:/d' /etc/apt/sources.list
echo "deb [trusted=yes] file:$P/os ./" > /etc/apt/sources.list.d/rezztor-os.list
apt-get update
# shellcheck disable=SC2046
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $(cat "$P/packages.txt")

# Docker: the image store is named on purpose, so an engine upgrade cannot
# change it and hide the images; container logs are capped.
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "features": { "containerd-snapshotter": true },
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF

install -m 0755 host/rezztor-setup host/rezztor-network host/rezztor-support host/rezztor-console host/rezztor-recover /usr/local/sbin/
# The update tool, its two units and the release keys it trusts (ADR 0080). The same script puts
# them on a machine that was installed before the tool existed.
sh host/install-update-tool.sh "$P"
install -m 0644 host/rezztor-setup.service host/rezztor-firewall.service \
    host/rezztor-load-images.service /etc/systemd/system/
install -m 0644 host/nginx-rezztor.conf /etc/nginx/sites-available/rezztor
ln -sf ../sites-available/rezztor /etc/nginx/sites-enabled/rezztor
rm -f /etc/nginx/sites-enabled/default

# The network guard, from the bundle, always on (both units name this path).
# One table for everything: what the containers may reach, no routing between
# the customer's networks, and what may reach this machine. "full" is the
# appliance's form; a hand install has no guard.json and gets the small one.
install -D -m 0755 bundle/rezztor-guard /opt/rezztor/rezztor-guard
install -m 0644 bundle/rezztor-guard.service bundle/rezztor-guard-runner.service /etc/systemd/system/
install -m 0644 host/90-rezztor-network.conf /etc/sysctl.d/
# Debian's own nftables unit stops with "flush ruleset", which would delete
# Docker's rules as well as ours. Nothing here uses it.
systemctl mask nftables.service

mkdir -p /etc/rezztor /etc/rezztor/networks
chmod 0700 /etc/rezztor
# The network list goes here and the Ansible executor reads it (uid 1000), so
# this one directory is readable. No secret is ever in it.
chmod 0755 /etc/rezztor/networks
printf '{"form": "full"}\n' > /etc/rezztor/guard.json
# Whose build this is, when the ISO was made for one customer (REZZTOR_ISO_MARK). The status screen shows it.
if [ -f build-mark ]; then install -m 0644 build-mark /etc/rezztor/build-mark; fi

# Network and names: systemd-networkd and systemd-resolved. rezztor-network
# writes the one netplan file (/etc/netplan/90-rezztor.yaml) and netplan turns
# it into networkd's files. With no network configured yet, waiting for one
# only delays the first boot by two minutes.
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
# The resolver only asks DNS servers. Left alone it also listens on port 5355
# (LLMNR) on every address.
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nLLMNR=no\nMulticastDNS=no\n' > /etc/systemd/resolved.conf.d/rezztor.conf
systemctl mask systemd-networkd-wait-online.service
systemctl enable systemd-networkd systemd-resolved systemd-timesyncd docker \
    rezztor-firewall rezztor-guard rezztor-guard-runner rezztor-load-images rezztor-setup

# nginx starts when rezztor-setup has made the certificate; SSH is off until
# the console menu switches it on.
systemctl disable nginx
systemctl disable ssh.service ssh.socket 2>/dev/null || true
systemctl mask ssh.service ssh.socket
cat > /etc/ssh/sshd_config.d/rezztor.conf <<'EOF'
PermitRootLogin no
AllowUsers rezadmin
EOF

# A closed console (ADR 0083), when the ISO carries a support key. The account
# rezadmin keeps its password and loses its shell: signing in is the setup
# menu, at the console and over SSH, and its one sudo rule is that menu with no
# argument. A root shell opens from the menu for a code Rezztor signed
# (host/rezztor-support). With no key on the ISO nothing here runs, and the
# account is what Debian's installer made it: a shell and sudo.
if ls keys/support/*.pub > /dev/null 2>&1; then
    install -d -m 0755 /opt/rezztor/support-keys
    install -m 0644 keys/support/*.pub /opt/rezztor/support-keys/
    usermod -s /usr/local/sbin/rezztor-console rezadmin
    gpasswd -d rezadmin sudo > /dev/null 2>&1 || true
    # A lost console password: the account rezrecover, on a local console only, whose login is the
    # recovery of rezztor-support (a challenge, Rezztor's code, a new password for rezadmin, nothing else).
    # Its password is its name and is printed on the sign-in screen: the proof is presence at the console
    # and Rezztor's code, not the password.
    useradd --system --no-create-home --home-dir / --shell /usr/local/sbin/rezztor-recover \
        --comment "console password recovery" rezrecover
    printf 'rezrecover:rezrecover\n' | chpasswd
    printf -- '-:rezrecover:ALL EXCEPT LOCAL\n' >> /etc/security/access.conf
    sed -i 's/^#[[:space:]]*\(account[[:space:]]\+required[[:space:]]\+pam_access\.so\)[[:space:]]*$/\1/' /etc/pam.d/login
    grep -q '^account[[:space:]]\+required[[:space:]]\+pam_access\.so' /etc/pam.d/login \
        || echo 'account required pam_access.so' >> /etc/pam.d/login
    printf 'rezadmin ALL=(root) NOPASSWD: /usr/local/sbin/rezztor-setup ""\n' > /etc/sudoers.d/rezztor-console
    printf 'rezrecover ALL=(root) NOPASSWD: /usr/local/sbin/rezztor-support recover\n' >> /etc/sudoers.d/rezztor-console
    chmod 0440 /etc/sudoers.d/rezztor-console
    visudo -cf /etc/sudoers.d/rezztor-console > /dev/null
    printf 'AllowTcpForwarding no\nAllowAgentForwarding no\nX11Forwarding no\nPermitTunnel no\nDenyUsers rezrecover\n' >> /etc/ssh/sshd_config.d/rezztor.conf
    # The boot loader is the other way to a shell: `e`, then init=/bin/bash.
    # Its entries start without a password and cannot be edited without one,
    # and the password is made here and kept nowhere. The recovery entries go.
    grub_pw=$(head -c 24 /dev/urandom | base64)
    grub_hash=$(printf '%s\n%s\n' "$grub_pw" "$grub_pw" | grub-mkpasswd-pbkdf2 2> /dev/null | awk '/grub\.pbkdf2/ {print $NF}')
    unset grub_pw
    if [ -z "$grub_hash" ]; then
        echo "error: the boot loader gave no password hash; the console would not be closed" >&2
        exit 1
    fi
    printf '#!/bin/sh\nprintf "%%s\\n" "set superusers=\\"rezztor\\"" "password_pbkdf2 rezztor %s"\n' "$grub_hash" > /etc/grub.d/01_rezztor
    chmod 0755 /etc/grub.d/01_rezztor
    sed -i 's/^CLASS="\(--class gnu-linux --class gnu --class os\)"$/CLASS="\1 --unrestricted"/' /etc/grub.d/10_linux
    if ! grep -q -- '--unrestricted' /etc/grub.d/10_linux; then
        echo "error: the boot entries could not be marked to start without the password" >&2
        exit 1
    fi
    mkdir -p /etc/default/grub.d
    echo 'GRUB_DISABLE_RECOVERY="true"' > /etc/default/grub.d/rezztor.cfg
    update-grub
fi

# Kernel messages stay off the console the setup tool draws on.
echo 'kernel.printk = 3 4 1 3' > /etc/sysctl.d/90-rezztor-console.conf

cat > /etc/issue <<'EOF'
Rezztor EAgent  -  TEST BUILD, not signed

Setup has not finished. It starts by itself on this console after a restart.

EOF

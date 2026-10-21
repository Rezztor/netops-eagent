#!/usr/bin/env bash
# Start the router inside the lab-c3725 container after a docker start/restart (the hypervisor comes up empty).
# Usage from the host:  docker exec -i lab-c3725 bash -s < scripts/lab/c3725-start-router.sh
# Add the word "config" to push /conf/startup-config.cfg over NVRAM (only if the router came up blank):
#   docker exec -i lab-c3725 bash -s config < scripts/lab/c3725-start-router.sh
set -u
exec 3<>/dev/tcp/127.0.0.1/7200
hv() { printf '%s\r\n' "$*" >&3; IFS= read -r -t 20 reply <&3; echo "$* -> ${reply%$'\r'}"; }

hv hypervisor working_dir /work
hv vm create c3725 0 c3725
hv vm set_ios c3725 /ios/c3725.image
hv vm set_ram c3725 256
hv vm set_nvram c3725 256
hv vm set_sparse_mem c3725 1
hv vm set_idle_pc c3725 0x60c09aa0
hv vm set_conf_reg c3725 0x2102
hv vm set_con_tcp_port c3725 2001
[ "${1:-}" = config ] && hv vm set_config c3725 /conf/startup-config.cfg
hv vm slot_add_binding c3725 1 0 NM-16ESW
hv nio create_tap nio_fa01 tap0
hv vm slot_add_nio_binding c3725 0 1 nio_fa01
hv vm start c3725
exec 3>&-

ip addr add 10.255.255.1/30 dev tap0 2>/dev/null
ip link set tap0 up
ethtool -K tap0 tx off rx off >/dev/null 2>&1
mkdir -p /run/sshd
pgrep -x sshd >/dev/null || /usr/sbin/sshd
ip -br addr show tap0

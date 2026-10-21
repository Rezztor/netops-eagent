#!/bin/bash
# The proof of the network guard (deploy/customer/rezztor-guard), from research 04, appendix A.
#
#   unshare -Urn bash network_guard_proof.sh
#
# Run by tests/test_network_guard.py. Everything lives in private network
# namespaces: no root, no Docker, nothing real is touched. It prints one PASS or
# FAIL line per check, once, and once more after a simulated Docker restart.
#
#   TOPOLOGY  three-vlan  one port on a trunk, VLANs 100 (devices), 200 (devices
#                         behind a gateway) and 300 (internet and users)
#             flat        one network, with the customer's own second bridge
#   GUARD     the rule set to load; empty loads none (to prove a hole is seen)
#   FORM      full | small   which form GUARD is (flat only: it decides what is expected)
#   SSH       on | off       whether GUARD was generated with SSH switched on
#   COMBINED  0 | 1          1: GUARD was generated from a list in which the network of the
#                            internet and the users is also a device network (three-vlan:
#                            VLAN 300; flat: the one network, written into a list). It stays
#                            open to the application, and the executor may enter it.
#
# "Docker" here is a stand-in that forwards everything, masquerades and publishes
# one port on every address. So this proves the guard is enough even when
# Docker's own rules do nothing. It does not prove the two together on a real host.
set -u
TOPOLOGY=${TOPOLOGY:-three-vlan}; GUARD=${GUARD:-}; FORM=${FORM:-full}; SSH=${SSH:-on}; COMBINED=${COMBINED:-0}
OUT=$(mktemp -d)
PIDS=()
mk() { unshare -n sleep 100000 & PIDS+=($!); eval "$1=$!"; sleep 0.1; }
ns() { local p=$1; shift; nsenter -t "$p" -n "$@"; }
trap 'kill "${PIDS[@]}" $(jobs -p) 2>/dev/null; rm -rf "$OUT"' EXIT

ip link set lo up
echo 1 > /proc/sys/net/ipv4/ip_forward

if [ "$TOPOLOGY" = three-vlan ]; then
  # --- the "switch" and the three customer networks behind one trunk ---------
  ip link add trunk0 type veth peer name sw0
  mk SW; ip link set sw0 netns "$SW"; ns "$SW" ip link set sw0 up
  mk DEV; mk GW; mk USR
  for pair in "100 $DEV" "200 $GW" "300 $USR"; do
    set -- $pair
    ns "$SW" ip link add link sw0 name "sw0.$1" type vlan id "$1"
    ns "$SW" ip link set "sw0.$1" netns "$2"
    ns "$2" ip link set lo up; ns "$2" ip link set "sw0.$1" up
  done
  ns "$DEV" ip addr add 10.0.100.10/24 dev sw0.100          # a switch in VLAN 100
  ns "$GW"  ip addr add 10.0.200.1/24 dev sw0.200           # VLAN 200 gateway
  ns "$GW"  ip addr add 10.20.0.9/32 dev lo                 # a device behind it
  ns "$GW"  ip addr add 198.51.100.7/32 dev lo              # a device behind it, on a public prefix
  ns "$USR" ip addr add 10.0.30.1/24 dev sw0.300            # VLAN 300 gateway, DNS server, a user
  ns "$USR" ip addr add 203.0.113.9/32 dev lo               # "the internet"
  # hostile static routes on the customer side, to try to use the host as a router
  ns "$DEV" ip route add default via 10.0.100.5
  ns "$USR" ip route add 10.0.100.0/24 via 10.0.30.5
  ns "$USR" ip route add 172.31.248.0/21 via 10.0.30.5
  # --- the appliance: one port, three VLAN sub-interfaces --------------------
  ip link set trunk0 up
  for v in 100 200 300; do ip link add link trunk0 name vlan$v type vlan id $v; ip link set vlan$v up; done
  ip addr add 10.0.100.5/24 dev vlan100
  ip addr add 10.0.200.5/24 dev vlan200
  ip addr add 10.0.30.5/24 dev vlan300
  ip route add default via 10.0.30.1
  ip route add 10.20.0.0/16 via 10.0.200.1
  ip route add 198.51.100.0/24 via 10.0.200.1
  HOSTADDRS="10.0.100.5, 10.0.200.5, 10.0.30.5"
else
  # --- one flat network: the gateway is also the DNS server, a device and a user
  ip link add lan0 type veth peer name far0
  mk LAN; ip link set far0 netns "$LAN"
  ns "$LAN" ip link set lo up; ns "$LAN" ip link set far0 up
  ns "$LAN" ip addr add 192.168.50.1/24 dev far0
  ns "$LAN" ip addr add 192.168.50.10/24 dev far0           # a switch on the same network
  ns "$LAN" ip addr add 203.0.113.9/32 dev lo               # "the internet"
  ns "$LAN" ip route add 172.31.248.0/21 via 192.168.50.5   # hostile: straight to the containers
  ip link set lan0 up
  ip addr add 192.168.50.5/24 dev lan0
  ip route add default via 192.168.50.1
  HOSTADDRS="192.168.50.5"
fi

# --- stand-ins for the Docker bridges and containers -------------------------
bridge() { # name subnet-prefix
  ip link add "$1" type bridge; ip addr add "$2.1/24" dev "$1"; ip link set "$1" up
}
join() { # bridge prefix host-part pid ifname [default]
  ip link add "v$4$5" type veth peer name "$5" netns "$4"
  ip link set "v$4$5" master "$1" up
  ns "$4" ip link set lo up; ns "$4" ip addr add "$2.$3/24" dev "$5"; ns "$4" ip link set "$5" up
  [ -z "${6:-}" ] || ns "$4" ip route add default via "$2.1"
}
bridge rz-app 172.31.249; bridge rz-runner 172.31.250; bridge rz-ansible 172.31.251; bridge rz-search 172.31.252
bridge rz-cloud 172.31.253; bridge rz-backend 172.31.254; bridge rz-clouddb 172.31.255
mk CAPP; mk CRUN; mk CANS; mk CSRCH; mk CCLD; mk CPG
join rz-app 172.31.249 2 "$CAPP" eth0 default               # the application: its own way out,
join rz-runner 172.31.250 3 "$CAPP" eth1                    # and a leg on the networks of what it talks to
join rz-backend 172.31.254 3 "$CAPP" eth2
join rz-runner 172.31.250 2 "$CRUN" eth0 default
join rz-ansible 172.31.251 2 "$CANS" eth0 default
join rz-search 172.31.252 2 "$CSRCH" eth0 default
join rz-cloud 172.31.253 2 "$CCLD" eth0 default
join rz-backend 172.31.254 2 "$CPG" eth0 default             # postgres; real Docker gives it no gateway at all
if [ "$TOPOLOGY" = flat ]; then                              # a container of the customer's own, not ours
  bridge br-other 172.18.0; mk COTH; join br-other 172.18.0 2 "$COTH" eth0 default
fi

docker_sim() { # Docker at its most permissive: forwards everything, masquerades, publishes the web port as 8443
nft -f - <<EOF
table ip docker_sim
delete table ip docker_sim
table ip docker_sim {
  chain pre {
    type nat hook prerouting priority dstnat;
    ip daddr { $HOSTADDRS } tcp dport 8443 dnat to 172.31.249.2:443
  }
  chain post {
    type nat hook postrouting priority srcnat;
    ip saddr 172.31.248.0/21 oifname != "rz-*" masquerade
    ip saddr 172.18.0.0/24 oifname != "br-other" masquerade
  }
  chain forward_sim {
    type filter hook forward priority filter; policy accept;
  }
}
EOF
}
docker_sim || { echo "ERROR the Docker stand-in did not load"; exit 3; }
if [ -n "$GUARD" ]; then nft -f "$GUARD" || { echo "ERROR the guard did not load"; exit 2; }; fi

# --- listeners ----------------------------------------------------------------
listen() { local p=$1 port=$2; ns "$p" socat TCP-LISTEN:"$port",reuseaddr,fork SYSTEM:'echo peer=$SOCAT_PEERADDR' 2>/dev/null & }
if [ "$TOPOLOGY" = three-vlan ]; then
  listen "$DEV" 22; listen "$GW" 22; listen "$GW" 17774; listen "$USR" 443; listen "$USR" 53
else
  listen "$LAN" 22; listen "$LAN" 443; listen "$LAN" 53
fi
listen "$CAPP" 443; listen "$CAPP" 8080; listen "$CRUN" 2222; listen "$CPG" 5432
for pair in "22 host-ssh" "80 host-http" "443 host-nginx" "2375 host-dockerapi"; do
  set -- $pair; socat TCP-LISTEN:"$1",reuseaddr,fork SYSTEM:"echo $2" 2>/dev/null &
done
sleep 0.7

# --- checks, all at once: a blocked connection costs its whole timeout --------
N=0; JOBS=()
in_ns() { local p=$1; shift; if [ "$p" = host ]; then "$@"; else ns "$p" "$@"; fi; }
t() { # expect(ok|no) description ns addr port [want-substring]; port "ping" sends one echo request
  N=$((N + 1))
  (
    want=$1 what=$2 p=$3 a=$4 port=$5 sub=${6:-}
    if [ "$port" = ping ]; then out=$(in_ns "$p" ping -c1 -W1 "$a" 2>/dev/null | grep -o '1 received')
    else out=$(in_ns "$p" timeout 3 socat -T1 - TCP:"$a":"$port",connect-timeout=1 </dev/null 2>/dev/null); fi
    [ -n "$out" ] && res=ok || res=no
    if [ "$res" = "$want" ] && { [ -z "$sub" ] || [[ "$out" == *"$sub"* ]]; }; then printf 'PASS  want=%s  %s\n' "$want" "$what"
    else printf 'FAIL  want=%s  %s  got=%s %s\n' "$want" "$what" "$res" "$out"; fi
  ) > "$OUT/$(printf %03d "$N")" &
  JOBS+=($!)
}
by() { [ "$1" = "$2" ] && echo ok || echo no; }   # ok when the form (or the SSH switch) is the one named

shared() { # true in every topology and both forms
t ok "application -> the runner, on the network they share"                  "$CAPP" 172.31.250.2 2222
t ok "application -> postgres, on the network they share"                    "$CAPP" 172.31.254.2 5432
t no "runner -> the application's own port, on the network they share"     "$CRUN" 172.31.250.3 8080
t no "runner -> postgres (two bridges of ours)"                              "$CRUN" 172.31.254.2 5432
t no "runner -> the application's own address on its outbound network"       "$CRUN" 172.31.249.2 443
t no "runner -> the host's own SSH on the bridge gateway"                    "$CRUN" 172.31.250.1 22
t no "search engine -> the host's own SSH on the bridge gateway"             "$CSRCH" 172.31.252.1 22
t no "postgres -> the host's own SSH on the bridge gateway"                  "$CPG" 172.31.254.1 22
t no "postgres -> internet"                                                  "$CPG" 203.0.113.9 443
t ok "runner -> internet"                                                    "$CRUN" 203.0.113.9 443
t ok "search engine -> internet"                                             "$CSRCH" 203.0.113.9 443
t ok "cloud executor -> internet"                                            "$CCLD" 203.0.113.9 443
}

suite_three_vlan() {
t ok "executor -> VLAN 100 device, seen as the host's VLAN 100 address"      "$CANS" 10.0.100.10 22 peer=10.0.100.5
t ok "executor -> device behind the VLAN 200 gateway, seen as .200.5"        "$CANS" 10.20.0.9 22 peer=10.0.200.5
t ok "executor -> device on a public prefix behind VLAN 200"                 "$CANS" 198.51.100.7 22 peer=10.0.200.5
t no "executor -> internet"                                                  "$CANS" 203.0.113.9 443
t "$(by "$COMBINED" 1)" "executor -> a private address on the user network (open only when it also carries devices)" "$CANS" 10.0.30.1 443
t ok "application -> a private address on the user network (a model server)" "$CAPP" 10.0.30.1 443
t ok "executor -> the DNS server, port 53 only"                              "$CANS" 10.0.30.1 53
t ok "application -> internet (model API), seen as the VLAN 300 address"     "$CAPP" 203.0.113.9 443 peer=10.0.30.5
t no "application -> VLAN 100 device"                                        "$CAPP" 10.0.100.10 22
t no "application -> device behind VLAN 200"                                 "$CAPP" 10.20.0.9 22
t no "application -> device on a public prefix behind VLAN 200"              "$CAPP" 198.51.100.7 22
t ok "application -> the one named exception inside a device network"        "$CAPP" 10.20.0.9 17774 peer=10.0.200.5
t no "runner -> VLAN 100 device"                                             "$CRUN" 10.0.100.10 22
t no "runner -> device behind VLAN 200"                                      "$CRUN" 10.20.0.9 22
t no "runner -> device on a public prefix behind VLAN 200"                   "$CRUN" 198.51.100.7 22
t no "runner -> a private address through the internet gateway"              "$CRUN" 10.0.30.1 443
t ok "runner -> the DNS server on a private address, port 53 only"           "$CRUN" 10.0.30.1 53
t no "runner -> the application's named exception"                           "$CRUN" 10.20.0.9 17774
t no "runner -> the host's own SSH on its VLAN 100 address"                  "$CRUN" 10.0.100.5 22
t no "search engine -> VLAN 100 device"                                      "$CSRCH" 10.0.100.10 22
t no "search engine -> device behind VLAN 200"                               "$CSRCH" 10.20.0.9 22
t no "cloud executor -> VLAN 100 device"                                     "$CCLD" 10.0.100.10 22
t no "cloud executor -> a private address through the internet gateway"      "$CCLD" 10.0.30.1 443
t no "VLAN 100 device -> internet through the host"                          "$DEV" 203.0.113.9 443
t no "user network -> VLAN 100 device through the host"                      "$USR" 10.0.100.10 22
t no "VLAN 100 device -> web port on the host's VLAN 100 address"            "$DEV" 10.0.100.5 443
t no "VLAN 100 device -> a container port published on every address"        "$DEV" 10.0.100.5 8443
t no "VLAN 100 device -> web port on the host's VLAN 300 address"            "$DEV" 10.0.30.5 443
t no "VLAN 100 device -> host SSH"                                           "$DEV" 10.0.100.5 22
t no "VLAN 100 device -> a Docker API port on the host"                      "$DEV" 10.0.100.5 2375
t ok "VLAN 100 device -> ping to the appliance"                              "$DEV" 10.0.100.5 ping
t no "user network -> postgres container address directly"                   "$USR" 172.31.254.2 5432
t no "user network -> a Docker API port on the host"                         "$USR" 10.0.30.5 2375
t no "user network -> a container port published on every address"           "$USR" 10.0.30.5 8443
t ok "user network -> web port (nginx on the host)"                          "$USR" 10.0.30.5 443 host-nginx
t ok "user network -> port 80 (nginx redirects it)"                          "$USR" 10.0.30.5 80 host-http
t "$(by "$SSH" on)" "user network -> host SSH (open only while switched on)" "$USR" 10.0.30.5 22
t ok "host itself -> VLAN 100 device (the wizard's test)"                    host 10.0.100.10 22 peer=10.0.100.5
shared
}

suite_flat() {
t ok "executor -> a device on the network, seen as the host's address"       "$CANS" 192.168.50.10 22 peer=192.168.50.5
t "$(by "$COMBINED" 0)" "executor -> internet (open only while no device network is known)" "$CANS" 203.0.113.9 443
t ok "application -> internet"                                               "$CAPP" 203.0.113.9 443 peer=192.168.50.5
t ok "application -> a private address (a model server on the network)"      "$CAPP" 192.168.50.1 443
t no "runner -> a device on the network"                                     "$CRUN" 192.168.50.10 22
t no "runner -> a private address through the gateway"                       "$CRUN" 192.168.50.1 443
t ok "runner -> the DNS server on a private address, port 53 only"           "$CRUN" 192.168.50.1 53
t no "runner -> the host's own SSH on its network address"                   "$CRUN" 192.168.50.5 22
t no "search engine -> a device on the network"                              "$CSRCH" 192.168.50.10 22
t no "cloud executor -> a device on the network"                             "$CCLD" 192.168.50.10 22
t no "network -> postgres container address directly"                        "$LAN" 172.31.254.2 5432
t no "network -> the application's container address directly"               "$LAN" 172.31.249.2 443
t ok "host itself -> a device on the network"                                host 192.168.50.10 22 peer=192.168.50.5
t ok "network -> ping to the host"                                           "$LAN" 192.168.50.5 ping
t ok "network -> port 443 on the host"                                       "$LAN" 192.168.50.5 443 host-nginx
# The two forms differ exactly here. Small: the customer's own server, its published web port and its
# other containers are left alone. Full: the appliance is closed and is nobody's router.
t "$(by "$FORM" small)" "network -> the web port the Compose file published (hand install)" "$LAN" 192.168.50.5 8443
t "$(by "$FORM" small)" "the customer's own container -> internet (not ours to judge)"      "$COTH" 203.0.113.9 443
t "$(by "$FORM" small)" "network -> another port on the host (not ours to close)"           "$LAN" 192.168.50.5 2375
if [ "$FORM" = small ]; then t ok "network -> host SSH (not ours to close)"                 "$LAN" 192.168.50.5 22
else t "$(by "$SSH" on)" "network -> host SSH (open only while switched on)"                "$LAN" 192.168.50.5 22; fi
shared
}

run() {
  echo "== $1"
  N=0; JOBS=(); rm -f "$OUT"/*
  if [ "$TOPOLOGY" = three-vlan ]; then suite_three_vlan; else suite_flat; fi
  wait "${JOBS[@]}"
  cat "$OUT"/*
}
run "topology=$TOPOLOGY guard=${GUARD:+on}${GUARD:-OFF} form=$FORM ssh=$SSH"
docker_sim
run "after a simulated Docker restart (its tables deleted and recreated)"
exit 0

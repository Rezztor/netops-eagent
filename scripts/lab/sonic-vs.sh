#!/usr/bin/env bash
# Local lab: one community SONiC virtual switch for testing the network tools.
#
#   scripts/lab/sonic-vs.sh up      create the lab network + container, enable SSH, create the login
#   scripts/lab/sonic-vs.sh down    remove the container (the network is removed when empty)
#   scripts/lab/sonic-vs.sh status
#   scripts/lab/sonic-vs.sh check   read the interface table through the executor (also runs at the end of `up`)
#
# Device: lab-sonic  172.30.30.11  login admin / $LAB_PASSWORD (default below; lab only, never a real credential).
# The lab network is its own Docker network. Only the Ansible executor should be attached to it
# (the runner must stay off it). 172.30.30.0/24 is chosen to avoid containerlab's 172.20.20.0/24 and the
# 172.18-25.0.0 ranges Docker hands out to other stacks.
#
# Known limits of this image (networkop/sonic-vs, a stripped community build): SSH and the `show` CLI answer, but
# `show version` crashes after its first line, `sonic_facts` and the Dell Enterprise REST/httpapi are absent, and the
# Dell `sonic-cli` terminal does not exist, so network_cli can never open. The device is therefore the inventory platform
# `sonic_community` (ansible_connection paramiko_ssh): the catalogue reads run fixed `show` commands over plain SSH and
# `reads/interfaces` returns the table (the `check` below proves it; `up` runs it when the executor is up). The Dell
# `sonic` change entries still cannot be proved on it; an IOSv/IOL or Enterprise SONiC image is needed for those.
# Prerequisite for `check`: lab-sonic is a `sonic_community` device in Inventory, synced to the executor with the
# `lab-sonic` credential, and rezztor-eagent-ansible is on the eagent-lab network.
set -euo pipefail

NET=eagent-lab; SUBNET=172.30.30.0/24; GATEWAY=172.30.30.1
NAME=lab-sonic; IP=172.30.30.11; IMAGE="${SONIC_IMAGE:-networkop/sonic-vs:20211006}"
PASSWORD="${LAB_PASSWORD:-LabAdmin123!}"

case "${1:-}" in
    up)
        docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet "$SUBNET" --gateway "$GATEWAY" "$NET" >/dev/null
        if ! docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
            # privileged: the SONiC switch image needs its own network namespace and kernel interfaces.
            docker run -d --name "$NAME" --hostname "$NAME" --privileged --network "$NET" --ip "$IP" "$IMAGE" >/dev/null
        fi
        docker start "$NAME" >/dev/null 2>&1 || true
        sleep 30   # supervisord starts the SONiC daemons
        docker exec "$NAME" sh -c "id admin >/dev/null 2>&1 || useradd -m -s /bin/bash admin
            echo 'admin:$PASSWORD' | chpasswd; mkdir -p /run/sshd
            # A real SONiC gives admin passwordless sudo, and its own show and config commands call
            # sudo. This image ships none and its package archive is gone, so admin is uid 0 here and
            # sudo is a stand-in that runs the command. Lab only.
            sed -i 's/^admin:x:[0-9]*:[0-9]*:/admin:x:0:0:/' /etc/passwd
            printf '%s\n' '#!/bin/sh' 'while [ \"\${1#-}\" != \"\$1\" ]; do shift; done' 'exec \"\$@\"' > /usr/local/bin/sudo
            chmod 755 /usr/local/bin/sudo
            # sshd counts any uid 0 account as root, and refuses its password by default.
            (grep -q '^PermitRootLogin' /etc/ssh/sshd_config \
              && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config) \
              || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
            grep -q '^PasswordAuthentication' /etc/ssh/sshd_config \
              && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
              || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
            pgrep -x sshd >/dev/null || /usr/sbin/sshd"
        echo "up: $NAME $IP (ssh admin, network $NET)"
        "$0" check ;;
    check)
        # A job through the executor itself: its token, its catalogue commit, its stored credential.
        docker ps --format '{{.Names}}' | grep -qx rezztor-eagent-ansible \
            || { echo "check: skipped, rezztor-eagent-ansible is not running"; exit 0; }
        docker exec -i rezztor-eagent-ansible python - <<'PY'
import http.client, json, os, socket, sys, time
token, sock = os.environ["REZZTOR_ANSIBLE_EXECUTOR_TOKEN"], os.environ.get("REZZTOR_ANSIBLE_SOCKET")
class Unix(http.client.HTTPConnection):  # the executor listens on a socket only when one is set (ADR 0051)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(30)
        self.sock.connect(sock)
def call(path, body=None):
    conn = Unix("localhost") if sock else http.client.HTTPConnection("127.0.0.1", 8500, timeout=30)
    conn.request("POST" if body else "GET", path, json.dumps(body) if body else None,
                 {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return json.load(conn.getresponse())
commit = call("/healthz")["catalogue_commit"]
job = call("/jobs", {"playbook_name": "reads/interfaces", "commit": commit, "variables": {},
                     "limit": "lab-sonic", "check_mode": True})["job_id"]
for _ in range(60):
    rec = call("/jobs/" + job)
    if rec["status"] in ("succeeded", "failed"):
        break
    time.sleep(2)
ok = rec["status"] == "succeeded" and "Ethernet0" in rec.get("output", "")
print("check: reads/interfaces on lab-sonic", "OK" if ok else "FAILED (" + rec["status"] + ")")
sys.exit(0 if ok else 1)
PY
        ;;
    down)
        docker rm -f "$NAME" >/dev/null 2>&1 || true
        docker network rm "$NET" >/dev/null 2>&1 || true
        echo "down" ;;
    status)
        docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
        docker exec "$NAME" sh -c 'pgrep -x sshd >/dev/null && echo "sshd: running"' 2>/dev/null || echo "sshd: not running" ;;
    *) sed -n 2,7p "$0"; exit 1 ;;
esac

#!/bin/bash
set -e

echo "====================BASE PACKAGES1 START===================="

apt-get update && apt-get upgrade -y

# openssh-client, not openssh-server. The application reaches each user's
# account on the runner over SSH (ADR 0018, ADR 0038): runner_pool generates a
# keypair with ssh-keygen and shell_openssh connects with ssh. Neither existed
# here after sshd was removed with code execution, so the image started, served,
# and could not execute a single line of agent code -- a failure only visible by
# running the built image. This container still accepts no shell connections;
# a client is not a server.
apt-get install -y --no-install-recommends \
    sudo curl wget git cron unzip 7zip openssh-client

echo "====================BASE PACKAGES1 END===================="

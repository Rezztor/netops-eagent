#!/bin/bash
set -euo pipefail

# Two modes, chosen by the uid this container was started with.
#
#   non-root  one account, one key. A single-runner deployment, where every
#             user shares that account -- and the retired pod-per-user model,
#             where the pod itself was the separation.
#
#   root      one account per user, from the pool baked into the image. Only
#             root may setuid into another account, which is what makes the
#             separation real UIDs rather than one view of another's files.
#
# Root is narrower than it sounds: cap_drop ALL with only CAP_SETUID and
# CAP_SETGID added, a read-only root filesystem, and every session sshd spawns is
# an unprivileged account holding no capabilities. Note that dropping ALL removes
# CAP_DAC_OVERRIDE too, so this root cannot write where the file modes say it
# cannot. ADR 0019 said the runner runs unprivileged; its successor records why
# the daemon is the exception and the sessions are not.
#
# Everything written here goes to RUNTIME_DIR, a volume, so the image's root
# filesystem stays read-only.

if [ "$(id -u)" = "0" ]; then
    MODE=slots
else
    MODE=single
fi

if [ -z "${RUNNER_AUTHORIZED_KEY:-}" ]; then
    # A runner with no key accepts nobody, which is a silent, confusing failure.
    # Refusing to start makes the misconfiguration obvious at rollout instead of
    # at the first code execution.
    echo "RUNNER_AUTHORIZED_KEY is not set. Refusing to start a runner nobody can reach." >&2
    exit 1
fi

# Deliberately no chmod on the directory itself.
#
# A volume can arrive with *group* ownership rather than user ownership --
# root:1000, mode 0775 -- and a process running as uid 1000 can then write into
# it but not chmod it, because it does not own it. Docker's
# `--tmpfs ...,uid=1000` does give user ownership, so a chmod here passes on
# this stack and fails with "chmod: changing permissions of '/run/runner':
# Operation not permitted" anywhere the volume is handed over the other way.
#
# The directory mode is not what protects these files anyway: the modes set on
# the files below are, and nothing else shares this volume.
mkdir -p "${RUNTIME_DIR}"

# One authorized_keys file per account. sshd resolves %u before it reads the
# file, so a key in one slot's file cannot open another slot's session.
#
# Every slot's file is truncated on start, deliberately. RUNTIME_DIR is a volume,
# so a key registered before a restart would otherwise outlive the pool's memory
# of it and keep opening sessions for an account that may since have been
# deleted. The pool re-registers on the next acquire, which runs before every
# execution, so the cost is nothing and the failure mode is refusal rather than a
# stale key that still works.
#
# Owned by whoever is running and world-readable on purpose: sshd reads these as
# root before it drops privileges, StrictModes is off, and the contents are
# public keys.
# No chmod on the directories, for the reason given above about RUNTIME_DIR and
# for one more: cap_drop ALL takes CAP_FOWNER with it, so this root cannot chmod
# a directory it does not own. That is not hypothetical -- the key directory is
# a volume, and a bind mount arrives owned by whoever owns it on the host. The
# modes that matter are on the files, which we create and therefore own.
mkdir -p "${RUNTIME_DIR}/keys/${RUNNER_USER}"

# The exception is each slot's directory: the application registers a key by
# replacing the file in it, and since 2026-10-16 the application is uid 1000,
# not root. root:${RUNNER_USER} 0775 lets it, and CHOWN (held for the pty) is
# what lets this root make the directory its own first, whoever created it.
# Here ${RUNNER_USER} is the shared account, reached only with the key the
# application holds; the slot accounts are other uids and not in its group.
# The file the application left is its own, and this root has no
# DAC_OVERRIDE to truncate it: it is removed and made again.
if [ "${MODE}" = "slots" ]; then
    for slot in $(getent passwd | awk -F: -v u="${RUNNER_USER}" 'index($1, u "-") == 1 {print $1}'); do
        mkdir -p "${RUNTIME_DIR}/keys/${slot}"
        chown "root:${RUNNER_USER}" "${RUNTIME_DIR}/keys/${slot}"
        chmod 0775 "${RUNTIME_DIR}/keys/${slot}"
        rm -f "${RUNTIME_DIR}/keys/${slot}/authorized_keys"
        : > "${RUNTIME_DIR}/keys/${slot}/authorized_keys"
        chmod 0644 "${RUNTIME_DIR}/keys/${slot}/authorized_keys"
    done
fi

# The shared account keeps its single key in both modes: it is how the
# application reaches a single-runner deployment. In slot mode sshd refuses the
# account whatever key it presents (below).
printf '%s\n' "${RUNNER_AUTHORIZED_KEY}" > "${RUNTIME_DIR}/keys/${RUNNER_USER}/authorized_keys"
chmod 0644 "${RUNTIME_DIR}/keys/${RUNNER_USER}/authorized_keys"
unset RUNNER_AUTHORIZED_KEY

# Host keys are generated per container rather than shipped in the image: a
# shared host key across every runner would let one impersonate another, which
# is exactly the substitution the application's host key checking exists to
# catch.
if [ ! -f "${RUNTIME_DIR}/ssh_host_ed25519_key" ]; then
    ssh-keygen -q -t ed25519 -f "${RUNTIME_DIR}/ssh_host_ed25519_key" -N ""
fi
chmod 600 "${RUNTIME_DIR}/ssh_host_ed25519_key"

# Publish the host key so the orchestrator can pin it before it ever connects.
#
# The public half, not a fingerprint: the caller needs to build a known_hosts
# entry, and a fingerprint cannot be turned back into one. Reading this from the
# pod's logs means the private half never leaves the container -- no Secret to
# manage, nothing in the pod spec for anyone with read access to lift, and the
# very first connection can use strict host key checking rather than trusting
# whatever answers.
echo "RUNNER_HOST_KEY $(cat "${RUNTIME_DIR}/ssh_host_ed25519_key.pub")"

# And publish it beside the key directories, where the application can read it.
#
# The retired pod-per-user pool pinned the host key by reading the line above
# out of the pod's logs. A shared runner has no equivalent the application can
# reach, and the alternative -- trust-on-first-use, which is all the static
# fallback path has -- is exactly the weakness pinning exists to remove. The
# public half only; the private half never leaves this container.
install -m 0644 "${RUNTIME_DIR}/ssh_host_ed25519_key.pub" "${RUNTIME_DIR}/keys/host_key.pub"

mkdir -p "${WORKSPACE}"

# In slot mode the shared account does not log in at all. Nothing reaches it
# there (the application uses it only when no pool is configured), and it is
# the group that may write every slot's key directory: a session as eagent could
# put its own key in front of any slot. sshd_config admits it for single mode
# and is read-only here, and AllowUsers given again would add to that list
# rather than replace it, so slot mode denies it on the command line instead.
if [ "${MODE}" = "slots" ]; then
    exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config -o "DenyUsers=${RUNNER_USER}"
fi
exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config

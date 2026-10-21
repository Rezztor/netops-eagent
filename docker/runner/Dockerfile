# Rezztor EAgent code-execution runner.
#
# Agent-executed code runs here, not in the application container. The
# application reaches it over SSH -- the contract shell_ssh.py already speaks --
# so this image only has to present an unprivileged shell with a Python and Node
# toolchain and nothing worth reaching.
#
# Deliberately minimal. Every package here is something agent-written code can
# use, so the smallest set that still does the job is the correct set.
#
# One process in this image runs as root: sshd, because only root may hand a
# session to a slot account (ADR 0038; see "No USER line" at the end of this
# file). It holds five capabilities and no others -- SETUID, SETGID, CHOWN,
# AUDIT_WRITE and SYS_CHROOT, each explained in the compose file -- and not
# DAC_OVERRIDE, so it cannot read or write where the file modes forbid it. Every
# session, and so everything agent code runs as, is an unprivileged account with
# no capability at all, on a read-only root filesystem. This header said until
# 2026-10-11 that nothing here runs as root; that was the image before the pool.
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    RUNNER_USER=eagent \
    RUNNER_UID=1000 \
    WORKSPACE=/workspace \
    RUNTIME_DIR=/run/runner \
    SSH_PORT=2222 \
    RUNNER_SLOTS=128 \
    RUNNER_SLOT_BASE_UID=2000

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        python3 python3-venv python3-pip \
        nodejs npm \
        ca-certificates curl git xz-utils \
        procps \
    && rm -rf /var/lib/apt/lists/*

# procps is `ps`, `pgrep` and `pkill` for the code that runs here. `release()`
# used `pkill` to stop a departing account and no longer depends on a package
# for that: it sends `kill -9 -1`, a shell builtin, which the kernel applies to
# every process of the account in one pass (runner_pool._kill_sessions).
# Revoking a key does not close sessions that are already open -- OpenSSH
# checks `authorized_keys` once, at authentication -- so that kill is what
# keeps a deleted user's code from running on under a uid the next claimant is
# about to be given.

# The interpreters the code execution tool actually invokes.
#
# It runs `ipython -c` for python and `node /exe/node_eval.js` for
# javascript -- not `python3`, and not a bare `node`. Both of those come from the
# *application* image (install_python.sh installs ipython; the /exe directory is
# copied in there), so a runner built without them starts cleanly, accepts SSH,
# and fails on the first line of agent code with `ipython: command not found`.
# The code itself arrives on file descriptor 3 (base64-decoded by coreutils'
# `base64`), never as an argument, so no other slot sees it in `ps`.
#
# A virtualenv rather than a system pip install: Debian marks its Python
# environment externally managed, and --break-system-packages is a promise to
# debug someone else's package manager later.
ENV VENV=/opt/runner-venv
RUN python3 -m venv ${VENV} \
    && ${VENV}/bin/pip install --no-cache-dir --upgrade pip \
    && ${VENV}/bin/pip install --no-cache-dir ipython requests markdown
# Symlinked onto the default PATH rather than relying on ENV PATH.
#
# A command arriving over SSH runs in a non-interactive, non-login shell, which
# sources no profile (Debian's bash does read ~/.bashrc there, which is why the
# slot accounts have a shell of their own, below) and takes its PATH from
# sshd's default -- not from the image's ENV. Setting ENV PATH here looks correct, builds correctly, and leaves
# `ipython: command not found` at the first python execution. Symlinks are on
# every PATH there is.
ENV PATH="/opt/runner-venv/bin:${PATH}"
RUN ln -sf ${VENV}/bin/ipython /usr/local/bin/ipython \
    && ln -sf ${VENV}/bin/python /usr/local/bin/runner-python

# The node evaluator the tool shells out to by absolute path.
COPY docker/run/fs/exe/node_eval.js /exe/node_eval.js
RUN chmod 0444 /exe/node_eval.js

# The document toolchain.
#
# Here at build time rather than fetched when an agent needs it, and that is the
# whole point. Everything writable in this container is tmpfs: a toolchain
# installed at runtime is gone at the next restart and downloaded again. It
# happened -- an agent asked for a PDF pulled 32 MB of static pandoc into /tmp,
# twice in one day.
#
# Three reasons that is the wrong shape, none of them about tidiness. The runner
# has a NetworkPolicy limiting what agent code can reach, and one that must call
# out to do its job is one whose egress can never be tightened. The air-gapped
# image is a fiction if a document task downloads its own tools. And the model
# picks the URL, so nothing pins a version or checks a hash -- which is a supply
# chain, just not one anybody is watching.
#
# No LaTeX. texlive-full is 2-4 GB and is why people conclude PDF generation is
# expensive. Pandoc writes typst, and typst is a single static binary, so the
# pair covers Markdown and HTML to PDF for about a fortieth of that.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        fonts-dejavu-core \
        fonts-liberation2 \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# On the fonts: a missing glyph is not an error. It renders as a box, the
# conversion succeeds, and nobody finds out until someone reads the document.
# DejaVu and Liberation cover Latin, Greek and Cyrillic; noto-core adds Sinhala
# and Tamil, which an organisation here will want, plus most other scripts, for
# 41 MB. CJK is deliberately left out -- `fonts-noto-cjk` is another 89 MB for
# languages nobody has asked for. Add it to the list above if that changes; the
# smoke test below will start covering it once it is there.

# Pinned by version and verified by digest, from the project's own release.
# `curl | sh` would put the contents of someone else's HTTP response into an
# image that runs agent-written code.
ARG TYPST_VERSION=0.15.1
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) target=x86_64-unknown-linux-musl; \
               digest=a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c ;; \
        arm64) target=aarch64-unknown-linux-musl; \
               digest=5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee ;; \
        *) echo "no typst build pinned for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/typst.tar.xz \
        "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-${target}.tar.xz"; \
    echo "${digest}  /tmp/typst.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/typst.tar.xz -C /tmp; \
    install -m 0555 "/tmp/typst-${target}/typst" /usr/local/bin/typst; \
    rm -rf /tmp/typst.tar.xz "/tmp/typst-${target}"

# Give pandoc's typst template a font to fall back to.
#
# Debian ships pandoc 3.1.11, whose typst template declares `font: ()` as the
# default when no `mainfont` is set. Typst tolerated an empty fallback list once;
# 0.15 refuses it -- "font fallback list must not be empty" -- so every
# unstyled document failed. Version skew between a 2023 pandoc and a current
# typst, not a missing font: `typst fonts` lists all six installed here.
#
# The `grep` before the edit is the point of doing it this way. If a later
# pandoc changes that line, the build fails here rather than quietly leaving the
# template unpatched and the failure to a user's first PDF.
RUN set -eux; \
    template=/usr/share/pandoc/data/templates/template.typst; \
    grep -q '^  font: (),$' "$template"; \
    sed -i 's/^  font: (),$/  font: ("Liberation Serif", "DejaVu Sans"),/' "$template"; \
    grep -q '^  font: ("Liberation Serif", "DejaVu Sans"),$' "$template"

# Convert something, here, now.
#
# A toolchain that is present but cannot produce a file is worse than one that
# is absent: the agent finds the binary, tries, and reports a failure the user
# has to interpret. This fails the build instead. The table and the accent are
# deliberate -- a missing font renders as boxes rather than an error, so the
# check reads the bytes back rather than trusting the exit status.
RUN set -eux; \
    printf '%s\n' \
        '# Smoke' \
        '' \
        '| Item | Value |' \
        '|---|---|' \
        '| Café | 1 |' \
        '' \
        '**Bold** text.' \
        > /tmp/smoke.md; \
    printf '%s\n' '<h1>Smoke</h1><p>Café</p>' > /tmp/smoke.html; \
    pandoc /tmp/smoke.md --pdf-engine=typst -o /tmp/smoke.md.pdf; \
    pandoc /tmp/smoke.html --pdf-engine=typst -o /tmp/smoke.html.pdf; \
    pandoc /tmp/smoke.md -o /tmp/smoke.docx; \
    head -c 5 /tmp/smoke.md.pdf | grep -q '%PDF-'; \
    head -c 5 /tmp/smoke.html.pdf | grep -q '%PDF-'; \
    head -c 2 /tmp/smoke.docx | grep -q 'PK'; \
    [ "$(stat -c %s /tmp/smoke.md.pdf)" -gt 1000 ]; \
    /opt/runner-venv/bin/python -c "import markdown; assert markdown.markdown('# x') == '<h1>x</h1>'"; \
    rm -f /tmp/smoke.md /tmp/smoke.html /tmp/smoke.md.pdf /tmp/smoke.html.pdf /tmp/smoke.docx

# And that the scripts we claim to support are reachable.
#
# Checked by asking typst what it can see rather than by reading glyphs back out
# of a PDF, which would mean carrying poppler in this image for the sake of one
# assertion. It catches the regression that matters: a font package dropped from
# the list above, which otherwise surfaces as boxes in somebody's document.
RUN set -eux; \
    fonts="$(typst fonts)"; \
    for family in "Liberation Serif" "DejaVu Sans" "Noto Sans Sinhala" "Noto Sans Tamil"; do \
        echo "${fonts}" | grep -qx "${family}" || { echo "missing font: ${family}" >&2; exit 1; }; \
    done

# `--password '*'`, not the `!` useradd leaves behind.
#
# OpenSSH reads /etc/shadow and refuses an account whose hash begins with `!` as
# locked. It only gets away with not doing so today because a non-root sshd
# cannot read /etc/shadow at all, so the check passes vacuously -- move the
# daemon to root for slot accounts and every login starts failing with "account
# is locked", which says nothing about keys and sends you looking in the wrong
# place. `*` is an invalid hash too, so password authentication remains
# impossible; it simply is not the locked marker.
RUN useradd --create-home --password '*' --uid ${RUNNER_UID} --shell /bin/bash ${RUNNER_USER} \
    && mkdir -p ${WORKSPACE} ${RUNTIME_DIR} \
    && chown ${RUNNER_USER}:${RUNNER_USER} ${WORKSPACE} \
    # root:eagent 0775, so the runtime directory is writable both by a root sshd
    # serving slot accounts and by the unprivileged single-account mode. This is
    # also the ownership a group-owned volume arrives with -- root:1000, mode
    # 0775 -- so both modes agree instead of each needing their own chmod.
    && chown root:${RUNNER_USER} ${RUNTIME_DIR} \
    && chmod 0775 ${RUNTIME_DIR}

# The shell of every slot account, and the reason is in the file: a command the
# application sends to a slot must not run that slot's own ~/.bashrc first, or
# the account being emptied decides whether it is emptied.
COPY docker/runner/slot-shell /usr/local/bin/slot-shell
RUN chmod 0555 /usr/local/bin/slot-shell \
    && [ "$(/usr/local/bin/slot-shell -c 'ulimit -Hu; ulimit -Hv; ulimit -Hf' | tr '\n' ' ')" = "256 1500000 2097152 " ]

# One Linux account per concurrent user, created here rather than on demand.
#
# The root filesystem is read-only at runtime, so `useradd` cannot run then --
# it cannot even lock /etc/passwd. Pre-creating a fixed pool is what lets the
# separation be real UIDs rather than a view: two sessions are two users to the
# kernel, so one cannot read the other's files whatever it does to its own
# namespace. Exceeding the pool needs a new image, which is the cost of keeping
# the root filesystem read-only.
#
# The home directories live under WORKSPACE on purpose. Docker seeds an empty
# named volume from the image, ownership included, so the per-slot directories
# arrive correctly owned without the container ever needing CAP_CHOWN.
RUN set -eux; \
    for i in $(seq 0 $((RUNNER_SLOTS - 1))); do \
        slot="$(printf '%s-%03d' "${RUNNER_USER}" "${i}")"; \
        uid=$((RUNNER_SLOT_BASE_UID + i)); \
        useradd --uid "${uid}" --no-create-home --shell /usr/local/bin/slot-shell --password '*' \
                --home-dir "${WORKSPACE}/${slot}" "${slot}"; \
        install -d -m 0700 -o "${uid}" -g "${uid}" "${WORKSPACE}/${slot}"; \
    done

# A slot's temporary files belong to that slot.
#
# `/tmp` is one tmpfs shared by all 128 accounts, and a shared temp directory is
# necessarily mode 1777 -- anything that lets 128 uids write also lets them read
# each other at the default 022 umask. The 0700 homes isolate `/workspace` and
# nothing else, so agent code writing a CSV to `/tmp` published it to every
# other user on the runner.
#
# Two mitigations, because neither is sufficient alone. `TMPDIR` moves the
# ordinary case inside the slot's own 0700 home, where the kernel already
# separates it. `umask 077` covers the rest: a tool that hardcodes `/tmp`
# instead of honouring TMPDIR now lands 0600 there.
#
# `ssh -tt` with no remote command starts a *login* shell, which is how the
# agent's sessions attach, so profile.d runs for them and everything they spawn
# inherits both settings. `release()`'s wipe empties the home, so TMPDIR is
# recreated per session rather than assumed to survive.
RUN printf '%s\n' \
        'umask 077' \
        'case "$(id -un)" in' \
        '  eagent-*)' \
        '    TMPDIR="$HOME/tmp"' \
        '    mkdir -p "$TMPDIR" 2>/dev/null || TMPDIR=/tmp' \
        '    export TMPDIR' \
        '    TMP="$TMPDIR"; TEMP="$TMPDIR"; export TMP TEMP' \
        '    ;;' \
        'esac' \
    > /etc/profile.d/10-slot-session.sh; \
    chmod 0444 /etc/profile.d/10-slot-session.sh

# Strip every setuid and setgid bit in the image.
#
# allowPrivilegeEscalation:false already neuters these, but that is one flag
# standing between agent-written code and a local privilege escalation. Removing
# the bits means a missing or misapplied flag is not immediately exploitable.
# Nothing here needs them: su, sudo, mount and passwd have no role in a runner
# whose entire job is executing code as one unprivileged account.
RUN find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod ug-s {} + 2>/dev/null || true

# Public key only. Password authentication would require chpasswd, which
# requires root, which is the thing this image is built to avoid -- and a
# password typed into a PTY is weaker than a key besides. The orchestrator
# generates a keypair per runner and injects the public half.
#
# `PermitUserRC no` because it defaults to *yes*: sshd would otherwise execute
# `~/.ssh/rc` on every login. With `StrictModes no` it does not object to the
# file's permissions either, so any account able to write into another's home --
# through the shared `/home/eagent`, or a home a previous occupant left
# world-writable -- could plant a script and have sshd run it as the next user
# to connect. Nothing here uses the feature.
RUN printf '%s\n' \
        "Port ${SSH_PORT}" \
        'PermitRootLogin no' \
        'PasswordAuthentication no' \
        'KbdInteractiveAuthentication no' \
        'PubkeyAuthentication yes' \
        'AuthorizedKeysFile /run/runner/keys/%u/authorized_keys' \
        'AllowUsers eagent eagent-*' \
        'UsePAM no' \
        'StrictModes no' \
        'PermitUserRC no' \
        'X11Forwarding no' \
        'AllowTcpForwarding no' \
        'AllowAgentForwarding no' \
        'PermitTunnel no' \
        'PermitUserEnvironment no' \
        'ClientAliveInterval 30' \
        'HostKey /run/runner/ssh_host_ed25519_key' \
        'PidFile /run/runner/sshd.pid' \
        'LogLevel INFO' \
    > /etc/ssh/sshd_config

COPY docker/runner/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh

# No USER line: sshd starts as root because only root may setuid into a slot
# account, which is the ordinary Unix model and the whole point of the pool. It
# runs with five capabilities and nothing else -- SETUID, SETGID, CHOWN,
# AUDIT_WRITE, SYS_CHROOT; see the compose file --
# and every session it spawns is an unprivileged account with no capabilities.
# ADR 0019 said the runner runs unprivileged; its successor records why the
# daemon is the exception and the sessions are not.
WORKDIR ${WORKSPACE}
EXPOSE 2222
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

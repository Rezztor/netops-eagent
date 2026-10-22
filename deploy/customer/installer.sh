#!/usr/bin/env bash
# Interactive installer for Rezztor EAgent on one host.
#
#   sh install.sh          (from the delivery bundle)
#   less install.sh && bash install.sh
#
# Asks what it needs, generates what it can, and leaves a running deployment
# with the administrator password on screen. Everything it does by hand is in
# INSTALL.md; this only removes the typing.
#
# Read it before you run it. It is served over HTTPS, but a script fetched
# from a host and checked against a hash from that same host is not checked
# at all -- the SHA256 in Rezztor's email is the one that means something,
# because it came a different way:
#
#   sha256sum install.sh
#
# Three principles it follows, each one earned:
#
#   Check before you ask, ask before you pull. The application image is 11 GB.
#   Discovering a full disk or an occupied port after that download is a waste
#   of twenty minutes, so every check that can fail runs first.
#
#   Never overwrite. A second run against an existing directory reconfigures
#   nothing on its own: an existing `.env` or runner key stops it, because the
#   FLASK_SECRET_KEY in that file is what keeps sessions valid and the runner
#   key is what lets the agent execute code at all.
#
#   Verify what started. `docker compose up` succeeding means the containers
#   were created, not that the product runs. This polls /api/health, which is
#   public and reports the version, so the last thing it says is true.
#
# Every prompt can be answered ahead of time with an environment variable, so
# the same script installs unattended:
#
#   REZZTOR_VERSION=2026.09.08 REZZTOR_PORT=50080 \
#   REZZTOR_ADMIN_USER=admin REZZTOR_ADMIN_PASSWORD=... \
#   ./install.sh --unattended
#
# COMPOSE_PROFILES=ansible (or cloud, or both with a comma) also starts that
# executor, now and at every later `docker compose up`, and makes its secrets
# here, so nothing is left to paste into the interface.

set -euo pipefail

# Images are local: loaded from the offline bundle (`docker load`), never pulled.
REGISTRY="rezztor"
DEFAULT_VERSION="2026.09.08"
# Pinned, and kept in step with docker-compose.yml. From Docker Hub rather than
# Rezztor's registry, which is why it needs its own reachability check: the
# per-package access grant that covers our images says nothing about this one.
POSTGRES_IMAGE="pgvector/pgvector:pg17"
# Same reasoning, same source: web search is its own container, and an offline
# bundle carries it too. Kept in step with docker-compose.yml by hand.
SEARXNG_IMAGE="searxng/searxng:2026.8.29-d226b78bc"
DEFAULT_PORT="50080"
# Where the support files come from when they are not already here. Empty by
# default: they travel with this script in the delivery bundle, and the host
# that might serve them instead is a decision deferred until the product is
# ready to deliver. Set it to fetch from a staging host.
INSTALL_BASE="${REZZTOR_INSTALL_URL:-}"
# The Compose file this script expects. Verified after download, so the one
# checksum Rezztor emails for install.sh covers the Compose file too -- there
# is no second hash for anyone to check, or forget to check.
COMPOSE_SHA256="4ef970bcbbf25c16588f675f49522978adeec7b9663f1620199ceb1280c8996a"
# The network guard, fetched and verified the same way, for the same reason. It
# is not run here -- it adds a firewall table on the host, which is the
# customer's call -- but it has to be *present*, because INSTALL.md step 6
# installs `rezztor-guard` from this directory.
GUARD_SHA256="47c755db47dab759214d082b74c3c543033e31122d1452e0143df17876da8370"
# The Compose file pins every container network inside one block,
# <prefix>.248.0/21. Checked against this host's addresses and routes below.
DEFAULT_NET_PREFIX="172.31"
# `gw_priority` in the Compose file pins the application's default route. Older
# versions refuse the file outright, so this is checked before anything else.
MIN_DOCKER="28.0.0"
MIN_COMPOSE="2.33.1"
REQUIRED_GB=37
# Once the images are on the host, all that is still needed is room for the
# data volume. Demanding 35 GB from someone reinstalling on a machine that
# already has the images would be a false alarm, and a loud one.
REQUIRED_GB_IMAGES_PRESENT=7
UNATTENDED=0

# Kept so the script can re-exec itself under a group this session lacks.
SELF="${BASH_SOURCE[0]}"
ARGS=("$@")

[[ "${1:-}" == "--unattended" ]] && UNATTENDED=1

# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; TEAL=$'\033[36m'; OFF=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; TEAL=""; OFF=""
fi

step()  { printf '\n%s==>%s %s%s%s\n' "$TEAL" "$OFF" "$BOLD" "$1" "$OFF"; }
ok()    { printf '    %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
warn()  { printf '    %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
note()  { printf '    %s%s%s\n' "$DIM" "$1" "$OFF"; }
die()   { printf '\n%serror:%s %s\n\n' "$RED" "$OFF" "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

# ask VAR "Question" "default"
# Uses $VAR if it is already set, so every prompt has an environment override
# and --unattended needs no separate code path.
ask() {
    local __var="$1" question="$2" default="${3:-}" reply
    if [[ -n "${!__var:-}" ]]; then
        printf '    %s: %s %s(from the environment)%s\n' "$question" "${!__var}" "$DIM" "$OFF"
        return
    fi
    if (( UNATTENDED )); then
        [[ -n "$default" ]] || die "--unattended, but $__var is unset and has no default"
        printf -v "$__var" '%s' "$default"
        printf '    %s: %s %s(default)%s\n' "$question" "$default" "$DIM" "$OFF"
        return
    fi
    if [[ -n "$default" ]]; then
        read -r -p "    $question [$default]: " reply || true
        printf -v "$__var" '%s' "${reply:-$default}"
    else
        read -r -p "    $question: " reply || true
        printf -v "$__var" '%s' "$reply"
    fi
}

# Never echoed, never in the shell history, never in the process list.
ask_secret() {
    local __var="$1" question="$2" reply
    if [[ -n "${!__var:-}" ]]; then
        printf '    %s: %s(from the environment)%s\n' "$question" "$DIM" "$OFF"
        return
    fi
    (( UNATTENDED )) && die "--unattended, but $__var is unset"
    read -r -s -p "    $question: " reply || true
    echo
    printf -v "$__var" '%s' "$reply"
}

confirm() {
    local reply
    (( UNATTENDED )) && return 0
    read -r -p "    $1 [y/N]: " reply || true
    [[ "$reply" =~ ^[Yy] ]]
}

# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------

# Groups this session actually holds, versus what the account is configured
# with. They differ whenever a group was granted after the session began, and
# that gap is the single most common reason docker refuses a user who is, on
# paper, allowed to use it.
in_group_session() { id -nG 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }
in_group_config()  { id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }

# `docker info` failing has at least four causes and they need four different
# answers. Reporting them all as "is the daemon running?" sends someone to
# restart a service when their actual problem is a group membership that has
# not taken effect -- which is the most common of the four, and the only one
# where the fix is to open a new terminal.
ensure_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        offer_docker_install
    fi

    if docker info >/dev/null 2>&1; then
        ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo present)"
        return
    fi

    local err
    err=$(docker info 2>&1 || true)

    case "$err" in
        *"permission denied"*|*"dial unix"*"permission denied"*)
            # Two different problems wear the same error, and they need
            # opposite answers. Ask the system which one it is rather than
            # guessing: `id -nG` is what this session holds, `id -nG "$USER"`
            # is what the group database says. When docker is in the second
            # and not the first, the account is configured correctly and only
            # this session is stale -- which is provable, not a hunch.
            if in_group_config docker && ! in_group_session docker; then
                warn "Your account is in the docker group, but this session is older than that change."
                note "Sessions are given their groups when they start, so this"
                note "one cannot see it -- and neither can a terminal opened"
                note "from the same login session, which is why \"open a new"
                note "terminal\" is not always enough."
                echo

                if [[ -z "${REZZTOR_GROUP_REEXEC:-}" ]] && command -v sg >/dev/null 2>&1; then
                    if confirm "Carry on in a shell that does have the group?"; then
                        export REZZTOR_GROUP_REEXEC=1
                        local quoted
                        printf -v quoted '%q ' bash "$SELF" "${ARGS[@]}"
                        step "Reopening with the docker group"
                        exec sg docker -c "$quoted"
                    fi
                fi

                die "Fix it in one of these ways, then run this again:

    newgrp docker          starts a shell that has the group, here and now
    log out and back in    of the desktop session, not just this terminal
    reboot                 if in doubt, this always works"
            fi

            die "Docker is installed and running, but this user cannot reach it,
and the docker group is not on this account at all.

    sudo usermod -aG docker $USER

    Then run: newgrp docker
    -- or log out of the desktop session and back in. Opening another
    terminal inside the same session is not enough, because it inherits
    the groups that session started with."
            ;;
        *"Cannot connect to the Docker daemon"*|*"Is the docker daemon running"*)
            if command -v systemctl >/dev/null 2>&1; then
                warn "Docker is installed but the daemon is not running."
                if confirm "Start it now (sudo systemctl enable --now docker)?"; then
                    sudo systemctl enable --now docker || die "Could not start Docker. Try it by hand and read: systemctl status docker"
                    sleep 2
                    docker info >/dev/null 2>&1 \
                        || die "Docker started but still cannot be reached. Read: systemctl status docker"
                    ok "Docker started"
                    return
                fi
            fi
            die "The Docker daemon is not running. Start it, then run this again:
        sudo systemctl enable --now docker"
            ;;
        *)
            die "Docker is installed but did not answer:

$err"
            ;;
    esac
}

# Offered, never automatic. It runs Docker's own installer as root, and
# nobody should discover that after the fact.
offer_docker_install() {
    warn "Docker is not installed."
    note "Rezztor EAgent runs in containers, so Docker is required."
    echo
    note "The installer can run Docker's official script for you:"
    note "    curl -fsSL https://get.docker.com | sudo sh"
    note "It supports Debian, Ubuntu, Fedora, CentOS, RHEL and SUSE. On"
    note "anything else -- or if your organisation installs software its own"
    note "way -- install Docker first and run this again:"
    note "    https://docs.docker.com/engine/install/"
    echo

    confirm "Install Docker now?" || die "Install Docker, then run this again."

    local sudo_cmd=""
    if [[ $EUID -ne 0 ]]; then
        command -v sudo >/dev/null 2>&1 \
            || die "Installing Docker needs root, and sudo is not available. Run this script as root, or install Docker yourself."
        sudo_cmd="sudo"
    fi

    step "Installing Docker"
    note "This takes a few minutes."
    # A name of its own: a fixed one in /tmp is a file another account may have made first and could
    # rewrite between the download and the sudo.
    local get_docker
    get_docker=$(mktemp) || die "Could not make a temporary file for Docker's installer."
    curl -fsSL https://get.docker.com -o "$get_docker" \
        || { rm -f "$get_docker"; die "Could not download Docker's installer. Check this host can reach get.docker.com."; }
    $sudo_cmd sh "$get_docker" || { rm -f "$get_docker"; die "Docker's installer failed. Its output is above."; }
    rm -f "$get_docker"

    command -v systemctl >/dev/null 2>&1 && $sudo_cmd systemctl enable --now docker || true
    ok "Docker installed"

    # Root can carry on. Anyone else has just been given a group they do not
    # yet hold: group membership is set when a session starts, so this shell
    # still cannot reach the daemon however long it waits.
    if [[ $EUID -ne 0 ]]; then
        $sudo_cmd usermod -aG docker "$USER" 2>/dev/null || true
        if ! docker info >/dev/null 2>&1; then
            cat <<EOF

${GREEN}Docker is installed.${OFF} One more step, and it is not optional:

    You have been added to the docker group, but this terminal started
    before that was true and cannot see it. Open a new terminal -- or run
    ${BOLD}newgrp docker${OFF} -- and start this installer again.

EOF
            exit 0
        fi
    fi
}

# ---------------------------------------------------------------------------
# preflight -- everything that can fail, before anything that takes time
# ---------------------------------------------------------------------------

preflight() {
    step "Checking this machine"

    ensure_docker

    # The v2 plugin, not the retired `docker-compose` script. The Compose file
    # uses `${VAR:?message}` defaults that v1 does not understand, so this
    # would otherwise fail later with something unhelpful about YAML.
    docker compose version >/dev/null 2>&1 \
        || die "The Docker Compose plugin is missing. 'docker compose version' must work; the old 'docker-compose' script is not enough.
    On most systems it comes with Docker itself; on Debian and Ubuntu the package is docker-compose-plugin."
    ok "Compose $(docker compose version --short 2>/dev/null || echo present)"

    local have
    have=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
    at_least "${have:-0}" "$MIN_DOCKER" \
        || die "Docker ${have:-(unknown)} is too old. The Compose file needs Docker $MIN_DOCKER or newer."
    have=$(docker compose version --short 2>/dev/null || true)
    at_least "${have#v}" "$MIN_COMPOSE" \
        || die "Docker Compose ${have:-(unknown)} is too old. The Compose file needs $MIN_COMPOSE or newer (package docker-compose-plugin)."

    check_container_block

    for tool in ssh-keygen openssl curl; do
        command -v "$tool" >/dev/null 2>&1 || die "'$tool' is not installed, and the installer needs it."
    done
    ok "ssh-keygen, openssl, curl"

    # The check that actually bites: the application image alone is 11.3 GB,
    # and finding that out after a twenty-minute download helps nobody.
    #
    # Note this measures the install directory, which is where Docker's own
    # storage usually lives -- but not always. If /var/lib/docker is on another
    # filesystem, that is the one that has to hold the images; this check would
    # pass and the pull would still fail. Reported honestly rather than guessed.
    local available_gb needed=$REQUIRED_GB
    if images_present; then
        needed=$REQUIRED_GB_IMAGES_PRESENT
    fi
    available_gb=$(df -Pk . | awk 'NR==2 {printf "%d", $4/1024/1024}')
    if (( available_gb < needed )); then
        if (( needed == REQUIRED_GB )); then
            die "Only ${available_gb} GB free here, and the images need about ${needed} GB. Free some space or install somewhere else."
        fi
        die "Only ${available_gb} GB free here, and the data volume needs about ${needed} GB."
    fi
    if (( needed == REQUIRED_GB_IMAGES_PRESENT )); then
        ok "${available_gb} GB free; the images are already on this host"
    else
        ok "${available_gb} GB free (need ${needed})"
    fi

    local docker_root
    docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
    if [[ -n "$docker_root" ]] && [[ "$(df -Pk . | awk 'NR==2 {print $1}')" != "$(df -Pk "$docker_root" 2>/dev/null | awk 'NR==2 {print $1}')" ]]; then
        local docker_gb
        docker_gb=$(df -Pk "$docker_root" | awk 'NR==2 {printf "%d", $4/1024/1024}')
        if (( docker_gb < needed )); then
            die "Docker stores images under $docker_root, which has only ${docker_gb} GB free. That is the filesystem the images land on, not this one."
        fi
        ok "${docker_gb} GB free on Docker's own filesystem ($docker_root)"
    fi

    if [[ -r /proc/meminfo ]]; then
        local mem_gb
        mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
        if (( mem_gb < 8 )); then
            warn "${mem_gb} GB RAM. 8 GB is the minimum and 16 is comfortable; it will run, slowly."
        else
            ok "${mem_gb} GB RAM"
        fi
    fi

    # The Compose file pins container_name, so one host runs one deployment --
    # a different port is not enough to run a second. Checked here rather than
    # discovered by `docker compose up` after an 11 GB pull, which is how this
    # was found.
    local clash=()
    for name in rezztor-eagent rezztor-eagent-runner rezztor-eagent-postgres; do
        docker container inspect "$name" >/dev/null 2>&1 && clash+=("$name")
    done
    if (( ${#clash[@]} )); then
        die "This host already has a container named ${clash[*]}. One host runs one deployment, whatever port you choose. Remove the old one first (docker compose down in its directory), or install on another host."
    fi
    ok "No conflicting containers"
}

fetch_support_files() {
    step "Deployment files"

    if [[ -f docker-compose.yml ]]; then
        ok "docker-compose.yml is already here"
    else
        fetch_or_die docker-compose.yml
        ok "docker-compose.yml downloaded"
    fi

    local actual
    actual=$(sha256sum docker-compose.yml | awk '{print $1}')
    if [[ "$actual" != "$COMPOSE_SHA256" ]]; then
        die "docker-compose.yml is not the file this installer expects.
    expected $COMPOSE_SHA256
    got      $actual
Delete it and run again to fetch a fresh copy. If it happens twice, stop and tell Rezztor."
    fi
    ok "docker-compose.yml verified"

    fetch_verified rezztor-guard "$GUARD_SHA256"
    chmod +x rezztor-guard
    # No hash on the unit file: it is a systemd unit the customer installs by
    # hand and reads first, and it carries no secret and executes nothing on
    # its own. A missing one is a warning rather than a failure, because a
    # host without systemd does not need it and should not be stopped for it.
    if [[ ! -f rezztor-guard.service ]]; then
        warn "rezztor-guard.service is not here. The guard still applies when you run it; it just will not be loaded again after a restart of this machine until the unit is in place."
    fi
    if [[ ! -f rezztor-guard-runner.service ]]; then
        warn "rezztor-guard-runner.service is not here. Without it the runner's accounts are not kept off its loopback after the runner starts again."
    fi
}

# The one place that reaches for a file this directory does not have. With no
# INSTALL_BASE set -- the default -- a missing file is a plain failure naming
# the file, rather than a download from a host nobody configured.
fetch_or_die() {
    local name="$1"

    [[ -n "$INSTALL_BASE" ]] || die "$name is missing from this directory.
Run the installer from the directory Rezztor sent you, which contains it,
or set REZZTOR_INSTALL_URL to a host serving it."
    note "Fetching $name from $INSTALL_BASE"
    curl -fsSL "$INSTALL_BASE/$name" -o "$name" \
        || die "Could not download $name from $INSTALL_BASE. Check this host can reach it, or copy the file here by hand."
}

# Download once, verify always. Re-verifying a file already here is what makes
# an offline bundle and a re-run take the same path as a fresh download.
fetch_verified() {
    local name="$1" want="$2" actual

    [[ -f "$name" ]] || fetch_or_die "$name"
    actual=$(sha256sum "$name" | awk '{print $1}')
    if [[ "$actual" != "$want" ]]; then
        die "$name is not the file this installer expects.
    expected $want
    got      $actual
Delete it and run again to fetch a fresh copy. If it happens twice, stop and tell Rezztor."
    fi
    ok "$name verified"
}

# A second run must not quietly replace the two files that cannot be
# regenerated. FLASK_SECRET_KEY signs existing sessions and the runner key is
# what the already-running runner has authorised -- and since the database
# arrived, .env also holds the password the data volume was initialised with,
# so replacing it does not sign people out, it locks the deployment out of its
# own accounts and conversations.
check_not_already_installed() {
    local existing=()
    [[ -f .env ]] && existing+=(".env")
    [[ -f runner_key ]] && existing+=("runner_key")
    (( ${#existing[@]} )) || return 0

    step "This looks like an existing installation"
    note "Found: ${existing[*]}"
    echo
    note "Replacing these would sign out every user and lock the application"
    note "out of its own runner. To upgrade instead, edit REZZTOR_VERSION in"
    note ".env and run: docker compose up -d"
    echo
    # Only a person may say yes to this: `confirm` answers yes for an unattended run.
    (( UNATTENDED )) && die "Nothing was changed. --unattended never replaces an installation."
    confirm "Start over anyway, replacing them?" \
        || die "Nothing was changed."
    warn "Replacing ${existing[*]}"
}

# Every image the stack needs, by the name the Compose file asks for. Used
# twice: to size the disk check, and to decide whether there is anything to
# pull at all.
images_present() {
    local version="${REZZTOR_VERSION:-$DEFAULT_VERSION}" profile profiles="${COMPOSE_PROFILES:-}"
    local images=("$REGISTRY/eagent:$version" "$REGISTRY/eagent-runner:$version" "$POSTGRES_IMAGE" "$SEARXNG_IMAGE")
    # An executor that was asked for is part of the stack: without its image
    # `docker compose up` would reach for a registry that is not there.
    for profile in ${profiles//,/ }; do
        images+=("$REGISTRY/eagent-$profile:$version")
    done
    docker image inspect "${images[@]}" >/dev/null 2>&1
}

# The optional executors, by the names the Compose file gives their profiles.
check_profiles() {
    local profile profiles="${COMPOSE_PROFILES:-}"
    for profile in ${profiles//,/ }; do
        [[ "$profile" == ansible || "$profile" == cloud ]] \
            || die "COMPOSE_PROFILES is '$profiles'. It may name ansible, cloud, or both with a comma."
    done
}

# overlaps A/len B/len: true when two IPv4 networks share an address.
ip2int() { local a b c d; IFS=. read -r a b c d <<< "$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }
overlaps() {
    local short=$(( ${1#*/} < ${2#*/} ? ${1#*/} : ${2#*/} ))
    local mask=$(( short == 0 ? 0 : (0xffffffff << (32 - short)) & 0xffffffff ))
    (( ($(ip2int "${1%/*}") & mask) == ($(ip2int "${2%/*}") & mask) ))
}

# at_least HAVE WANT: version comparison, by sort -V.
at_least() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]; }

# The containers' block must not be a network this host already uses: Docker
# would create the bridges without a word, and from then on the host sends that
# range into a container bridge instead of to the customer's network. Read from
# the routing table, which holds every address this host has and every network
# it knows a way to. Our own bridges (rz-*) are not a clash.
#
# A network this host is in, or any route as narrow as the block, stops the
# install. A wider route through a gateway or a tunnel (10.0.0.0/8 to the core
# switch) only warns: the bridges are more specific and still work, and a host
# with such a route would otherwise have no block left to choose.
check_container_block() {
    local prefix="${REZZTOR_NET_PREFIX:-$DEFAULT_NET_PREFIX}" block route connected wide=""
    [[ "$prefix" =~ ^[0-9]{1,3}\.[0-9]{1,3}$ ]] \
        || die "REZZTOR_NET_PREFIX is '$prefix'. It must be the first two numbers of an address, like 172.31 or 10.99."
    block="$prefix.248.0/21"
    if ! command -v ip >/dev/null 2>&1; then
        warn "'ip' is not installed, so $block could not be checked against this host's networks."
        return
    fi
    while read -r route connected; do
        [[ "$route" == */* ]] || route="$route/32"
        overlaps "$block" "$route" || continue
        if (( ${route#*/} >= 21 )) || [[ "$connected" == 1 ]]; then
            die "The containers use $block, and this host already has a network there ($route).
    Choose another block: set REZZTOR_NET_PREFIX to two numbers that are free on your
    network, for example
        REZZTOR_NET_PREFIX=10.99 sh install.sh        (the block is then 10.99.248.0/21)"
        fi
        wide="$route"
    done < <(ip -4 route show table all 2>/dev/null | awk '
        / dev rz-/ { next }
        $1 ~ /^[0-9]/ { print $1, ($0 ~ /proto kernel/ ? 1 : 0) }
        $1 == "local" { print $2, 1 }' | sort -u)
    if [[ -n "$wide" ]]; then
        warn "This host routes $wide elsewhere, and the containers take $block out of it. If you have machines in $block, set REZZTOR_NET_PREFIX."
    else
        ok "Container networks: $block is free on this host"
    fi
}

port_is_free() {
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# ---------------------------------------------------------------------------
# gather
# ---------------------------------------------------------------------------

gather() {
    check_profiles
    step "What to install"
    ask REZZTOR_VERSION "Release version" "$DEFAULT_VERSION"
    [[ "$REZZTOR_VERSION" =~ ^([0-9]{4}\.[0-9]{2}\.[0-9]{2}(-lab|-uat)?|latest)$ ]] \
        || die "'$REZZTOR_VERSION' is not a release. Expected a date like 2026.09.08, or 'latest'."

    # Asked before anything reaches for a registry, because whether a registry
    # is needed depends on the answer. An offline bundle loads its images from
    # media and has no route out; every check below would fail on a host where
    # nothing is missing, which made the one installation path that cannot use
    # a network the one path this script refused to complete.
    if images_present; then
        ok "$REZZTOR_VERSION is already on this host: nothing to sign in to, nothing to pull"
        gather_local_settings
        return
    fi

    die "Release $REZZTOR_VERSION is not on this host.

    Load the images from the offline bundle first:
        docker load -i <bundle>/images/*.tar
    then run this script again."
}

# The half of the questions that have nothing to do with a registry: where it
# listens and who the first administrator is. Its own function because both
# paths through `gather` need it and only one of them talks to a registry.
gather_local_settings() {
    ask REZZTOR_PORT "HTTP port" "$DEFAULT_PORT"
    [[ "$REZZTOR_PORT" =~ ^[0-9]+$ ]] && (( REZZTOR_PORT > 0 && REZZTOR_PORT < 65536 )) \
        || die "'$REZZTOR_PORT' is not a port number."
    port_is_free "$REZZTOR_PORT" \
        || die "Something is already listening on port $REZZTOR_PORT. Pick another, or stop what is there."
    ok "Port $REZZTOR_PORT is free"

    step "Administrator account"
    note "Seeded on first boot only. Change the password in the interface after."
    ask REZZTOR_ADMIN_USER "Username" "admin"
    if [[ -z "${REZZTOR_ADMIN_PASSWORD:-}" ]] && ! (( UNATTENDED )); then
        if confirm "Generate a strong password?"; then
            REZZTOR_ADMIN_PASSWORD="$(openssl rand -base64 24)"
            ok "Generated -- shown at the end"
        else
            ask_secret REZZTOR_ADMIN_PASSWORD "Password (not shown)"
            [[ ${#REZZTOR_ADMIN_PASSWORD} -ge 12 ]] \
                || die "Use at least 12 characters. This account can do anything on the instance."
        fi
    fi
    : "${REZZTOR_ADMIN_PASSWORD:=$(openssl rand -base64 24)}"
}

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

write_config() {
    # Compose reads .env, not a shell: unquoted, `$word` is a variable and ` #` starts a comment, so a typed
    # password would arrive cut short. Single quotes carry every character but these two.
    case "$REZZTOR_ADMIN_USER$REZZTOR_ADMIN_PASSWORD" in
        *"'"*|*$'\n'*) die "The administrator name and password cannot hold a single quote or a line break." ;;
    esac

    step "Writing configuration"

    rm -f runner_key runner_key.pub
    ssh-keygen -t ed25519 -f ./runner_key -N "" -q -C "rezztor-eagent-runner"
    chmod 600 runner_key
    ok "Runner key generated"

    umask 077
    cat > .env <<EOF
# Written by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). See INSTALL.md.
REZZTOR_VERSION=$REZZTOR_VERSION
REZZTOR_PORT=$REZZTOR_PORT
REZZTOR_ADMIN_USER='$REZZTOR_ADMIN_USER'
REZZTOR_ADMIN_PASSWORD='$REZZTOR_ADMIN_PASSWORD'

# Signs session cookies. Never change it: every user is signed out if you do.
FLASK_SECRET_KEY=$(openssl rand -hex 32)

# The database. REZZTOR_DB_PASSWORD owns the data and is what migrations run
# as; REZZTOR_DB_APP_PASSWORD is the role the application serves requests as,
# which cannot bypass row-level security. Never change either by hand: the
# first is what the existing volume was initialised with, and changing it
# locks you out of your own data.
REZZTOR_DB_PASSWORD=$(openssl rand -hex 24)
REZZTOR_DB_APP_PASSWORD=$(openssl rand -hex 24)

# Signs SearXNG's own cookies. The search engine runs in its own container,
# and refuses to start without this rather than falling back to a shipped
# default. Nothing of yours is signed with it, so it is safe to rotate.
REZZTOR_SEARXNG_SECRET=$(openssl rand -hex 32)

# Azure changes (optional; the profile named cloud). The two tokens must differ.
# KEY seals the Azure keys at rest and PASSPHRASE encrypts OpenTofu's state: back
# up this file, because without them the state and the keys cannot be opened.
# Never change them by hand once the executor has made a key or a state.
REZZTOR_CLOUD_JOB_TOKEN=$(openssl rand -hex 32)
REZZTOR_CLOUD_ADMIN_TOKEN=$(openssl rand -hex 32)
REZZTOR_CLOUD_KEY=$(openssl rand -base64 32)
REZZTOR_CLOUD_STATE_PASSPHRASE=$(openssl rand -hex 32)
REZZTOR_DB_CLOUD_PASSWORD=$(openssl rand -hex 24)

# Public half of the runner key. The private half is ./runner_key.
RUNNER_AUTHORIZED_KEY=$(cat runner_key.pub)
EOF
    # Only when asked for, so a hand install's .env is what it always was. It
    # has to be in the file and not only in this shell: a later `docker compose
    # up` by hand would otherwise publish the port on every address again.
    if [[ -n "${REZZTOR_BIND_ADDRESS:-}" ]]; then
        printf '\n# The host address the HTTP port is published on.\nREZZTOR_BIND_ADDRESS=%s\n' "$REZZTOR_BIND_ADDRESS" >> .env
    fi
    # The proxy whose X-Forwarded-For the product believes. The same rule.
    if [[ -n "${FORWARDED_ALLOW_IPS:-}" ]]; then
        printf '\n# The proxy on this host whose X-Forwarded-For is believed.\nFORWARDED_ALLOW_IPS=%s\n' "$FORWARDED_ALLOW_IPS" >> .env
    fi
    # The same rule for the container block: only when it is not the default,
    # and in the file, so a later `docker compose up` creates the same networks.
    if [[ -n "${REZZTOR_NET_PREFIX:-}" ]]; then
        printf '\n# The container networks are %s.248.0/21.\nREZZTOR_NET_PREFIX=%s\n' "$REZZTOR_NET_PREFIX" "$REZZTOR_NET_PREFIX" >> .env
    fi
    # And for the executors that were asked for: Compose reads this name from
    # the file, so a later `docker compose up` starts the same containers.
    if [[ -n "${COMPOSE_PROFILES:-}" ]]; then
        printf '\n# The optional executors that run on this host.\nCOMPOSE_PROFILES=%s\n' "$COMPOSE_PROFILES" >> .env
    fi
    # The secrets of network automation, only with its executor: the application
    # switches the plugin on by itself when it is given a job token, and a token
    # with no executor behind it would switch on a plugin that cannot work.
    if [[ ",${COMPOSE_PROFILES:-}," == *,ansible,* ]]; then
        cat >> .env <<EOF

# Network automation. The two tokens must differ. CREDENTIAL_KEY encrypts the
# device credentials the executor stores: back up this file, because without
# it every stored credential must be entered again. Never change it by hand
# once a credential is stored.
REZZTOR_ANSIBLE_EXECUTOR_TOKEN=$(openssl rand -hex 32)
REZZTOR_ANSIBLE_ADMIN_TOKEN=$(openssl rand -hex 32)
REZZTOR_ANSIBLE_CREDENTIAL_KEY=$(openssl rand -base64 32)
EOF
    fi
    chmod 600 .env
    ok ".env written (mode 600)"
}

start() {
    # An offline bundle loads its images with `docker load` before this runs,
    # and there is no registry to reach -- so pulling unconditionally turned
    # the one installation path that cannot use a network into one that
    # required it. Nothing to pull is a state, not a failure.
    if images_present; then
        step "Images"
        ok "Already on this host; nothing to pull"
        step "Starting"
        docker compose up -d 2>&1 | sed 's/^/    /'
        return
    fi

    die "Images are not on this host. Load them with docker load first."
}

# `docker compose up` returning 0 means the containers were created. It says
# nothing about whether the product came up, which is what anyone actually
# wants to know -- so wait for the health endpoint, which is public and names
# the running version.
wait_until_healthy() {
    step "Waiting for it to come up"
    note "First boot initialises the database and warms caches."

    local deadline=$((SECONDS + 300)) body=""
    while (( SECONDS < deadline )); do
        if body=$(curl -fsS --max-time 5 "http://127.0.0.1:$REZZTOR_PORT/api/health" 2>/dev/null); then
            local running
            running=$(printf '%s' "$body" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')
            ok "Up, running ${running:-an unreported version}"
            if [[ -n "$running" && "$REZZTOR_VERSION" != "latest" && "$running" != "$REZZTOR_VERSION" ]]; then
                warn "Asked for $REZZTOR_VERSION but $running started. Check REZZTOR_VERSION in .env."
            fi
            return 0
        fi
        if [[ -z "$(docker compose ps -q eagent 2>/dev/null)" ]]; then
            die "The application container stopped. See: docker compose logs eagent"
        fi
        printf '.'
        sleep 5
    done
    echo
    die "It did not answer within 5 minutes. See: docker compose logs eagent"
}

summary() {
    local host_ip
    host_ip=$(hostname -I 2>/dev/null | awk '{print $1}') || true   # -I is Debian's and Red Hat's; not an error to lack it
    cat <<EOF

$(printf '%s' "$GREEN")Rezztor EAgent is running.$(printf '%s' "$OFF")

    ${BOLD}Address${OFF}    http://${host_ip:-localhost}:$REZZTOR_PORT/app
    ${BOLD}Username${OFF}   $REZZTOR_ADMIN_USER
    ${BOLD}Password${OFF}   $REZZTOR_ADMIN_PASSWORD

    ${DIM}The password is in .env too, and seeds the first boot only.${OFF}

${BOLD}Next${OFF}
    1. Load the network guard, before anyone uses it (INSTALL.md, section 6).
       Until then the runner can reach every network this host can.
    2. Sign in and change the administrator password (Users)
    3. Activate the licence          (Settings -> Licence)
    4. Point it at a model           (Models)

${BOLD}Keep${OFF}
    .env and runner_key are not in the Docker volume, and you need both to
    rebuild this deployment. Back them up somewhere other than this host.
    If you run the Azure changes, .env also holds the passphrase that opens
    their state and the key that seals their credentials: lose those and the
    state cannot be read again. The same file holds the key that encrypts the
    device credentials of network automation.

EOF
}

main() {
    printf '\n%sRezztor EAgent installer%s\n' "$BOLD" "$OFF"
    note "Installing into $(pwd)"
    preflight
    fetch_support_files
    check_not_already_installed
    gather
    write_config
    start
    wait_until_healthy
    summary
}

main "$@"

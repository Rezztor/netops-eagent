#!/bin/bash

echo "Running initialization script..."

# branch from parameter
if [ -z "$1" ]; then
    echo "Error: Branch parameter is empty. Please provide a valid branch name."
    exit 1
fi
BRANCH="$1"

raise_open_file_limit() {
    local requested="${REZZTOR_NOFILE_LIMIT:-65535}"
    local soft
    local hard
    local target

    if ! [[ "$requested" =~ ^[0-9]+$ ]] || [ "$requested" -lt 1 ]; then
        echo "Warning: invalid REZZTOR_NOFILE_LIMIT='$requested'; keeping open file limit at $(ulimit -S -n)." >&2
        return
    fi

    soft="$(ulimit -S -n)"
    hard="$(ulimit -H -n)"

    if [ "$soft" = "unlimited" ]; then
        echo "Open file limit is already unlimited."
        return
    fi

    target="$requested"
    if [ "$hard" != "unlimited" ] && [ "$target" -gt "$hard" ]; then
        target="$hard"
    fi

    if [ "$target" -gt "$soft" ]; then
        if ulimit -S -n "$target"; then
            echo "Raised open file soft limit from $soft to $(ulimit -S -n) (hard: $hard)."
        else
            echo "Warning: failed to raise open file soft limit from $soft to $target (hard: $hard)." >&2
        fi
    else
        echo "Open file soft limit is $soft (target: $requested, hard: $hard)."
    fi
}

raise_open_file_limit

if [ "$(id -u)" = "0" ]; then
    # Root only long enough to hand the application its writable places, then
    # this script again as the eagent account, which is what serves (ADR 0022,
    # amended 2026-10-16). The compose files give this container CHOWN,
    # DAC_READ_SEARCH, SETUID and SETGID and nothing else, with
    # no-new-privileges, and the application holds none of them.
    #
    # Every installation before then ran the application as root, so its usr
    # volume holds root's files; the update that brings this image is run by
    # the old update tool, which knows nothing of it, so the image fixes it
    # here, on every start. -h: a link is changed, never what it points to,
    # and the volume is its own file system, so no hard link leads out of it.
    find /eagent/usr -xdev ! -user eagent -exec chown -h eagent:eagent {} +
    # The update inbox (ADR 0080) is a folder of the host, root's and 0700 on
    # an appliance installed before this; root:eagent 0770 lets the
    # application write its one file and one request and nothing more.
    inbox=/run/rezztor-update/inbox
    if [ -d "$inbox" ] && [ ! -L "$inbox" ]; then
        find "$inbox" -xdev -mindepth 1 ! -user eagent -exec chown -h eagent:eagent {} +
        chown root:eagent "$inbox" && chmod 0770 "$inbox"
    fi
    # The container's log is this process's stdout and stderr, pipes owned by
    # root 0600. runc hands them to the user of a container that starts
    # unprivileged; this one starts as root, so it is done here, or the
    # console's audit line (helpers/console_admin.py writes to /proc/1/fd/1)
    # could no longer reach the log.
    for fd in 1 2; do
        if [ -p "/proc/self/fd/$fd" ]; then chown eagent:eagent "/proc/self/fd/$fd"; fi
    done
    export HOME=/home/eagent USER=eagent LOGNAME=eagent
    exec setpriv --reuid=eagent --regid=eagent --clear-groups --no-new-privs -- /exe/initialize.sh "$BRANCH"
fi

# Ensure upload storage exists before API and connector callers can reference it.
mkdir -p /eagent/usr/uploads

# Unprivileged: one process, no supervisor.
#
# supervisord refuses to start as a non-root user ("Can't drop privilege as
# nonroot user"), which is what crash-looped the first unprivileged deploy. The
# workload wants to be PID 1 regardless: Docker's own restart policy restarts a
# container that exits, which is the job supervisord was doing.
#
# This used to add "SearXNG is its own Deployment there" as the reason there was
# nothing left to supervise. That was not true when it was written -- the chart
# shipped no such Deployment, so search worked on Compose and silently failed
# here. It is true now.
echo "Starting unprivileged as uid $(id -u)."
exec /exe/run_eagent.sh "$BRANCH"

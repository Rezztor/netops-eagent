#!/bin/bash
set -e

. "/ins/setup_venv.sh" "$@"
. "/ins/copy_eagent.sh" "$@"

# Launched directly. Upstream started the app through self_update_manager.py,
# which git-cloned Rezztor/netops-eagent and rewrote the running code in place.
# Rezztor EAgent updates by rolling a new container image instead, so the
# application is the container's only process of record.
cd /eagent

# 8080, not 80: the application never runs as root (initialize.sh), and a port
# below 1024 would need a capability it does not hold. The host's port is the
# compose file's business; both compose files map it to this one.
PORT="${WEB_UI_PORT:-8080}"
HOST="${WEB_UI_HOST:-0.0.0.0}"

python /eagent/prepare.py --dockerized=true
exec python /eagent/run_ui.py --dockerized=true --port="$PORT" --host="$HOST"

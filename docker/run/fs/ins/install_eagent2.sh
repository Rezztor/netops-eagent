#!/bin/bash
set -e

# Cachebuster: re-runs the install so a source change invalidates the layer.
# Upstream also deleted and re-cloned the repo here; there is nothing to
# re-clone now that the source arrives with the build context, and removing it
# would destroy the only copy.

bash /ins/install_eagent.sh "$@"

# remove python packages cache
. "/ins/setup_venv.sh" "$@"
pip cache purge
uv cache prune

#!/bin/bash
set -e

# Build the web frontend into frontend/dist.
#
# Load-bearing since REZZTOR_UI defaults to the built frontend: without this the
# image starts, warns, and serves the inherited interface instead -- a
# deployment that looks like it worked and is running the wrong thing.
#
# Node is installed here rather than in the base image because it is a build
# dependency, not a runtime one: the output is static files, and nothing in the
# running container needs node afterwards.

SOURCE_DIR="/git/eagent"
FRONTEND_DIR="$SOURCE_DIR/frontend"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
  echo "No frontend/package.json; skipping the frontend build."
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Installing Node.js for the frontend build..."
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
fi

cd "$FRONTEND_DIR"
# `npm ci` rather than `install`: the lockfile is the contract, and a build that
# silently resolves different versions is not reproducible.
npm ci --no-audit --no-fund
npm run build

# The build artefact is all that is needed at runtime.
rm -rf "$FRONTEND_DIR/node_modules"

echo "Frontend built:"
ls -la "$FRONTEND_DIR/dist"

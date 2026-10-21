# The throwaway container the build tools run in: scripts/build_iso.sh (the package folder, the ISO) and
# scripts/sign_release.py (a package's Debian updates). TOOLS_BASE is pinned in pins.env.
ARG TOOLS_BASE
FROM ${TOOLS_BASE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends xorriso apt-utils ca-certificates curl gpg \
 && rm -rf /var/lib/apt/lists/*

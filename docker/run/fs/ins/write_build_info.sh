#!/bin/bash
set -e

# Stamp the build's version into the image.
#
# .git is excluded from the Docker build context (correctly -- it is large and
# carries the whole history), so a delivered instance cannot derive its own
# version from the repository. helpers/git.py falls back to this file.
#
# Values come from build args rather than from git, because git is not
# available here by design. The publish workflow passes the release version;
# a local build without them produces a file that honestly says "unknown"
# rather than a wrong or invented version.

TARGET="${BUILD_INFO_TARGET:-/git/eagent/build_info.json}"

BUILD_VERSION="${BUILD_VERSION:-unknown}"
BUILD_COMMIT="${BUILD_COMMIT:-}"
BUILD_TIME="${BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

# A lab build has the licence check off, for the owner's own test lab (ADR 0082).
# It says so in its version, so that it cannot pass for a release: the image
# tag, the bundle, the ISO's name and every screen that shows the version carry it.
#
# A UAT build trusts the dummy licence root, server and CA certificate it is
# given in BUILD_UAT_TRUST, and no other (ADR 0085). It says so in its version
# the same way, with -uat, and the trust is written beside the stamp.
LICENCE_LINE=""
TRUST_FILE="$(dirname "$TARGET")/licence_trust.json"
rm -f "$TRUST_FILE"
if [[ -n "${BUILD_UAT_TRUST:-}" && "${BUILD_LICENCE:-}" != uat ]]; then
    echo "error: BUILD_UAT_TRUST is only for BUILD_LICENCE=uat" >&2
    exit 1
fi
if [[ "${BUILD_LICENCE:-}" == uat ]]; then
    if [[ "$BUILD_VERSION" != *-uat || -z "${BUILD_UAT_TRUST:-}" ]]; then
        echo "error: BUILD_LICENCE=uat needs a BUILD_VERSION that ends in -uat, and BUILD_UAT_TRUST" >&2
        exit 1
    fi
    printf '%s\n' "$BUILD_UAT_TRUST" > "$TRUST_FILE"
    LICENCE_LINE='"licence": "uat",'
elif [[ -n "${BUILD_LICENCE:-}" ]]; then
    if [[ "$BUILD_LICENCE" != open || "$BUILD_VERSION" != *-lab ]]; then
        echo "error: BUILD_LICENCE can only be 'open', and only with a BUILD_VERSION that ends in -lab (or uat, ADR 0085)" >&2
        exit 1
    fi
    LICENCE_LINE='"licence": "open",'
fi

cat > "$TARGET" <<EOF
{
  "version": "$BUILD_VERSION",
  "short_tag": "$BUILD_VERSION",
  "tag": "$BUILD_VERSION",
  "commit_hash": "$BUILD_COMMIT",
  "commit_time": "$BUILD_TIME",
  $LICENCE_LINE
  "branch": ""
}
EOF

case "${BUILD_LICENCE:-}" in
    open) NOTE=" LAB BUILD, licence check off" ;;
    uat) NOTE=" UAT BUILD, trusts the dummy licence root in $TRUST_FILE only" ;;
    *) NOTE="" ;;
esac
echo "Stamped build info: version=$BUILD_VERSION commit=${BUILD_COMMIT:-none}$NOTE"

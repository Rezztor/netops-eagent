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

# A release image is Enterprise: the stamp says so, and the licence check stays on.
# A source checkout and a self-built image write no such line and are Community.
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
elif [[ "${BUILD_LICENCE:-}" == enterprise ]]; then
    if [[ "$BUILD_VERSION" == *-lab || "$BUILD_VERSION" == *-uat ]]; then
        echo "error: BUILD_LICENCE=enterprise is a release build; the version must not end in -lab or -uat" >&2
        exit 1
    fi
    LICENCE_LINE='"licence": "enterprise",'
elif [[ -n "${BUILD_LICENCE:-}" ]]; then
    echo "error: BUILD_LICENCE is 'enterprise' or 'uat', or unset for a Community image" >&2
    exit 1
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
    enterprise) NOTE=" ENTERPRISE BUILD, licence check on" ;;
    uat) NOTE=" UAT BUILD, trusts the dummy licence root in $TRUST_FILE only" ;;
    *) NOTE="" ;;
esac
echo "Stamped build info: version=$BUILD_VERSION commit=${BUILD_COMMIT:-none}$NOTE"

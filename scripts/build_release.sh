#!/usr/bin/env bash
# Build what a customer receives, from a tag and from nothing else (ADR 0084).
#
#   scripts/build_release.sh v2026.10.13
#       a core ISO alone (the first core, when there is no older core to bridge from)
#   scripts/build_release.sh core v2027.01.15 --bridge-from 2027.01.08 --key PEM --recipients FILE [--note TEXT]...
#       a new core: its ISO and, from the same images, the bridge package that takes an installed machine
#       from the last package of the old core (--bridge-from) to this core, every image whole
#   scripts/build_release.sh package v2026.10.21 --base 2026.10.19 (--min-from 2026.10.20 | --first) \
#           --key PEM --recipients FILE [--note TEXT]...
#       an update package on the core --base; --min-from is the package before it, and --first says it is
#       the first package of that core (sign_release.py refuses a package with neither)
#
#   Both package modes take --os-snapshot YYYYMMDDTHHMMSSZ (Debian's updates since the core, from Debian's
#   snapshot archive at that time, ADR 0092), --os-core CORE (a bridge: the old core) and --format 3 (for
#   machines whose update tool is older than ADR 0092), passed to sign_release.py as they are.
#
#   REZZTOR_ISO_MARK=EXBANK scripts/build_release.sh v2026.10.13      marked for one customer
#   REZZTOR_ISO_OPEN_CONSOLE=1 scripts/build_release.sh v2026.10.13   an open console, said out loud
#
# The tag is cloned fresh from the remote into $WORK/release/<tag>, so the build
# sees tracked files and nothing else: no working folder, no untracked file, no
# local setting. Every image is built there under the version as its tag (the
# development images tagged latest are left alone), and that clone's
# scripts/build_iso.sh writes $WORK/out/rezztor-eagent-<version>.iso, which it
# refuses to do from anywhere but a clean checkout of the tag. A package is
# signed by that clone's scripts/sign_release.py into $WORK/out. The image it is
# compared with (the core, or for a bridge the last package of the old core)
# must be on this machine under its version: keep the images of every release.
# The recipients file names the portal's distribution key (the update service
# re-encrypts for each installation). REZZTOR_RELEASE_PYTHON names a Python
# that has `cryptography` (default python3).
#
# A tag is `v` and the version, and a version is a date (the bundle and the
# update tool hold it to YYYY.MM.DD; a package may add .N for a second one the
# same day): v2026.10.13 builds version 2026.10.13. Push the tag first; what is
# not on the remote is not a release. A release never ends in -uat (ADR 0085).
#
# Test the result as a customer gets it: deploy/iso/test/test-iso.sh <iso> bios

set -euo pipefail

usage() {
    echo "usage: scripts/build_release.sh TAG" >&2
    echo "       scripts/build_release.sh core TAG --bridge-from VERSION --key PEM --recipients FILE [--note TEXT]..." >&2
    echo "       scripts/build_release.sh package TAG --base CORE (--min-from VERSION | --first) --key PEM --recipients FILE [--note TEXT]..." >&2
    echo "TAG is a pushed tag, v and the version, which is a date: v2026.10.13" >&2
    exit 1
}

MODE=iso
case "${1:-}" in core | package) MODE="$1"; shift ;; esac
TAG="${1:-}"
[[ $# -gt 0 ]] && shift
DATE='[0-9]{4}\.[0-9]{2}\.[0-9]{2}'
CORE_RE="^$DATE\$"
VERSION_RE="^$DATE(\.[0-9]{1,2})?\$"
if [[ "$MODE" == package ]]; then [[ "$TAG" =~ ^v && "${TAG#v}" =~ $VERSION_RE ]] || usage
else [[ "$TAG" =~ ^v && "${TAG#v}" =~ $CORE_RE ]] || usage; fi
VERSION="${TAG#v}"

BRIDGE_FROM="" BASE="" MIN_FROM="" FIRST="" KEY="" RECIPIENTS=""
NOTES=() OS=()
while [[ $# -gt 0 ]]; do
    [[ "$1" == --first || $# -ge 2 ]] || usage
    case "$1" in
        --bridge-from) BRIDGE_FROM="$2" ;;
        --base) BASE="$2" ;;
        --min-from) MIN_FROM="$2" ;;
        --first) FIRST=1; shift; continue ;;
        --key) KEY="$(realpath -e "$2")" || usage ;;
        --recipients) RECIPIENTS="$(realpath -e "$2")" || usage ;;
        --note) NOTES+=(--note "$2") ;;
        --os-snapshot | --os-core | --format) OS+=("$1" "$2") ;;
        *) usage ;;
    esac
    shift 2
done
SIGN=()
case "$MODE" in
    iso)
        [[ -z "$BRIDGE_FROM$BASE$MIN_FROM$FIRST$KEY$RECIPIENTS${NOTES[*]}${OS[*]}" ]] || usage ;;
    core)
        # A bridge's min_from is the last package of the old core: an installed machine walks to it first.
        [[ "$BRIDGE_FROM" =~ $VERSION_RE && -n "$KEY" && -n "$RECIPIENTS" && -z "$BASE$MIN_FROM$FIRST" ]] || usage
        SIGN=(--base "$VERSION" --version "$VERSION" --min-from "$BRIDGE_FROM") ;;
    package)
        [[ "$BASE" =~ $CORE_RE && -n "$KEY" && -n "$RECIPIENTS" && -z "$BRIDGE_FROM" ]] || usage
        if [[ -n "$FIRST" && -z "$MIN_FROM" ]]; then
            SIGN=(--base "$BASE" --version "$VERSION" --first)
        elif [[ -z "$FIRST" && "$MIN_FROM" =~ $VERSION_RE ]]; then
            SIGN=(--base "$BASE" --version "$VERSION" --min-from "$MIN_FROM")
        else
            usage
        fi ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/.."
WORK="${REZZTOR_ISO_WORK:-$HOME/iso-build}"
# REZZTOR_RELEASE_REMOTE: another place to clone from, for trying this script before a tag is pushed.
REMOTE="${REZZTOR_RELEASE_REMOTE:-$(git remote get-url origin)}"
SRC="$WORK/release/$TAG"
PYTHON="${REZZTOR_RELEASE_PYTHON:-python3}"

if [[ "$MODE" != iso ]]; then
    # The image the package is compared with, said before half an hour of building.
    since="${BRIDGE_FROM:-$BASE}"
    if ! docker image inspect "rezztor/eagent:$since" > /dev/null 2>&1; then
        echo "error: rezztor/eagent:$since is not on this machine, and the package is made by comparing with it." >&2
        echo "Load the images of $since (keep the images of every release you hand out)." >&2
        exit 1
    fi
fi

echo "==> Fresh clone of $TAG from $REMOTE"
rm -rf "$SRC"
mkdir -p "$WORK/release"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" "$REMOTE" "$SRC"
cd "$SRC"
if [[ "$(git describe --exact-match --tags HEAD 2>/dev/null)" != "$TAG" ]]; then
    echo "error: $TAG is not a tag on $REMOTE (a branch of that name does not count)." >&2
    exit 1
fi
echo "    commit $(git rev-parse HEAD)"
if [[ "$MODE" != package ]]; then
    # scripts/build_iso.sh is what refuses a release with an open console nobody asked for. Said here as
    # well, before half an hour of building images.
    if [[ -z "${REZZTOR_ISO_OPEN_CONSOLE:-}${REZZTOR_ISO_SUPPORT_KEYS:-}" ]] && ! compgen -G "deploy/iso/keys/support/*.pub" > /dev/null; then
        echo "error: $TAG has no support key (deploy/iso/keys/support/*.pub), so its console would be open." >&2
        echo "Make the key (deploy/iso/keys/README.md), or set REZZTOR_ISO_OPEN_CONSOLE=1 to build it open." >&2
        exit 1
    fi
    if [[ -z "${REZZTOR_ISO_NO_UPDATES:-}" ]] && ! compgen -G "deploy/iso/keys/*.pub" > /dev/null; then
        echo "error: $TAG has no release key (deploy/iso/keys/*.pub), so no update could ever be installed on it." >&2
        echo "Make the two keys (deploy/iso/keys/README.md), or set REZZTOR_ISO_NO_UPDATES=1 to build it so." >&2
        exit 1
    fi
fi

# A release is never a lab build, a UAT build or a stand-in, and carries the release keys of its tag,
# whatever this shell has set (ADR 0082, 0085).
unset BUILD_LICENCE REZZTOR_ISO_LAB REZZTOR_ISO_STANDIN REZZTOR_UAT_TRUST REZZTOR_ISO_UAT REZZTOR_ISO_RELEASE_KEYS
REZZTOR_IMAGE_TAG="$VERSION" BUILD_VERSION="$VERSION" scripts/build_local.sh base app runner ansible cloud
if [[ "$MODE" != package ]]; then
    REZZTOR_ISO_RELEASE=1 REZZTOR_ISO_VERSION="$VERSION" REZZTOR_ISO_WORK="$WORK" scripts/build_iso.sh --refresh-payload
fi
if [[ "$MODE" != iso ]]; then
    echo "==> Package $VERSION$([[ "$MODE" == core ]] && echo ", the bridge from $BRIDGE_FROM")"
    "$PYTHON" scripts/sign_release.py package "${SIGN[@]}" --key "$KEY" --recipients "$RECIPIENTS" \
        --out "$WORK/out" "${NOTES[@]}" "${OS[@]}"
fi

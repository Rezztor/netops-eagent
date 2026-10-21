#!/usr/bin/env bash
# Build the TEST ISO: Debian's installer image with our payload added.
#
#   ./scripts/build_iso.sh                    build; reuses every download
#   ./scripts/build_iso.sh --refresh-payload  the product images changed:
#                                             remake the bundle, keep the rest
#   REZZTOR_ISO_STANDIN=1 ./scripts/build_iso.sh
#       the same ISO with tiny stand-in images under the real names, written
#       as rezztor-eagent-STANDIN-<yyyymmdd>.iso. Only for deploy/iso/test/
#       test-iso.sh on a machine without KVM. It is not the product.
#
#   REZZTOR_ISO_UAT=1 REZZTOR_ISO_RELEASE_KEYS=<dir> ./scripts/build_iso.sh --refresh-payload
#       a UAT ISO, rezztor-eagent-UAT-<yyyymmdd>.iso, from rezztor/eagent:uat
#       (ADR 0085), carrying the dummy release keys in <dir> instead of ours.
#
#   scripts/build_release.sh TAG              what a customer receives: this
#       script run in a fresh clone of a tag, written as
#       rezztor-eagent-<version>.iso (ADR 0084). Not started by hand.
#
# Output: $WORK/out/rezztor-eagent-TEST-<yyyymmdd>.iso and its .sha256
# (WORK defaults to ~/iso-build; nothing is written into the repository
# except deploy/iso/os-packages.sha256 on the very first build).
#
# The ISO is a carrier, not a second install path: it holds the offline bundle
# exactly as scripts/build_offline_bundle.sh makes it, and the installed
# machine runs that bundle's install.sh. deploy/iso/AGENTS.md has the layout.
#
# The build tools (xorriso, apt) run in a throwaway container, so nothing is
# installed on this machine. It needs docker, curl and sha256sum, and the
# release images present locally (rezztor/eagent:latest and the other three).
# What is fetched is pinned in deploy/iso/pins.env. Nothing secret is made.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO=$PWD
# shellcheck source=deploy/iso/pins.env
. deploy/iso/pins.env

WORK="${REZZTOR_ISO_WORK:-$HOME/iso-build}"
VERSION="${REZZTOR_ISO_VERSION:-$(date +%Y.%m.%d)}"
IMAGE_TAG="${REZZTOR_IMAGE_TAG:-latest}"
REFRESH=0
[[ "${1:-}" == "--refresh-payload" ]] && REFRESH=1
KIND=TEST
if [[ -n "${REZZTOR_ISO_STANDIN:-}" ]]; then
    KIND=STANDIN
    # `docker save` in the bundle script now gives stand-in images.
    PATH="$REPO/deploy/iso/test/standin:$PATH"
fi
if [[ -n "${REZZTOR_ISO_LAB:-}" ]]; then
    # The owner's own test lab: the licence check is off (ADR 0082). The application image is
    # rezztor/eagent:lab, the other three are as they are, and the version ends in -lab.
    KIND=LAB
    VERSION="${VERSION%-lab}-lab"
fi
if [[ -n "${REZZTOR_ISO_UAT:-}" ]]; then
    # A production-shaped UAT environment (ADR 0085): the application image is rezztor/eagent:uat, which
    # trusts only a dummy licence root and the UAT licence server, and the version ends in -uat.
    if [[ "$KIND" != TEST ]]; then
        echo "error: a UAT build is not a $KIND build." >&2
        exit 1
    fi
    KIND=UAT
    VERSION="${VERSION%-uat}-uat"
fi
if [[ -n "${REZZTOR_ISO_RELEASE:-}" ]]; then
    # What a customer receives (ADR 0084). Only from a clean checkout of the tag v<version>, which is
    # what scripts/build_release.sh makes: a working folder holds what no tag does, and an ISO built
    # from one cannot be built again.
    if [[ "$KIND" != TEST ]]; then
        echo "error: a release is not a $KIND build." >&2
        exit 1
    fi
    if [[ -n "${REZZTOR_ISO_RELEASE_KEYS:-}" ]]; then
        echo "error: a release carries the release keys of its tag (deploy/iso/keys/*.pub), not REZZTOR_ISO_RELEASE_KEYS." >&2
        exit 1
    fi
    KIND=RELEASE
    IMAGE_TAG="$VERSION"
    if [[ -n "$(git status --porcelain)" \
          || "$(git rev-parse HEAD)" != "$(git rev-parse -q --verify "refs/tags/v$VERSION^{commit}")" ]]; then
        echo "error: a release is built from a clean checkout of the tag v$VERSION and this is not one." >&2
        echo "Run scripts/build_release.sh v$VERSION, which clones the tag and builds there." >&2
        exit 1
    fi
fi
# Whose build this is, when it is made for one customer: a short code the owner keeps a list of, never
# a name (ADR 0083). It is in the file name, on the ISO, and on the status screen of the machine.
MARK="${REZZTOR_ISO_MARK:-}"
if [[ -n "$MARK" && ! "$MARK" =~ ^[A-Z0-9]{4,16}$ ]]; then
    echo "error: REZZTOR_ISO_MARK is 4 to 16 capital letters and digits, a code and not a name." >&2
    exit 1
fi
OUT="$WORK/out/rezztor-eagent-$KIND-$(date +%Y%m%d)${MARK:+-$MARK}.iso"
# A release is named by its version: the same tag gives the same name on any day.
if [[ "$KIND" == RELEASE ]]; then OUT="$WORK/out/rezztor-eagent-$VERSION${MARK:+-$MARK}.iso"; fi
TOOLS=rezztor-iso-tools:local
LOCK=deploy/iso/os-packages.sha256

step() { printf '\n==> %s\n' "$1"; }

# Docker's storage on a btrfs development machine sometimes answers
# "input/output error"; the same command then works the second time.
retry() {
    local n
    for n in 1 2 3; do
        "$@" && return 0
        echo "    attempt $n failed: $*" >&2
        sleep 5
    done
    return 1
}

in_tools() {
    docker run --rm -e DOCKER_KEY_FPR -e DEBIAN_ISO -e VERSION -e PACKAGES -e KIND \
        -v "$REPO:/repo:ro" -v "$WORK:/work" "$TOOLS" \
        bash -euo pipefail -c "$1; chown -R $(id -u):$(id -g) /work/cache /work/out /work/work"
}

mkdir -p "$WORK/cache" "$WORK/out" "$WORK/work"
export DOCKER_KEY_FPR DEBIAN_ISO VERSION KIND

step "Build tools container"
retry docker build -q -t "$TOOLS" --build-arg TOOLS_BASE="$TOOLS_BASE" - < deploy/iso/tools.Dockerfile

step "Debian installer image: $DEBIAN_ISO"
iso_ok() { echo "$DEBIAN_ISO_SHA256  $WORK/cache/$DEBIAN_ISO" | sha256sum -c --quiet 2>/dev/null; }
if ! iso_ok; then
    curl -fL --retry 5 -C - -o "$WORK/cache/$DEBIAN_ISO" "$DEBIAN_ISO_URL"
    iso_ok || { echo "error: $DEBIAN_ISO does not match the pinned checksum" >&2; exit 1; }
fi

step "Package folder: Docker Engine, nginx and what they need"
os_ok() { [[ -f $LOCK ]] && (cd "$WORK/cache/os" 2>/dev/null && sha256sum -c --quiet "$REPO/$LOCK" >/dev/null 2>&1); }
if ! os_ok; then
    # Resolved against an EMPTY package database, so the folder holds every
    # dependency and the installed machine never needs another source.
    PACKAGES="$(tr '\n' ' ' < deploy/iso/packages.txt) $OS_PACKAGE_PINS" in_tools '
        curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.asc
        fpr=$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: "\$1 == \"fpr\" {print \$10}" | head -1)
        [ "$fpr" = "$DOCKER_KEY_FPR" ] || { echo "error: Docker signing key is $fpr, not the pinned one" >&2; exit 1; }
        gpg --dearmor < /tmp/docker.asc > /tmp/docker.gpg
        printf "%s\n" \
            "deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian trixie main" \
            "deb [arch=amd64 signed-by=/tmp/docker.gpg] https://download.docker.com/linux/debian trixie stable" \
            > /tmp/sources.list
        rm -rf /work/cache/os && mkdir -p /work/cache/os/partial && : > /tmp/status
        apt="apt-get -o Dir::Etc::sourcelist=/tmp/sources.list -o Dir::Etc::sourceparts=/dev/null
             -o Dir::State::status=/tmp/status -o Dir::Cache::archives=/work/cache/os
             -o APT::Install-Recommends=false"
        $apt update
        $apt install --download-only -y $PACKAGES
        cd /work/cache/os && rm -rf partial lock
        apt-ftparchive packages . > Packages'
    if [[ -f $LOCK ]]; then
        os_ok || { echo "error: the fetched packages differ from $LOCK." >&2
                   echo "Docker or Debian published something new. Pin it in deploy/iso/pins.env," >&2
                   echo "or delete $LOCK to accept what was fetched, and review the diff." >&2; exit 1; }
    else
        (cd "$WORK/cache/os" && sha256sum -- *.deb) > "$LOCK"
        echo "    wrote $LOCK: commit it"
    fi
fi
echo "    $(find "$WORK/cache/os" -name '*.deb' | wc -l) packages, $(du -sh "$WORK/cache/os" | cut -f1)"

step "Offline bundle $VERSION (scripts/build_offline_bundle.sh)"
BUNDLE="$WORK/work/bundle-$KIND/rezztor-eagent-${VERSION}-offline"
if (( REFRESH )) || [[ ! -f "$BUNDLE/SHA256SUMS" ]]; then
    rm -rf "$WORK/work/bundle-$KIND"
    if [[ "$KIND" == LAB || "$KIND" == UAT ]]; then
        docker tag "rezztor/eagent:${KIND,,}" "rezztor/eagent:$VERSION"
        for name in eagent-runner eagent-ansible eagent-cloud; do
            docker tag "rezztor/$name:$IMAGE_TAG" "rezztor/$name:$VERSION"
        done
    elif [[ "$KIND" == TEST && "$IMAGE_TAG" != "$VERSION" ]]; then
        for name in eagent eagent-runner eagent-ansible eagent-cloud; do
            docker tag "rezztor/$name:$IMAGE_TAG" "rezztor/$name:$VERSION"
        done
    fi
    # The name of the ISO and what is in it must agree: an image with the licence check off goes
    # into a LAB ISO and into nothing else, and an image with the UAT trust file (ADR 0085) into a
    # UAT ISO and into nothing else; a release or a TEST ISO that carries that file is not written.
    if [[ "$KIND" != STANDIN ]]; then
        stamp="$(docker run --rm --network none --entrypoint sh "rezztor/eagent:$VERSION" -c \
            'cat /eagent/build_info.json; if [ -e /eagent/licence_trust.json ]; then echo UAT_TRUST_FILE; fi')"
        case "$stamp" in
            *'"licence": "uat"'*UAT_TRUST_FILE) is_lab=UAT ;;
            *UAT_TRUST_FILE | *'"licence": "uat"'*) is_lab="half-UAT" ;;
            *'"licence"'*) is_lab=LAB ;;
            *) is_lab=TEST ;;
        esac
        if [[ "$is_lab" != "${KIND/RELEASE/TEST}" ]]; then
            echo "error: rezztor/eagent:$VERSION is a $is_lab image and this is a $KIND build. Nothing was written." >&2
            exit 1
        fi
        # A release carries the image built from its own tag, not one of that name from another day.
        if [[ "$KIND" == RELEASE ]] && ! grep -q "\"commit_hash\": \"$(git rev-parse HEAD)\"" <<< "$stamp"; then
            echo "error: rezztor/eagent:$VERSION was not built from the tag v$VERSION. Nothing was written." >&2
            exit 1
        fi
    fi
    retry scripts/build_offline_bundle.sh "$VERSION" "$WORK/work/bundle-$KIND"
else
    echo "    reusing $BUNDLE (pass --refresh-payload after rebuilding the images)"
fi

step "Payload checksums"
STAGE="$WORK/work/stage-$KIND"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -r deploy/iso/host deploy/iso/keys deploy/iso/install-target.sh deploy/iso/packages.txt "$STAGE/"
# A support key on the ISO closes the console of the machines it installs (ADR 0083). The owner's keys
# are deploy/iso/keys/support/*.pub; REZZTOR_ISO_SUPPORT_KEYS names a folder of others, for a test build
# with a throwaway key. A lab machine's console stays open.
if [[ -n "${REZZTOR_ISO_SUPPORT_KEYS:-}" ]]; then
    mkdir -p "$STAGE/keys/support"
    cp "$REZZTOR_ISO_SUPPORT_KEYS"/*.pub "$STAGE/keys/support/"
fi
if [[ "$KIND" == LAB ]]; then rm -rf "$STAGE/keys/support"; fi
# REZZTOR_ISO_RELEASE_KEYS names a folder of release keys that replace deploy/iso/keys/*.pub, for a UAT or
# test build with dummy keys (ADR 0085): its machines then take no update signed with a real release key.
# A release refuses it above.
if [[ -n "${REZZTOR_ISO_RELEASE_KEYS:-}" ]]; then
    rm -f "$STAGE"/keys/*.pub
    cp "$REZZTOR_ISO_RELEASE_KEYS"/*.pub "$STAGE/keys/"
fi
# A release closes the console unless the owner says otherwise for this build: an open one is a
# decision for one customer, not what happens when nobody thought of it.
if [[ "$KIND" == RELEASE && -z "${REZZTOR_ISO_OPEN_CONSOLE:-}" ]] && ! compgen -G "$STAGE/keys/support/*.pub" > /dev/null; then
    echo "error: this release has no support key (deploy/iso/keys/support/*.pub), so its console would be open." >&2
    echo "Make the key (deploy/iso/keys/README.md), or set REZZTOR_ISO_OPEN_CONSOLE=1 to build it open." >&2
    exit 1
fi
if [[ "$KIND" == RELEASE && -n "${REZZTOR_ISO_OPEN_CONSOLE:-}" ]]; then rm -rf "$STAGE/keys/support"; fi
# A release without a release key installs machines that take no update, ever: 2026.10.13 was one.
if [[ "$KIND" == RELEASE && -z "${REZZTOR_ISO_NO_UPDATES:-}" ]] && ! compgen -G "$STAGE/keys/*.pub" > /dev/null; then
    echo "error: this release has no release key (deploy/iso/keys/*.pub), so no update could ever be installed on it." >&2
    echo "Make the two keys (deploy/iso/keys/README.md), or set REZZTOR_ISO_NO_UPDATES=1 to build it so." >&2
    exit 1
fi
if [[ -n "$MARK" ]]; then echo "$MARK" > "$STAGE/build-mark"; fi
echo "$VERSION" > "$STAGE/VERSION"
# Covers our files and the package folder. The bundle's files are covered by
# the bundle's own SHA256SUMS, which is itself listed here.
# Written outside the staged folder: a list written inside it lists itself.
SUMS="$WORK/work/SHA256SUMS-$KIND"
{
    (cd "$STAGE" && find . -type f -printf '%P\0' | sort -z | xargs -0 sha256sum)
    (cd "$WORK/cache" && find os -type f -print0 | sort -z | xargs -0 sha256sum)
    echo "$(sha256sum < "$BUNDLE/SHA256SUMS" | cut -d' ' -f1)  bundle/SHA256SUMS"
} > "$SUMS"
# The installed machine checks this list first and installs nothing when one
# entry is missing, so every entry must be a file that goes onto the ISO.
while read -r _ path; do
    case "$path" in
    bundle/SHA256SUMS) src="$BUNDLE/SHA256SUMS" ;;
    os/*) src="$WORK/cache/$path" ;;
    *) src="$STAGE/$path" ;;
    esac
    [[ -f "$src" ]] || { echo "error: SHA256SUMS lists $path, which is not in the payload" >&2; exit 1; }
done < "$SUMS"
mv "$SUMS" "$STAGE/SHA256SUMS"
echo "    $(wc -l < "$STAGE/SHA256SUMS") files listed, every one present"

step "ISO"
# -boot_image any replay keeps Debian's BIOS and UEFI boot records as they
# are. Files over 4 GB (the application image) need ISO 9660 level 3, which
# Linux reads. Then the signed boot files are compared with Debian's: Secure
# Boot only keeps working while they are untouched.
in_tools '
    out=/work/out/.building-$KIND.iso
    rm -f "$out"
    xorriso -indev "/work/cache/$DEBIAN_ISO" -outdev "$out" \
        -compliance iso_9660_level=3 -file_size_limit 100g -- \
        -volid REZZTOR_EAGENT_TEST \
        -map "/work/work/stage-$KIND" /rezztor \
        -map "/work/work/bundle-$KIND/rezztor-eagent-$VERSION-offline" /rezztor/bundle \
        -map /work/cache/os /rezztor/os \
        -map /repo/deploy/iso/preseed.cfg /preseed.cfg \
        -map /repo/deploy/iso/grub.cfg /boot/grub/grub.cfg \
        -map /repo/deploy/iso/isolinux.cfg /isolinux/isolinux.cfg \
        -boot_image any replay
    for f in /boot/grub/efi.img /EFI/boot/bootx64.efi /EFI/boot/grubx64.efi /install.amd/vmlinuz /install.amd/initrd.gz; do
        a=$(xorriso -indev "/work/cache/$DEBIAN_ISO" -osirrox on -extract "$f" /tmp/a 2>/dev/null; sha256sum < /tmp/a; rm -f /tmp/a)
        b=$(xorriso -indev "$out" -osirrox on -extract "$f" /tmp/b 2>/dev/null; sha256sum < /tmp/b; rm -f /tmp/b)
        [ "$a" = "$b" ] || { echo "error: $f differs from the one in the Debian image" >&2; exit 1; }
    done'
mv "$WORK/out/.building-$KIND.iso" "$OUT"
(cd "$WORK/out" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

echo
echo "ISO ready: $OUT ($(du -h "$OUT" | cut -f1))"
echo "           $(cut -d' ' -f1 "$OUT.sha256")"
echo "TEST BUILD, not signed. Test it with: deploy/iso/test/test-iso.sh $OUT uefi"
if [[ "$KIND" == RELEASE ]]; then
    console=closed; compgen -G "$STAGE/keys/support/*.pub" > /dev/null || console=OPEN
    keys=$(cd "$STAGE/keys" && ls -- *.pub 2>/dev/null | sed 's/\.pub$//' | paste -sd, -)
    echo "RELEASE $VERSION from the tag v$VERSION ($(git rev-parse --short HEAD)), console $console, release keys ${keys:-NONE}${MARK:+, marked $MARK}."
fi
if [[ "$KIND" == STANDIN ]]; then echo "STAND-IN images: this ISO is for the QEMU test only, it is not the product."; fi
if [[ "$KIND" == LAB ]]; then echo "LAB BUILD: the licence check is off. For the owner's own test lab. Never hand this ISO to a customer."; fi
if [[ "$KIND" == UAT ]]; then
    keys=$(cd "$STAGE/keys" && ls -- *.pub 2>/dev/null | sed 's/\.pub$//' | paste -sd, -)
    echo "UAT BUILD: trusts a dummy licence root and the UAT licence server only, release keys ${keys:-NONE}. Never hand this ISO to a customer."
fi

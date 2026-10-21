#!/usr/bin/env bash
# Build an offline delivery bundle: images, install files, and checksums.
#
# For a customer whose estate has no route to a registry. Everything they need
# crosses the air gap on one piece of media, and they verify it on arrival
# with nothing but sha256sum.
#
#   ./scripts/build_offline_bundle.sh 2026.09.08 [output-dir]
#
# Produces <output>/rezztor-eagent-<version>-offline/ containing:
#   images/            the images as tarballs (docker load)
#   install.sh         the bootstrap, with installer.sh's hash stamped in
#   installer.sh       the installer itself
#   INSTALL.md         the same guide, with the offline steps
#   docker-compose.yml points at locally loaded images, not the registry
#   searxng/           the search engine's settings, which that file mounts
#   rezztor-guard + its systemd unit -- the one boundary not in compose
#   .env.example
#   SHA256SUMS         covers every file above
#
# This is the whole delivery. There is no install host to fetch a missing
# piece from, so anything the installer or the compose file reaches for has
# to be in here -- which is why install.sh, installer.sh, the guard and
# searxng/settings.yml are copied rather than assumed.
#
# Run it after building the release images locally (tagged rezztor/eagent,
# rezztor/eagent-runner, rezztor/eagent-ansible, rezztor/eagent-cloud).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERSION="${1:?usage: build_offline_bundle.sh <version> [output-dir]}"
OUT_ROOT="${2:-dist}"
REGISTRY="rezztor"
BUNDLE="$OUT_ROOT/rezztor-eagent-${VERSION}-offline"

if [[ ! "$VERSION" =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}(-lab|-uat)?$ ]]; then
    echo "error: version must be YYYY.MM.DD, got '$VERSION'" >&2
    exit 1
fi

APP="$REGISTRY/eagent:$VERSION"
RUNNER="$REGISTRY/eagent-runner:$VERSION"
# Optional at run time -- the service sits behind a compose profile -- but not
# optional here: an air-gapped site that wants network automation has no way to
# fetch it later, which is the whole reason this bundle exists.
ANSIBLE="$REGISTRY/eagent-ansible:$VERSION"
# Azure changes (ADR 0061): OpenTofu and its two providers, already inside the
# image and checked by hash when it was built. Also optional at run time (the
# `cloud` profile) and also required here, for the same reason: a site with no
# route to a registry, or to the OpenTofu and provider download hosts, cannot
# add it later.
CLOUD="$REGISTRY/eagent-cloud:$VERSION"
# Required, and from Docker Hub. Kept in step with
# docker-compose.yml and deploy/customer/installer.sh by hand; there is a test
# that they agree.
POSTGRES="pgvector/pgvector:pg17"
# Also from Docker Hub, and also not optional: the agent's web search is a
# separate container now, and an air-gapped site cannot pull it later either.
# Pinned in lockstep with docker-compose.yml.
SEARXNG="searxng/searxng:2026.8.29-d226b78bc"

for image in "$APP" "$RUNNER" "$ANSIBLE" "$CLOUD" "$POSTGRES" "$SEARXNG"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "error: $image is not present locally." >&2
        echo "Build the release images locally first." >&2
        exit 1
    fi
done

# What explains, tests or builds the code stays here (.dockerignore and the
# Dockerfile's assemble step); the source ships. Of deploy/ the image holds the
# files an update package takes from it
# (scripts/sign_release.py, HOST: the four host files, the guard and its units,
# ADR 0092), and the public release keys (not support/) so that a package can
# add a new one. Looked for in the images themselves, because a rule that was quietly
# undone is found by nothing else before a customer has it.
for image in "$APP" "$ANSIBLE" "$CLOUD"; do
    found="$(docker run --rm --network none --entrypoint sh "$image" -c '
        find /eagent /app -path /eagent/usr -prune -o \( \
            -name "*.dox.md" -o -name AGENTS.md -o -name CLAUDE.md -o -name README.md \
            -o -name "Dockerfile*" -o -name conftest.py -o -name pytest.ini -o -name "requirements*.txt" \
            -o -type d -name tests -o -path /eagent/docs -o -path /eagent/scripts \
            -o -path /eagent/docker -o -path /eagent/frontend/src \
            -o -path /eagent/skills/eagent-development -o -path "/eagent/skills/eagent-*-plugin*" \
            -o -path /eagent/knowledge/main/about/architecture.md \
            -o \( -path "/eagent/deploy/*" -type f ! -path /eagent/deploy/customer/docker-compose.yml \
                  ! -path "/eagent/deploy/iso/host/rezztor-update" ! -path "/eagent/deploy/iso/host/rezztor-setup" \
                  ! -path "/eagent/deploy/iso/host/rezztor-network" ! -regex "/eagent/deploy/iso/keys/[a-z0-9-]*\.pub" \
                  ! -path /eagent/deploy/customer/rezztor-guard ! -path /eagent/deploy/customer/rezztor-guard.service \
                  ! -path /eagent/deploy/customer/rezztor-guard-runner.service \
                  ! -path /eagent/deploy/iso/host/rezztor-firewall.service ! -path /eagent/deploy/iso/host/rezztor-load-images.service \
                  ! -path /eagent/deploy/iso/host/rezztor-setup.service ! -path /eagent/deploy/iso/host/rezztor-update.service \
                  ! -path /eagent/deploy/iso/host/rezztor-update.path \) \
            \) -print 2>/dev/null | head -20')"
    if [[ -n "$found" ]]; then
        echo "error: $image carries engineering material a customer must not get:" >&2
        echo "$found" >&2
        exit 1
    fi
done

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/images"

echo "==> saving images (this is slow; they are large)"
docker save "$APP" | gzip > "$BUNDLE/images/eagent-${VERSION}.tar.gz"
docker save "$RUNNER" | gzip > "$BUNDLE/images/eagent-runner-${VERSION}.tar.gz"
docker save "$ANSIBLE" | gzip > "$BUNDLE/images/eagent-ansible-${VERSION}.tar.gz"
docker save "$CLOUD" | gzip > "$BUNDLE/images/eagent-cloud-${VERSION}.tar.gz"
# No rename for this one: it is not a registry-prefixed image, so `docker load`
# restores it under exactly the name the Compose file already asks for.
docker save "$POSTGRES" | gzip > "$BUNDLE/images/postgres.tar.gz"
# Same reasoning: no rename, so the name the Compose file asks for survives.
docker save "$SEARXNG" | gzip > "$BUNDLE/images/searxng.tar.gz"

echo "==> copying the install files"
cp deploy/customer/INSTALL.md "$BUNDLE/"
cp deploy/customer/.env.example "$BUNDLE/"
cp deploy/customer/installer.sh "$BUNDLE/"
# The network guard, and the unit that loads it before Docker. The installer
# puts them beside the compose file and does not run them: they add a firewall
# table on the host, which is the customer's decision, and INSTALL.md step 6 is
# where they are told to.
cp deploy/customer/rezztor-guard "$BUNDLE/"
cp deploy/customer/rezztor-guard.service "$BUNDLE/"
cp deploy/customer/rezztor-guard-runner.service "$BUNDLE/"
# Mounted read-only by the compose file. Without it the search container comes
# up on its own defaults, which is not what was tested.
mkdir -p "$BUNDLE/searxng"
cp deploy/customer/searxng/settings.yml "$BUNDLE/searxng/"

# The bootstrap carries the installer's hash, so the one checksum Rezztor emails
# for install.sh covers the installer too -- and the installer covers the
# Compose file. Stamped from the file actually being shipped, here, because the
# publish step that used to do it went with the install host.
installer_sha=$(sha256sum deploy/customer/installer.sh | awk '{print $1}')
sed "s/__INSTALLER_SHA__/$installer_sha/" deploy/customer/install.sh > "$BUNDLE/install.sh"
case "$(cat "$BUNDLE/install.sh")" in
    *__INSTALLER_SHA__*) echo "error: installer hash placeholder not replaced" >&2; exit 1 ;;
esac
chmod +x "$BUNDLE/install.sh" "$BUNDLE/installer.sh" "$BUNDLE/rezztor-guard"
cp deploy/customer/docker-compose.yml "$BUNDLE/docker-compose.yml"

# Adjust the guide for a bundle that never touches a network.
python3 - "$BUNDLE/INSTALL.md" "$VERSION" <<'PY'
import sys
path, version = sys.argv[1], sys.argv[2]
text = open(path).read().replace("<version>", version)

# This deployment has no licence server route either. A sentence that is not
# found means INSTALL.md changed and this did not: stop, rather than ship an
# offline bundle whose guide tells the customer to activate online.
def swap(old, new):
    assert old in text, "INSTALL.md no longer says: " + old
    return text.replace(old, new)

text = swap(
    "**Settings → Licence**, type the activation key Rezztor sent you into\n"
    "**Activation key**, and press **Activate**. The product checks the key for\n"
    "typing mistakes before it sends anything.",
    "**Settings → Licence**, then **No internet access?**. Type a **Deployment\n"
    "name**, press **Make request code** and **Copy request code**, and send the\n"
    "code to Rezztor. Rezztor sends back a licence file (`.rezlic`). Choose the\n"
    "file or paste its text under **Licence file** and press **Load licence file**.",
)
text = swap(
    "The product talks to one host, `https://license.rezztor.com`, over HTTPS only.\n"
    "It contacts it once to activate, then once a day in the background to renew\n"
    "its 30-day lease. What it sends is the key (at activation), a random\n"
    "installation id, the licence number and version, the installation token it was\n"
    "given at activation, the number and a hash of the lease it holds, a seat count,\n"
    "the product version and a random nonce, and nothing else. No conversation\n"
    "content, no user names, no files, no host names, no device addresses.",
    "This deployment never contacts the licence server. The file is signed by\n"
    "Rezztor and verified for this installation before it is stored; a file made\n"
    "for another installation is refused. The request code holds the installation\n"
    "id, the deployment name, the product version and a timestamp.\n\n"
    "An offline licence is valid for one year at most. Rezztor will send a\n"
    "replacement before it lapses; request and load it the same way.",
)
text = text.replace(
    "Roughly 20 minutes, most of it spent pulling a large image.",
    "Roughly 20 minutes, most of it spent loading large images from this bundle.",
)
open(path, "w").write(text)
PY

echo "==> checksums"
( cd "$BUNDLE" && find . -type f ! -name SHA256SUMS -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS )

SIZE="$(du -sh "$BUNDLE" | cut -f1)"
echo
echo "Bundle ready: $BUNDLE ($SIZE)"
echo
echo "Hand over the whole directory. The customer verifies it with:"
echo "  sha256sum -c SHA256SUMS"
echo
echo "Email them this, so the checksum arrives by a different route than the"
echo "script it checks:"
echo "  sha256sum install.sh"
echo "  $(sha256sum "$BUNDLE/install.sh" | awk '{print $1}')"
echo
echo "Issue the offline licence file from the licence console, using the"
echo "customer's request code."
echo "The file carries the full contract term and CANNOT be revoked before expiry."

#!/usr/bin/env bash
# Build the images locally for development and testing. No registry, no push.
#
#   ./scripts/build_local.sh                 # base, app, runner, ansible, cloud
#   ./scripts/build_local.sh app runner      # only those (base|app|runner|ansible|cloud|ansible-quick)
#   ansible-quick layers the current executor code on an existing image (no Galaxy download)
#   NO_CACHE=1 ./scripts/build_local.sh      # rebuild from scratch
#   BUILD_VERSION=2026.10.12 ./scripts/build_local.sh app   # the version the product reports (default: local);
#                                            # an update package needs the real one (scripts/sign_release.py)
#   BUILD_LICENCE=enterprise BUILD_VERSION=2026.10.21 ./scripts/build_local.sh app
#                                            # an Enterprise image: the stamp says so, tagged as REZZTOR_IMAGE_TAG
#   BUILD_LICENCE=uat BUILD_VERSION=2026.10.18-uat REZZTOR_UAT_TRUST=uat-trust.json ./scripts/build_local.sh app
#                                            # a UAT image, rezztor/eagent:uat, that trusts only the dummy licence
#                                            # root, server and CA in that file (ADR 0085)
#
# Tags are rezztor/<name>:latest, which is what docker/run/docker-compose.yml
# expects. Then: docker compose -f docker/run/docker-compose.yml up -d
# REZZTOR_IMAGE_TAG names another tag: scripts/build_release.sh builds a release
# under its version and leaves the images tagged latest alone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
OPTS=(); [[ -n "${NO_CACHE:-}" ]] && OPTS+=(--no-cache)
TAG="${REZZTOR_IMAGE_TAG:-latest}"

# What the images are built from, by hash, for the application image to carry. It
# holds no Dockerfile, no docker/ and no requirements file (a customer gets
# nothing that builds the product), and scripts/sign_release.py must still see
# when one of them changed: that is when an image travels whole in an update.
# The paths cover its INPUTS; tests/test_update_tool.py compares the two.
build_inputs() {
    git ls-files -z -- requirements.txt Dockerfile docker executor cloud_executor \
            plugins/_ea_ansible plugins/_ea_azure/helpers/prices.py \
        | grep -zvE '(^|/)tests/|\.dox\.md$|(^|/)(AGENTS|README)\.md$' \
        | xargs -0 sha256sum > build_inputs.sha256
}

# A UAT build's trust (ADR 0085): {"root_keys": {kid: base64url Ed25519 public key}, "server": "https://...",
# "ca_pem": "-----BEGIN CERTIFICATE-----..."}, checked here and passed to the build compact. Public material only.
uat_trust() {
    [[ "${BUILD_LICENCE:-}" == uat ]] || return 0
    python3 -c '
import base64, json, sys
t = json.load(open(sys.argv[1]))
roots, server, ca = t["root_keys"], t["server"], t["ca_pem"]
assert isinstance(roots, dict) and roots, "root_keys is empty"
for kid, key in roots.items():
    assert len(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))) == 32, "root key " + kid + " is not 32 bytes"
assert server.startswith("https://"), "server is not https://"
assert "BEGIN CERTIFICATE" in ca, "ca_pem holds no certificate"
print(json.dumps({"root_keys": roots, "server": server, "ca_pem": ca}, separators=(",", ":")))
' "${REZZTOR_UAT_TRUST:?BUILD_LICENCE=uat needs REZZTOR_UAT_TRUST, the trust JSON file (ADR 0085)}"
}

build() {
    case "$1" in
        base)    docker build "${OPTS[@]}" -t rezztor/eagent-base:latest docker/base/ ;;
        # A UAT build (BUILD_LICENCE=uat, ADR 0085) is tagged uat and never becomes rezztor/eagent:latest.
        # An Enterprise build (BUILD_LICENCE=enterprise) keeps the tag it was given: the version, for a release.
        app)     build_inputs
                 app_tag="$TAG"
                 [[ "${BUILD_LICENCE:-}" == uat ]] && app_tag=uat
                 trust="$(uat_trust)"
                 docker build "${OPTS[@]}" -t "rezztor/eagent:$app_tag" \
                     --build-arg CACHE_DATE="$(date +%s)" --build-arg BUILD_LICENCE="${BUILD_LICENCE:-}" \
                     --build-arg BUILD_UAT_TRUST="$trust" \
                     --build-arg BUILD_VERSION="${BUILD_VERSION:-local}" --build-arg BUILD_COMMIT="$COMMIT" . ;;
        runner)  docker build "${OPTS[@]}" -t "rezztor/eagent-runner:$TAG" -f docker/runner/Dockerfile . ;;
        ansible) docker build "${OPTS[@]}" -t "rezztor/eagent-ansible:$TAG" -f executor/Dockerfile \
                     --build-arg CATALOGUE_COMMIT="$COMMIT" . ;;
        cloud)   docker build "${OPTS[@]}" -t "rezztor/eagent-cloud:$TAG" -f cloud_executor/Dockerfile \
                     --build-arg TEMPLATES_COMMIT="$COMMIT" --build-arg VERSION="${BUILD_VERSION:-local}" . ;;
        ansible-quick)
            docker image inspect rezztor/eagent-ansible:collections-base >/dev/null 2>&1 \
                || docker tag rezztor/eagent-ansible:latest rezztor/eagent-ansible:collections-base
            docker build "${OPTS[@]}" -t rezztor/eagent-ansible:latest -f executor/Dockerfile.quick \
                --build-arg CATALOGUE_COMMIT="$COMMIT" . ;;
        *) echo "unknown target '$1' (base|app|runner|ansible|cloud|ansible-quick)" >&2; exit 1 ;;
    esac
}

for target in "${@:-base app runner ansible cloud}"; do
    # the default is one word-split string; explicit args are already split
    for t in $target; do echo "==> $t"; build "$t"; done
done
echo "done: docker images 'rezztor/*'"

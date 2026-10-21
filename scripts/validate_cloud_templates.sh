#!/usr/bin/env bash
# Validate every cloud executor template with the real OpenTofu and the pinned providers inside the image, with no
# network. The unit tests replay recorded plans and cannot see a provider argument that does not exist; this can
# (it caught private_endpoint on 2026-10-04). Run it after changing a template or a provider version.
#
#   scripts/validate_cloud_templates.sh [image]      default image: rezztor/eagent-cloud:latest
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:-rezztor/eagent-cloud:latest}"

docker run --rm --network none --entrypoint sh \
  -e TF_CLI_CONFIG_FILE=/etc/tofu/cli.tfrc -e TF_IN_AUTOMATION=1 \
  -v "$PWD/cloud_executor/templates:/t:ro" "$image" -c '
ok=0; bad=0
for d in /t/*/; do
  n=$(basename "$d"); [ -f "$d/main.tf" ] || continue
  w=/tmp/v/$n; mkdir -p "$w"; cp "$d"/*.tf "$w"/; cd "$w"
  # the block the runner writes beside every template (runner._executor_file), without the backend
  printf "terraform {\n  required_providers {\n    azurerm = { source = \"hashicorp/azurerm\" }\n    azapi   = { source = \"azure/azapi\" }\n  }\n}\nprovider \"azurerm\" {\n  features {}\n  subscription_id = \"00000000-0000-0000-0000-000000000000\"\n}\n" > _executor.tf
  if tofu init -backend=false -input=false -no-color >/tmp/init.log 2>&1 && tofu validate -no-color >/tmp/val.log 2>&1; then
    ok=$((ok+1))
  else
    bad=$((bad+1)); echo "FAIL $n"; grep -A7 "Error" /tmp/init.log /tmp/val.log | head -20
  fi
done
echo "templates validated: $ok ok, $bad failed"
[ "$bad" -eq 0 ]'

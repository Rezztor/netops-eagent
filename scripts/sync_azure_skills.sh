#!/usr/bin/env bash
# Vendor Microsoft's official Azure agent skills (https://github.com/microsoft/azure-skills, MIT) into the Azure plugin
# as reference text. DEVELOPER-TIME ONLY: the product never runs this, never fetches anything at run time, and ships
# whatever is checked in (ADR 0076).
#
#   scripts/sync_azure_skills.sh --check [--from <clone>]
#       exit 1 if upstream's default branch is past the vendored commit, or the monthly review (next_review_due) is overdue
#   scripts/sync_azure_skills.sh <commit-or-tag> [--from <clone>]
#       REVIEW: writes nothing into the plugin; prints, and saves to tmp/azure-skills-review-<commit>.md, every file added,
#       removed and changed with its diff, and a flag per new line a reviewer must read (see FLAGS in the helper)
#   scripts/sync_azure_skills.sh <commit-or-tag> --accept --reviewer "Name" [--from <clone>]
#       IMPORT: refused unless a review report exists for that same commit and content; records reviewer and date
#
# The owner's rule: fetch the update every month, and no import without the security check. Read the flags, then accept.
# Copies only `*.md` of the allow-listed skills, renames every SKILL.md to OVERVIEW.md, copies the LICENSE and writes
# reference/VENDOR.json (commit, date, allow-list, SHA-256 of every file, reviewed_by, reviewed_on, next_review_due). No scripts,
# hooks, .mcp.json, plugin manifests, telemetry files or images are copied. Accepting one commit twice changes nothing.
# Then: read `git diff`, run `pytest plugins/_ea_azure/tests/test_azure_microsoft_guidance.py`, commit.
set -euo pipefail
cd "$(dirname "$0")/.."

URL="https://github.com/microsoft/azure-skills"
DEST="${SYNC_AZURE_SKILLS_DEST:-plugins/_ea_azure/skills/azure-microsoft-guidance/reference}"

# name|reason it is vendored
ALLOW=(
  "azure-diagnostics|the troubleshooting guides by service (App Service, Functions, Container Apps, AKS, VM, messaging): the core of what the agent is for"
  "azure-kusto|KQL patterns and schema discovery; the guarded Log Analytics query is KQL too (Azure Data Explorer itself is not reachable from here)"
  "azure-storage|storage account, tier and redundancy guidance for sizing and advice"
  "azure-compute|VM and scale set sizing, pricing and capacity reservation guidance (the create workflows are read as advice, not run)"
  "azure-quotas|what a quota is, how to read usage and ask for an increase (the engineer runs the az quota commands)"
  "azure-reliability|zone redundancy and multi-region assessment of App Service and Functions: a checklist for advice"
  "azure-kubernetes|AKS Day-0 and Day-1 guidance: SKU, networking, security, upgrades, cost"
  "azure-messaging|Event Hubs and Service Bus SDK troubleshooting, one file"
  "azure-enterprise-infra-planner|landing zone, hub-spoke and DR design guidance, WAF checklists and per-resource constraints (planning only)"
  "azure-resource-visualizer|how to turn Resource Graph rows into a Mermaid architecture diagram"
  "azure-upgrade|plan and SKU upgrade assessment (Functions Consumption to Flex, Redis to Managed Redis); read as an assessment checklist"
  "entra-app-registration|Entra app registration, permissions and OAuth concepts, the same objects the product's own read and change apps are"
)
# Left out on purpose:
#   azure-prepare, azure-deploy, azure-validate, azure-app-onboard, azure-app-onboard-prereq, azure-cloud-migrate,
#   python-appservice-deploy, appinsights-instrumentation, azure-aigateway, microsoft-foundry, azure-ai,
#   airunway-aks-setup, entra-agent-id: deploy and developer skills. They create, deploy and delete from a developer's
#   workstation with az, azd and the Azure MCP server; our agent has none of those and the product never deletes.
#   azure-compliance: runs an external binary.
#   azure-resource-lookup: azure_inventory does exactly this from the stored snapshot.
#   discover-azure-skills: a catalogue search that calls out to the network.

die() { echo "sync_azure_skills: $*" >&2; exit 2; }

mode=review ref="" from="" reviewer=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --accept) mode=accept ;;
    --reviewer) [ $# -ge 2 ] || die "--reviewer needs a name"; reviewer="$2"; shift ;;
    --from) [ $# -ge 2 ] || die "--from needs a path"; from="$2"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$ref" ] || die "one commit or tag only"; ref="$1" ;;
  esac
  shift
done

vendored() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); print(v["commit"], v.get("next_review_due", ""))' "$DEST/VENDOR.json"; }
REPORTS="${SYNC_AZURE_SKILLS_REPORT_DIR:-tmp}"

if [ "$mode" = check ]; then
  read -r have due < <(vendored 2>/dev/null) || die "nothing vendored yet ($DEST/VENDOR.json is missing)"
  if [ -n "$from" ]; then head=$(git -C "$from" rev-parse HEAD)
  else head=$(git ls-remote "$URL" HEAD | cut -f1); fi
  [ -n "$head" ] || die "could not read upstream's default branch"
  rc=0
  if [ "$head" = "$have" ]; then echo "up to date: $have"
  else echo "upstream has moved: vendored $have, default branch is $head"; rc=1; fi
  if [ -n "$due" ] && [[ "$(date +%F)" > "$due" ]]; then echo "the monthly review is overdue: it was due $due"; rc=1; fi
  exit $rc
fi

[ -n "$ref" ] || die "usage: $0 <commit-or-tag> [--accept --reviewer NAME] [--from <clone>] | --check [--from <clone>]"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
git clone --quiet "${from:-$URL}" "$work/clone"
git -C "$work/clone" checkout --quiet --detach "$ref" || die "$ref is not a commit or tag of the source"
sha=$(git -C "$work/clone" rev-parse HEAD)
date=$(git -C "$work/clone" show -s --format=%cI HEAD)
names=(); for e in "${ALLOW[@]}"; do names+=("${e%%|*}"); done
python3 scripts/sync_azure_skills.py "$mode" "$work/clone" "$DEST" "$REPORTS" "$sha" "$date" "$URL" --reviewer "$reviewer" "${names[@]}"

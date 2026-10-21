"""The Devices form can express every platform the catalogue runs against.

A group created in the Devices UI carried no `ansible_network_os` and no
`ansible_connection`, because the form had no field for them. Every job against
such a group ran over plain `ssh` and the first task answered `Connection type
ssh is not valid for this module`. The executor's renderer had accepted both
variables all along and the import-JSON path carried them, so the workaround was
to export the inventory, edit it by hand and import it back -- which worked, and
which nobody would find. Reported as F3 from the live SONiC verification on
2026-09-19.

The form now offers a Platform select. That puts the same list in two places,
which is the next bug waiting to happen: add a platform to the catalogue, forget
the form, and the product can run something its own interface cannot describe.
This fails when they disagree.
"""

from __future__ import annotations

import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(os.path.dirname(PLUGIN_ROOT))
CATALOGUE = os.path.join(PLUGIN_ROOT, "catalogue.yaml")
PANEL = os.path.join(PROJECT_ROOT, "frontend", "src", "routes", "NetworkInventory.tsx")

#: Not a device platform: reads/ping_localhost reaches nothing at all, so no
#: group needs to describe itself as it.
NOT_A_DEVICE = {"any"}


def catalogue_platforms() -> set[str]:
    with open(CATALOGUE, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    entries = document["entries"] if isinstance(document, dict) else document
    found = {platform for entry in entries for platform in (entry.get("platforms") or [])}
    return found - NOT_A_DEVICE


def form_platforms() -> dict[str, dict[str, str]]:
    """The PLATFORMS table, read out of the component.

    Parsed rather than imported, because it is TypeScript. The shape is fixed by
    the test below, so a rewrite that this cannot read fails loudly instead of
    quietly matching nothing.
    """
    source = open(PANEL, encoding="utf-8").read()
    block = re.search(r"const PLATFORMS[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert block, "PLATFORMS table not found in NetworkInventory.tsx"

    entries: dict[str, dict[str, str]] = {}
    for key, body in re.findall(r"(\w+):\s*\{(.*?)\},", block.group(1), re.S):
        fields = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", body))
        entries[key] = fields
    return entries


def test_the_form_offers_every_platform_the_catalogue_targets():
    missing = catalogue_platforms() - set(form_platforms())
    assert not missing, (
        f"the catalogue has entries for {sorted(missing)} and the Devices form "
        "cannot describe a group as one, so those entries cannot be run from the "
        "interface. Add them to PLATFORMS in NetworkInventory.tsx."
    )


def test_the_form_offers_nothing_the_catalogue_cannot_run():
    extra = set(form_platforms()) - catalogue_platforms()
    assert not extra, (
        f"the Devices form offers {sorted(extra)} and no catalogue entry targets "
        "it, so choosing it produces a group nothing can be run against."
    )


def test_every_platform_names_a_connection_and_a_network_os():
    """Both, or neither is any use.

    A network_os with no network_cli connection is the failure this field exists
    to remove; a connection with no network_os leaves the playbooks unable to
    dispatch, which is what reads/device_facts now refuses by name.
    """
    for key, spec in form_platforms().items():
        assert spec.get("network_os"), f"{key} has no ansible_network_os"
        assert spec.get("connection"), f"{key} has no ansible_connection"
        assert spec.get("label"), f"{key} has no label to show"
        # community SONiC is no collection: its network_os is the bare name
        assert "." in spec["network_os"] or key == "sonic_community", (
            f"{key}: use the collection-qualified name, which is what the "
            "playbooks match on after stripping the namespace"
        )


def test_the_platform_names_match_what_the_playbooks_dispatch_on():
    """The form writes `ansible_network_os`; the playbooks read it and take the
    part after the last dot. Those two have to agree, or a correctly filled form
    produces jobs that refuse themselves."""
    for key, spec in form_platforms().items():
        assert spec["network_os"].rsplit(".", 1)[-1] == key, (
            f"{key}: the playbooks reduce {spec['network_os']!r} to "
            f"{spec['network_os'].rsplit('.', 1)[-1]!r}, which is not {key!r}"
        )


def test_a_catalogue_entry_lists_community_sonic():
    with open(CATALOGUE, encoding="utf-8") as handle:
        entries = yaml.safe_load(handle)["entries"]
    assert any("sonic_community" in (e.get("platforms") or []) for e in entries)

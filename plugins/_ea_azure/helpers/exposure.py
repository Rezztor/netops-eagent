"""Why a stored resource might be open to the internet. Computed on read from the stored fields, never stored.

A pointer to look, not a verdict: it does not replace a firewall review. Pure except `of`, which reads one
more hop for a virtual machine.

ponytail: a scan in Python over the live rows; a few thousand rows. Move the rules into SQL if a tenant passes 50,000.
"""

from __future__ import annotations

from typing import Any

REASONS = ("public_ip", "open_rule", "public_access", "public_blob", "external_ingress")
_ANY_SOURCE = {"*", "internet", "0.0.0.0/0", "::/0"}
_NEEDS_NETWORK_RULE = {"microsoft.storage/storageaccounts", "microsoft.keyvault/vaults"}


def _yes(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _has_public_ip(links_out: list[dict[str, Any]]) -> bool:
    return any(link.get("kind") == "public_ip" for link in links_out)


def _open_rule(rule: dict[str, Any]) -> bool:
    return (
        str(rule.get("direction", "")).lower() == "inbound" and str(rule.get("access", "")).lower() == "allow"
        and any(str(s).strip().lower() in _ANY_SOURCE for s in rule.get("sources") or [])
    )


def reasons(resource: dict[str, Any], links_out: list[dict[str, Any]]) -> list[str]:
    """The `REASONS` that apply to one resource. A machine's `nic` link may carry that card's own `links_out`."""
    type_, props = str(resource.get("type", "")).lower(), resource.get("props") or {}
    found = []
    if (type_ == "microsoft.network/networkinterfaces" and _has_public_ip(links_out)) or (
        type_ == "microsoft.compute/virtualmachines"
        and any(link.get("kind") == "nic" and _has_public_ip(link.get("links_out") or []) for link in links_out)
    ):
        found.append("public_ip")
    if type_ == "microsoft.network/networksecuritygroups" and any(_open_rule(r) for r in props.get("rules") or []):
        found.append("open_rule")
    if str(props.get("public_network_access", "")).lower() == "enabled" and (
        type_ not in _NEEDS_NETWORK_RULE or str(props.get("default_network_action", "")).lower() == "allow"
    ):
        found.append("public_access")
    if _yes(props.get("allow_blob_public_access")):
        found.append("public_blob")
    if _yes(props.get("ingress_external")):
        found.append("external_ingress")
    return found


def of(resource: dict[str, Any]) -> list[str]:
    """`reasons` for a record from `store.get`: for a virtual machine, its network cards' links are read too."""
    links = resource.get("links_out") or []
    if str(resource.get("type", "")).lower() == "microsoft.compute/virtualmachines":
        from plugins._ea_azure.helpers import store

        links = [
            {**link, "links_out": (store.get(link["id"]) or {}).get("links_out", [])} if link["kind"] == "nic" and link["id"] else link
            for link in links
        ]
    return reasons(resource, links)

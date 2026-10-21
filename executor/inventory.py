"""The device inventory, owned by the executor.

ADR 0030: "the app sends an inventory selector; the executor owns the inventory
that resolves it." Until now that ownership was a read-only file an operator
mounted by hand, which is ownership in the sense that nobody else could change
it -- including the operator, without a redeploy.

This makes it managed content: a JSON document an administrator edits through the
configuration plane, rendered into a real Ansible inventory at the moment a job
runs.

Two rules give it its shape.

**No credential may be written here.** The inventory names a credential; the
credential store holds it. A `vars` block that accepted `ansible_password` would
be a second credential store with none of the first one's properties -- not
encrypted, returned by GET, and sitting in whatever the console last rendered.
So those keys are refused by name, the same way the job API refuses playbook
content by name, and for the same reason.

**Secrets are resolved into the job's own directory and nowhere else.** The
rendered inventory is written into the per-job private data directory, which is
tmpfs and removed when the job ends. It is never written next to the document it
was rendered from.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

import yaml

from executor import credentials
from executor import store as state

#: Group and host names become Ansible identifiers and appear in `--limit`, so
#: they are held to the same character set the limit pattern allows.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: Names Ansible gives a meaning of its own: as a host or group they would make `--limit all` select every device.
RESERVED = frozenset({"all", "ungrouped", "localhost"})

#: Variables that would carry a secret. Refused by name, before anything looks
#: at what they contain -- the same rule as the job API's forbidden fields, and
#: written out here rather than inferred so it is testable.
FORBIDDEN_VARS: frozenset[str] = frozenset(
    {
        "ansible_password",
        "ansible_pass",
        "ansible_ssh_pass",
        "ansible_ssh_password",
        "ansible_become_pass",
        "ansible_become_password",
        "ansible_ssh_private_key_file",
        "ansible_private_key_file",
        "ansible_httpapi_pass",
        "ansible_httpapi_password",
        "ansible_net_password",
        "ansible_net_passwd",
        "ansible_vault_password",
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "private_key",
    }
)

#: How a credential of each kind is presented to Ansible. Keeping this here
#: rather than in the credential store is deliberate: the store's job is to keep
#: a secret, and knowing what Ansible calls it is not part of that.
CREDENTIAL_VARS = {
    "password": "ansible_password",
    "ssh_key": "ansible_ssh_private_key_file",
    "api_token": "ansible_httpapi_pass",
    "enable": "ansible_become_password",
}

#: Platforms read over HTTPS by `uri` on the executor itself (reads/fw/read.yml), so Ansible must not open a
#: connection to the device: `httpapi` for `paloaltonetworks.panos.panos` fails at the first task because
#: that collection is not installed (and is not wanted). The application now records `local` itself
#: (plugins/_ea_inventory/helpers/executor_sync.py); this still renders `local` whatever the document says, for a document
#: stored before that change and for a group written by hand, which preflight reports as stale rather than breaking a read.
API_FROM_EXECUTOR = ("fortios", "panos")

EMPTY: dict[str, Any] = {"groups": {}, "hosts": {}}


class InventoryError(Exception):
    """Refused. The message is written to be shown to an administrator."""


def _check_vars(where: str, values: Any) -> dict[str, Any]:
    if values in (None, {}):
        return {}
    if not isinstance(values, dict):
        raise InventoryError(f"{where}: vars must be an object.")
    offending = sorted({str(key) for key in values} & FORBIDDEN_VARS)
    if offending:
        raise InventoryError(
            f"{where}: {', '.join(offending)} cannot be set here. Store the secret as a "
            "credential and name it on the group instead."
        )
    return {str(key): value for key, value in values.items()}


def validate(document: Any) -> dict[str, Any]:
    """Return a normalised inventory, or raise with what is wrong and where."""
    if not isinstance(document, dict):
        raise InventoryError("An inventory must be an object.")

    unknown = set(document) - {"groups", "hosts"}
    if unknown:
        raise InventoryError("Unknown inventory sections: " + ", ".join(sorted(unknown)))

    raw_hosts = document.get("hosts") or {}
    if not isinstance(raw_hosts, dict):
        raise InventoryError("hosts must be an object keyed by host name.")

    hosts: dict[str, Any] = {}
    for name, entry in raw_hosts.items():
        if not NAME.match(str(name)) or str(name).lower() in RESERVED:
            raise InventoryError(f"'{name}' is not a usable host name.")
        if not isinstance(entry, dict):
            raise InventoryError(f"host {name}: must be an object.")
        address = str(entry.get("ansible_host") or "").strip()
        if not address:
            raise InventoryError(f"host {name}: needs an ansible_host address.")
        hosts[str(name)] = {
            "ansible_host": address,
            "vars": _check_vars(f"host {name}", entry.get("vars")),
        }

    raw_groups = document.get("groups") or {}
    if not isinstance(raw_groups, dict):
        raise InventoryError("groups must be an object keyed by group name.")

    groups: dict[str, Any] = {}
    for name, entry in raw_groups.items():
        if not NAME.match(str(name)) or str(name).lower() in RESERVED:
            raise InventoryError(f"'{name}' is not a usable group name.")
        if not isinstance(entry, dict):
            raise InventoryError(f"group {name}: must be an object.")
        members = entry.get("hosts") or []
        if not isinstance(members, list):
            raise InventoryError(f"group {name}: hosts must be a list.")
        missing = [str(host) for host in members if str(host) not in hosts]
        if missing:
            # A group naming a host that does not exist resolves to an empty
            # --limit, which runs against nothing and reports success.
            raise InventoryError(
                f"group {name}: no such host: {', '.join(sorted(missing))}."
            )
        groups[str(name)] = {
            "hosts": [str(host) for host in members],
            "vars": _check_vars(f"group {name}", entry.get("vars")),
            "credential": str(entry.get("credential") or ""),
        }

    # One namespace for `--limit`: a host with a group's name would make a job for that one device a job for the group.
    both = sorted(set(hosts) & set(groups))
    if both:
        raise InventoryError(f"'{both[0]}' is both a host and a group. Rename one of them.")

    return {"groups": groups, "hosts": hosts}


def unresolved_credentials(document: dict[str, Any], known: set[str]) -> list[str]:
    """Credential names the inventory refers to and the store does not have.

    Reported rather than refused: an administrator loading an inventory before
    its credentials is doing them in a reasonable order, and being told about it
    is more use than being stopped.
    """
    wanted = {
        group["credential"]
        for group in document.get("groups", {}).values()
        if group.get("credential")
    }
    return sorted(wanted - known)


class InventoryStore:
    """One JSON document, replaced atomically."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                return validate(json.load(handle))
        except FileNotFoundError:
            return dict(EMPTY)
        except (OSError, ValueError) as error:
            raise InventoryError(f"The stored inventory is unreadable: {error}") from error

    def save(self, document: Any) -> dict[str, Any]:
        checked = validate(document)
        with self._lock:
            temporary = f"{self.path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(checked, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        return checked

    # --- rendering -----------------------------------------------------------

    def render(self, directory: str) -> str:
        """Write an Ansible inventory into a job's own directory.

        The secrets go in here and only here. `directory` is the job's private
        data directory: tmpfs, removed when the job ends, and never shared with
        another job.
        """
        document = self.load()
        children: dict[str, Any] = {}

        for group_name, group in document["groups"].items():
            group_vars = dict(group["vars"])
            name = group.get("credential")
            if name:
                record = {item["name"]: item for item in credentials.store().list()}.get(name)
                if record is None:
                    raise InventoryError(
                        f"group {group_name} names credential '{name}', which is not stored here."
                    )
                kind = record["kind"]
                variable = CREDENTIAL_VARS.get(kind)
                if variable is None:
                    raise InventoryError(f"credential '{name}' has an unusable kind '{kind}'.")
                if record["username"]:
                    group_vars["ansible_user"] = record["username"]
                group_vars[variable] = credentials.store().resolve(name)

            if str(group_vars.get("ansible_network_os") or "").rsplit(".", 1)[-1] in API_FROM_EXECUTOR:
                group_vars["ansible_connection"] = "local"

            children[group_name] = {
                "hosts": {
                    host: {
                        "ansible_host": document["hosts"][host]["ansible_host"],
                        **document["hosts"][host]["vars"],
                    }
                    for host in group["hosts"]
                },
                "vars": group_vars,
            }

        # Hosts in no group still have to be addressable, or an inventory that
        # validated cannot run.
        grouped = {host for group in document["groups"].values() for host in group["hosts"]}
        ungrouped = {
            host: {"ansible_host": entry["ansible_host"], **entry["vars"]}
            for host, entry in document["hosts"].items()
            if host not in grouped
        }
        if ungrouped:
            children["ungrouped"] = {"hosts": ungrouped}

        path = os.path.join(directory, "inventory.yml")
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            yaml.safe_dump({"all": {"children": children}}, file, default_flow_style=False)
        return path


_store: InventoryStore | None = None
_store_lock = threading.Lock()


def store() -> InventoryStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = InventoryStore(os.path.join(state.state_dir(), "inventory.json"))
        return _store


def reset_for_tests(path: str | None = None) -> InventoryStore | None:
    global _store
    with _store_lock:
        _store = InventoryStore(path) if path else None
        return _store

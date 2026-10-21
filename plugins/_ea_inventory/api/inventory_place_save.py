"""Suggest, create, rename, deactivate and delete places. Gated on `inventory.manage` by
`helpers/api_policy.py`; this handler also needs `inventory.places` for a site, a location or a
building, for any `chain`, and for every delete, because the table holds one permission per route.
Creating a place is a direct, audited write (ADR 0054); placing a device is still a proposal."""

from __future__ import annotations

from typing import Any

from helpers import login, rbac
from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import places, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError

UPPER = 3  # site, location, building: the first three levels belong to administrators
_FIELDS = ("room_type", "address", "contact", "notes")


def _needs_places(message: str = "") -> dict[str, Any] | None:
    if login.has_permission(rbac.PERM_INVENTORY_PLACES):
        return None
    return {"ok": False, "error": message or f"Requires the {rbac.PERM_INVENTORY_PLACES} permission."}


class InventoryPlaceSave(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            return self._run(str(input.get("action") or ""), input)
        except InventoryError as error:
            out: dict[str, Any] = {"ok": False, "error": str(error)}
            if getattr(error, "existing", None):
                out["existing"] = error.existing  # type: ignore[attr-defined]
            return out

    def _run(self, action: str, input: dict) -> dict[str, Any]:
        parent = places.normalise_id(input.get("parent_id"))
        if action == "suggest":
            if parent and not places.get(parent):
                raise InventoryError(f"There is no place {parent}.")
            if parent and places.kind_of(parent) == "rack":
                raise InventoryError("A rack is the lowest level; nothing goes under it.")
            kind = places.KINDS[places.depth(parent) if parent else 0]
            taken = {p["code"] for p in places.list_places() if p["parent_id"] == parent}
            return {"ok": True, "code": places.suggest_code(kind, str(input.get("name") or ""), taken)}
        if action == "create":
            chain = input.get("chain") or []
            if not isinstance(chain, list) or not all(isinstance(c, dict) for c in chain):
                raise InventoryError("chain must be a list of {name, code}.")
            if chain or (places.depth(parent) + 1 if parent else 1) <= UPPER:
                denied = _needs_places()
                if denied:
                    return denied
            created = places.create(
                parent, input.get("name"), input.get("code"), chain=[(c.get("name"), c.get("code")) for c in chain],
                actor=proposals.current_actor(), **{f: input.get(f) or "" for f in _FIELDS},
            )
            return {"ok": True, "place": created[0], "created": created}
        id = places.normalise_id(input.get("id"))
        if not id:
            raise InventoryError("id is required.")
        if action == "update":
            if places.depth(id) <= UPPER:
                denied = _needs_places()
                if denied:
                    return denied
            changes = {k: input[k] for k in (*_FIELDS, "name", "active") if k in input}
            return {"ok": True, "place": places.update(id, **changes)}
        if action == "delete":
            denied = _needs_places()
            if denied:
                return denied
            places.delete(id)
            return {"ok": True}
        raise InventoryError("action must be suggest, create, update or delete.")

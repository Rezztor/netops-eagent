"""Constants and helpers shared by the cloud executor's tests."""
import json
from pathlib import Path

HERE = Path(__file__).parent
SUB = "11111111-2222-3333-4444-555555555555"
TENANT = "99999999-8888-7777-6666-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RESOURCE_GROUP = {"subscription": SUB, "resource_group": "rg-payments-prod-uaen"}
RG_SOURCE = {"template": "resource_group", "variables": {
    "region": "uaenorth", "tags": {"env": "prod", "owner": "netops", "costcentre": "cc-1001", "project": "payments"}}}
RAILS = {"subscriptions": [SUB], "regions": ["uaenorth"], "resource_groups": {"existing": [], "new_prefix": "rg-"}}


def plan_fixture(name="resource_group", update=False):
    return json.loads((HERE / "fixtures" / "templates" / f"{name}{'.update' if update else ''}.json").read_text())




def request(**extra):
    return {"mode": "apply", "source": RG_SOURCE, "workspace": RESOURCE_GROUP, "change_id": "chg-1", "actor": "alice", **extra}

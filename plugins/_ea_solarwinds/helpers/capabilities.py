"""Every question this plugin can ask SolarWinds, as one fixed template each.

The agent never writes SWQL. It names a capability by this module's key, and
this module owns the only SWQL text that capability will ever run. Every
template below is a Python string literal; every value the caller supplies
travels through SWIS's own `parameters` binding (`@name` in the text, resolved
server-side), never through string formatting. That is the whole guarantee:
there is no code path here from an argument's *value* to query text, only from
an argument's *presence* to which of two fixed literals gets used.

Column names follow SolarWinds' standard, long-stable Orion schema. Entity
names were checked against real SWQL examples or the OrionSDK schema docs
during planning (`docs/internal/research/solarwinds-plugin-plan.md`); exact
column availability on any one instance is confirmed live, in Phase 1
verification, not asserted here as certain for every possible install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from plugins._ea_solarwinds.helpers import config as plugin_config


class CapabilityError(Exception):
    """A capability name or its arguments were wrong. Shown to the agent."""


class UnknownCapability(CapabilityError):
    pass


@dataclass(slots=True)
class Built:
    swql: str
    params: dict[str, Any]


@dataclass(slots=True)
class Capability:
    name: str
    summary: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    build: Callable[[dict[str, Any], int], Built]
    # Internal capabilities serve plugin code (bulk inventory reads), never the
    # agent: hidden from names()/describe() and refused by build() unless the
    # caller says internal=True -- which the agent tool never does.
    internal: bool = False


def _require(args: dict[str, Any], *names: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        value = str(args.get(name) or "").strip()
        if not value:
            raise CapabilityError(f"'{name}' is required for this capability.")
        out[name] = value
    return out


def _pattern(value: str) -> str:
    """A LIKE pattern for a fragment the caller typed -- never SQL, just the
    wildcards SWIS's own bound-parameter LIKE syntax expects."""
    return f"%{value}%"


# --- Nodes -------------------------------------------------------------------

def _node_status(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} Caption, IPAddress, Vendor, MachineType, Status, "
            "StatusDescription, ResponseTime, LastBoot "
            "FROM Orion.Nodes "
            "WHERE Caption LIKE @pattern OR IPAddress = @exact "
            "ORDER BY Caption"
        ),
        params={"pattern": _pattern(target), "exact": target},
    )


def _node_search(args: dict[str, Any], top: int) -> Built:
    fragment = _require(args, "fragment")["fragment"]
    return Built(
        swql=(
            f"SELECT TOP {top} Caption, IPAddress, Vendor, Status "
            "FROM Orion.Nodes "
            "WHERE Caption LIKE @pattern OR IPAddress LIKE @pattern OR Vendor LIKE @pattern "
            "ORDER BY Caption"
        ),
        params={"pattern": _pattern(fragment)},
    )


def _node_inventory(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} Caption, IPAddress, Vendor, MachineType, "
            "NodeDescription, Location, Contact, SysObjectID, Version, "
            "SerialNumber "
            "FROM Orion.Nodes "
            "WHERE Caption LIKE @pattern OR IPAddress = @exact"
        ),
        params={"pattern": _pattern(target), "exact": target},
    )


# --- Alerts and events ---------------------------------------------------

def _active_alerts(args: dict[str, Any], top: int) -> Built:
    node = str(args.get("node") or "").strip()
    if node:
        return Built(
            swql=(
                f"SELECT TOP {top} AlertObjectID, AlertName, Severity, "
                "TriggeredDateTime, AlertMessage, EntityCaption "
                "FROM Orion.AlertActive "
                "WHERE EntityCaption LIKE @pattern "
                "ORDER BY Severity DESC, TriggeredDateTime DESC"
            ),
            params={"pattern": _pattern(node)},
        )
    return Built(
        swql=(
            f"SELECT TOP {top} AlertObjectID, AlertName, Severity, "
            "TriggeredDateTime, AlertMessage, EntityCaption "
            "FROM Orion.AlertActive "
            "ORDER BY Severity DESC, TriggeredDateTime DESC"
        ),
        params={},
    )


def _recent_events(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} EventTime, EventType, Message, NetworkNode "
            "FROM Orion.Events "
            "WHERE NetworkNode LIKE @pattern "
            "ORDER BY EventTime DESC"
        ),
        params={"pattern": _pattern(target)},
    )


# --- Interfaces, volumes, applications, hardware --------------------------

def _interface_status(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} Node.Caption AS NodeCaption, Interfaces.Name, "
            "Interfaces.InterfaceAlias, Interfaces.OperStatus, "
            "Interfaces.AdminStatus, Interfaces.InPercentUtil, "
            "Interfaces.OutPercentUtil, Interfaces.TotalBandwidth "
            "FROM Orion.NPM.Interfaces AS Interfaces "
            "INNER JOIN Orion.Nodes AS Node ON Interfaces.NodeID = Node.NodeID "
            "WHERE Node.Caption LIKE @pattern "
            "ORDER BY Interfaces.Name"
        ),
        params={"pattern": _pattern(target)},
    )


def _volume_usage(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} Node.Caption AS NodeCaption, Volumes.Caption AS Volume, "
            "Volumes.VolumePercentUsed, Volumes.VolumeSize, Volumes.VolumeSpaceAvailable "
            "FROM Orion.Volumes AS Volumes "
            "INNER JOIN Orion.Nodes AS Node ON Volumes.NodeID = Node.NodeID "
            "WHERE Node.Caption LIKE @pattern "
            "ORDER BY Volumes.VolumePercentUsed DESC"
        ),
        params={"pattern": _pattern(target)},
    )


def _application_status(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} Node.Caption AS NodeCaption, Application.Name, "
            "Application.Status, Application.StatusDescription "
            "FROM Orion.APM.Application AS Application "
            "INNER JOIN Orion.Nodes AS Node ON Application.NodeID = Node.NodeID "
            "WHERE Node.Caption LIKE @pattern "
            "ORDER BY Application.Status DESC"
        ),
        params={"pattern": _pattern(target)},
    )


def _hardware_health(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    category = str(args.get("category") or "").strip()
    if category:
        return Built(
            swql=(
                f"SELECT TOP {top} Node.Caption AS NodeCaption, "
                "HardwareItem.Name, HardwareItem.HardwareCategoryName, "
                "HardwareItem.Status, HardwareItem.Value, "
                "HardwareItem.HardwareUnitDescription "
                "FROM Orion.HardwareHealth.HardwareItem AS HardwareItem "
                "INNER JOIN Orion.Nodes AS Node ON HardwareItem.NodeID = Node.NodeID "
                "WHERE Node.Caption LIKE @pattern "
                "AND HardwareItem.HardwareCategoryName LIKE @category "
                "ORDER BY HardwareItem.HardwareCategoryName"
            ),
            params={"pattern": _pattern(target), "category": _pattern(category)},
        )
    return Built(
        swql=(
            f"SELECT TOP {top} Node.Caption AS NodeCaption, "
            "HardwareItem.Name, HardwareItem.HardwareCategoryName, "
            "HardwareItem.Status, HardwareItem.Value, "
            "HardwareItem.HardwareUnitDescription "
            "FROM Orion.HardwareHealth.HardwareItem AS HardwareItem "
            "INNER JOIN Orion.Nodes AS Node ON HardwareItem.NodeID = Node.NodeID "
            "WHERE Node.Caption LIKE @pattern "
            "ORDER BY HardwareItem.HardwareCategoryName"
        ),
        params={"pattern": _pattern(target)},
    )


# --- Configuration (NCM) ------------------------------------------------

#: A whole configuration is the largest thing a lookup can hand the model: a few per call, each cut.
MAX_CONFIGS = 5
CONFIG_LINES = 200


def cut_config(text: Any) -> str:
    """The first CONFIG_LINES lines of a configuration, and how many were left out."""
    lines = str(text or "").splitlines()
    if len(lines) <= CONFIG_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:CONFIG_LINES]) + f"\n... {len(lines) - CONFIG_LINES} more lines not shown"


def _device_config(args: dict[str, Any], top: int) -> Built:
    """Reads whatever NCM already archived on its own schedule. Never triggers
    a live download -- that is a different NCM operation, and this module has
    no function that reaches it, consistent with the module docstring.

    At most MAX_CONFIGS per call, newest first; `before` (a DownloadTime from the
    previous answer) asks for the ones older than that."""
    target = _require(args, "node")["node"]
    params: dict[str, Any] = {"pattern": _pattern(target)}
    older = ""
    if str(args.get("before") or "").strip():
        params["before"], older = str(args["before"]).strip(), "AND Archive.DownloadTime < @before "  # bound, never in the text
    return Built(
        swql=(
            f"SELECT TOP {min(top, MAX_CONFIGS)} Node.Caption AS NodeCaption, "
            "Archive.ConfigType, Archive.DownloadTime, Archive.Config "
            "FROM Cirrus.ConfigArchive AS Archive "
            "INNER JOIN Cirrus.Nodes AS CNode ON Archive.NodeID = CNode.NodeID "
            "INNER JOIN Orion.Nodes AS Node ON CNode.CoreNodeID = Node.NodeID "
            f"WHERE Node.Caption LIKE @pattern {older}"
            "ORDER BY Archive.DownloadTime DESC"
        ),
        params=params,
    )


# --- NetFlow (NTA) -------------------------------------------------------

def _netflow_top_talkers(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} SourceIP, DestinationIP, SUM(Bytes) AS TotalBytes "
            "FROM Orion.Netflow.FlowsByConversation "
            "WHERE NodeID IN (SELECT NodeID FROM Orion.Nodes WHERE Caption LIKE @pattern) "
            "GROUP BY SourceIP, DestinationIP "
            "ORDER BY TotalBytes DESC"
        ),
        params={"pattern": _pattern(target)},
    )


def _netflow_by_ip(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "ip")["ip"]
    return Built(
        swql=(
            f"SELECT TOP {top} SourceIP, DestinationIP, SUM(Bytes) AS TotalBytes "
            "FROM Orion.Netflow.FlowsByIP "
            "WHERE SourceIP = @ip OR DestinationIP = @ip "
            "GROUP BY SourceIP, DestinationIP "
            "ORDER BY TotalBytes DESC"
        ),
        params={"ip": target},
    )


# --- Topology and endpoints ------------------------------------------------

def _device_neighbors(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "node")["node"]
    return Built(
        swql=(
            f"SELECT TOP {top} LocalNode.Caption AS LocalNode, "
            "Neighbors.LocalInterface, Neighbors.RemoteNodeCaption, "
            "Neighbors.RemoteInterface "
            "FROM Orion.Routing.Neighbors AS Neighbors "
            "INNER JOIN Orion.Nodes AS LocalNode ON Neighbors.NodeID = LocalNode.NodeID "
            "WHERE LocalNode.Caption LIKE @pattern"
        ),
        params={"pattern": _pattern(target)},
    )


def _device_port_location(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "identifier")["identifier"]
    return Built(
        swql=(
            f"SELECT TOP {top} Endpoint.MACAddress, Endpoint.IPAddress, "
            "Port.Name AS PortName, Node.Caption AS SwitchCaption, "
            "Endpoint.LastUpdate "
            "FROM Orion.UDT.Endpoint AS Endpoint "
            "INNER JOIN Orion.UDT.Port AS Port ON Endpoint.PortID = Port.PortID "
            "INNER JOIN Orion.Nodes AS Node ON Port.NodeID = Node.NodeID "
            "WHERE Endpoint.MACAddress = @mac OR Endpoint.IPAddress = @ip "
            "ORDER BY Endpoint.LastUpdate DESC"
        ),
        params={"mac": target, "ip": target},
    )


def _ip_lookup(args: dict[str, Any], top: int) -> Built:
    target = _require(args, "ip")["ip"]
    return Built(
        swql=(
            f"SELECT TOP {top} IPNode.IPAddress, IPNode.Status, Subnet.Address AS SubnetAddress, "
            "Subnet.CIDR, Subnet.VLAN, Subnet.FriendlyName "
            "FROM IPAM.IPNode AS IPNode "
            "INNER JOIN IPAM.Subnet AS Subnet ON IPNode.SubnetId = Subnet.SubnetId "
            "WHERE IPNode.IPAddress = @ip"
        ),
        params={"ip": target},
    )


# --- Bulk reads for the inventory (internal only) ---------------------------
# Keyset paging on NodeID; every value is bound, `TOP @page` included. The only
# thing that varies the text is `omit`, and it may only name columns from this
# module's own constant tuple -- so a column an instance lacks can be dropped
# without any caller-supplied string ever reaching the query. Column names are
# unverified against a live Orion; helpers/bulk.py reports any that get dropped.

NODE_OPTIONAL_COLUMNS = ("IOSVersion", "SysObjectID", "Location", "LastSystemUpTimePollUtc")
_NODE_CORE_COLUMNS = ("NodeID", "Caption", "DNS", "SysName", "IPAddress", "Vendor", "MachineType", "Status")
MAX_BULK_PAGE = 1000


def _int(args: dict[str, Any], name: str, low: int = 0, high: int | None = None) -> int:
    value = args.get(name)
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(value)
    except (TypeError, ValueError):
        raise CapabilityError(f"'{name}' must be an integer.") from None
    if number < low or (high is not None and number > high):
        raise CapabilityError(f"'{name}' is out of range.")
    return number


def _nodes_page(args: dict[str, Any], top: int) -> Built:
    omit = set(args.get("omit") or ())
    if not omit <= set(NODE_OPTIONAL_COLUMNS):
        raise CapabilityError("'omit' may only name known optional columns.")
    columns = ", ".join((*_NODE_CORE_COLUMNS, *(c for c in NODE_OPTIONAL_COLUMNS if c not in omit)))
    return Built(
        swql=f"SELECT TOP @page {columns} FROM Orion.Nodes WHERE NodeID > @after ORDER BY NodeID",
        params={"after": _int(args, "after"), "page": _int(args, "page", 1, MAX_BULK_PAGE)},
    )


def _id_range(args: dict[str, Any]) -> dict[str, Any]:
    return {"after": _int(args, "after"), "upto": _int(args, "upto")}


def _node_serials_page(args: dict[str, Any], top: int) -> Built:
    return Built(
        swql=(
            "SELECT NodeID, ServiceTag FROM Orion.HardwareHealth.HardwareInfo "
            "WHERE NodeID > @after AND NodeID <= @upto ORDER BY NodeID"
        ),
        params=_id_range(args),
    )


def _node_serials_ncm_page(args: dict[str, Any], top: int) -> Built:
    """Chassis serials NCM already collected (EntityClass 3 = chassis)."""
    return Built(
        swql=(
            "SELECT CNode.CoreNodeID AS NodeID, Physical.SerialNumber "
            "FROM Cirrus.EntityPhysical AS Physical "
            "INNER JOIN Cirrus.Nodes AS CNode ON Physical.NodeID = CNode.NodeID "
            "WHERE CNode.CoreNodeID > @after AND CNode.CoreNodeID <= @upto "
            "AND Physical.EntityClass = 3 AND Physical.SerialNumber <> '' "
            "ORDER BY CNode.CoreNodeID"
        ),
        params=_id_range(args),
    )


_CAPABILITIES: dict[str, Capability] = {
    c.name: c
    for c in (
        Capability("node_status", "Is this device up? Vendor, IP, last response time.", ("node",), (), _node_status),
        Capability("node_search", "Which nodes match a name/IP/vendor fragment?", ("fragment",), (), _node_search),
        Capability("node_inventory", "Full inventory for a device.", ("node",), (), _node_inventory),
        Capability("active_alerts", "What's currently alerting, and how severely?", (), ("node",), _active_alerts),
        Capability("recent_events", "What happened on this node recently?", ("node",), (), _recent_events),
        Capability("interface_status", "Are this device's interfaces up? Utilisation?", ("node",), (), _interface_status),
        Capability("volume_usage", "How full are this device's disks/volumes?", ("node",), (), _volume_usage),
        Capability("application_status", "Are this device's monitored (SAM) applications healthy?", ("node",), (), _application_status),
        Capability("hardware_health", "CPU, fan, temperature, power supply, voltage.", ("node",), ("category",), _hardware_health),
        Capability("device_config", "Most recent archived config (NCM). Never a live pull.", ("node",), ("before",), _device_config),
        Capability("netflow_top_talkers", "Which IP pairs move the most traffic through this device?", ("node",), (), _netflow_top_talkers),
        Capability("netflow_by_ip", "What is this IP sending or receiving?", ("ip",), (), _netflow_by_ip),
        Capability("device_neighbors", "What is this device directly connected to?", ("node",), (), _device_neighbors),
        Capability("device_port_location", "Which switch/port is this MAC or IP plugged into?", ("identifier",), (), _device_port_location),
        Capability("ip_lookup", "Is this IP in use? Which subnet/VLAN?", ("ip",), (), _ip_lookup),
        Capability("nodes_page", "One keyset page of nodes (bulk inventory).", ("after", "page"), ("omit",), _nodes_page, True),
        Capability("node_serials_page", "Hardware serials for a NodeID range.", ("after", "upto"), (), _node_serials_page, True),
        Capability("node_serials_ncm_page", "NCM chassis serials for a NodeID range.", ("after", "upto"), (), _node_serials_ncm_page, True),
    )
}


def names() -> tuple[str, ...]:
    return tuple(c.name for c in _CAPABILITIES.values() if not c.internal)


def describe() -> list[dict[str, Any]]:
    """For the prompt and the skill -- what exists and what it needs."""
    return [
        {
            "name": c.name,
            "summary": c.summary,
            "required": list(c.required),
            "optional": list(c.optional),
        }
        for c in _CAPABILITIES.values()
        if not c.internal
    ]


def build(capability: str, args: dict[str, Any] | None = None, *, internal: bool = False) -> Built:
    """The SWQL and bound parameters for one named capability.

    The only thing that decides which literal SWQL string is used is
    `capability` and, for a few templates, whether an *optional* argument was
    supplied at all -- never any argument's value. Every value is bound.
    """
    entry = _CAPABILITIES.get(str(capability or "").strip())
    if entry is None or (entry.internal and not internal):
        raise UnknownCapability(
            f"'{capability}' is not a capability. Available: {', '.join(names())}."
        )
    top = plugin_config.get_config()["max_rows"]
    return entry.build(dict(args or {}), top)

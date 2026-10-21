# nsg_rule: one Allow rule on an NSG that exists or that an earlier step of the same change set makes. The NSG is not read:
# the rule names it, and the executor proves it exists (templates.yaml `parents`). A later run may add a source, a
# destination or a port to this rule; it never takes one away.
variable "resource_group" { type = string }
variable "nsg_name" { type = string }
variable "name" { type = string }
variable "priority" { type = number }
variable "direction" { type = string }
variable "protocol" { type = string }
variable "sources" { type = list(object({ cidr = string })) }
variable "destinations" { type = list(object({ cidr = string })) }
variable "ports" { type = list(object({ range = string })) }

resource "azurerm_network_security_rule" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group
  network_security_group_name  = var.nsg_name
  priority                     = var.priority
  direction                    = var.direction
  access                       = "Allow"
  protocol                     = var.protocol
  source_port_range            = "*"
  source_address_prefixes      = [for s in var.sources : s.cidr]
  destination_address_prefixes = [for d in var.destinations : d.cidr]
  destination_port_ranges      = [for p in var.ports : p.range]
}

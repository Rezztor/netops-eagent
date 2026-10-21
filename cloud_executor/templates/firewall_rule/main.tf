# firewall_rule: one Allow rule in a firewall-policy rule collection group. A later run may add a source, a
# destination, a port or an FQDN to that rule; it never takes one away, never sets Deny, never touches DNAT.
# Written from documented output, not run against a device:
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/firewall_policy_rule_collection_group
variable "firewall_policy_id" { type = string }
variable "name" { type = string }
variable "priority" { type = number }
variable "collection_name" { type = string }
variable "collection_priority" { type = number }
variable "kind" { type = string }
variable "rule_name" { type = string }
variable "protocols" {
  type    = list(object({ name = string }))
  default = []
}
variable "app_protocol" {
  type    = string
  default = "Https"
}
variable "app_port" {
  type    = number
  default = 443
}
variable "sources" { type = list(object({ cidr = string })) }
variable "destinations" {
  type    = list(object({ cidr = string }))
  default = []
}
variable "ports" {
  type    = list(object({ range = string }))
  default = []
}
variable "fqdns" {
  type    = list(object({ name = string }))
  default = []
}

# unverified: resource and argument names written from the azurerm documentation, not validated.
resource "azurerm_firewall_policy_rule_collection_group" "this" {
  name               = var.name
  firewall_policy_id = var.firewall_policy_id
  priority           = var.priority

  dynamic "network_rule_collection" {
    for_each = var.kind == "network" ? [1] : []
    content {
      name     = var.collection_name
      priority = var.collection_priority
      action   = "Allow"
      rule {
        name                  = var.rule_name
        protocols             = [for p in var.protocols : p.name]
        source_addresses      = [for s in var.sources : s.cidr]
        destination_addresses = [for d in var.destinations : d.cidr]
        destination_ports     = [for p in var.ports : p.range]
      }
    }
  }

  dynamic "application_rule_collection" {
    for_each = var.kind == "application" ? [1] : []
    content {
      name     = var.collection_name
      priority = var.collection_priority
      action   = "Allow"
      rule {
        name              = var.rule_name
        source_addresses  = [for s in var.sources : s.cidr]
        destination_fqdns = [for f in var.fqdns : f.name]
        protocols {
          type = var.app_protocol
          port = var.app_port
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = contains(["network", "application"], var.kind)
      error_message = "kind must be network or application; DNAT and Deny are suggestions, not this template."
    }
    precondition {
      condition     = var.kind != "network" || (length(var.protocols) > 0 && length(var.destinations) > 0 && length(var.ports) > 0)
      error_message = "A network Allow rule needs protocols, destinations and ports."
    }
    precondition {
      condition     = var.kind != "application" || length(var.fqdns) > 0
      error_message = "An application Allow rule needs at least one FQDN."
    }
  }
}

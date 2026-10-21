# route: one route on a route table, which is either an existing one, or one made by an earlier step of the same change set,
# or a new one made in this change. Nothing is read: the route names its table, and the executor proves an existing table
# exists (templates.yaml `parents`).
variable "resource_group" { type = string }
variable "region" {
  type    = string
  default = ""
}
variable "route_table" { type = string }
variable "create_table" {
  type    = bool
  default = false
}
variable "name" { type = string }
variable "prefix" { type = string }
variable "next_hop_type" { type = string }
variable "next_hop_ip" {
  type    = string
  default = ""
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_route_table" "this" {
  count               = var.create_table ? 1 : 0
  name                = var.route_table
  resource_group_name = var.resource_group
  location            = var.region
  tags                = var.tags
}

resource "azurerm_route" "this" {
  name                   = var.name
  resource_group_name    = var.resource_group
  route_table_name       = var.route_table
  address_prefix         = var.prefix
  next_hop_type          = var.next_hop_type
  next_hop_in_ip_address = var.next_hop_type == "VirtualAppliance" ? var.next_hop_ip : null
  depends_on             = [azurerm_route_table.this]

  lifecycle {
    precondition {
      condition     = var.next_hop_type != "VirtualAppliance" || var.next_hop_ip != ""
      error_message = "A route to a virtual appliance needs the appliance's IP address."
    }
    precondition {
      condition     = !var.create_table || var.region != ""
      error_message = "A new route table needs a region."
    }
  }
}

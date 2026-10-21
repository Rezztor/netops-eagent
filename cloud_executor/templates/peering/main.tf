# peering: both sides of a peering between two virtual networks of this subscription, each of which may already exist or be
# made by an earlier step of the same change set. Nothing is read: the code names both networks (by a built ID), so the plan
# is the same either way, and the executor proves they exist (templates.yaml `parents`). Traffic between them and the
# VNet-access flag are always on.
variable "subscription_id" { type = string }
variable "resource_group" { type = string }
variable "local_vnet" { type = string }
variable "remote_vnet" { type = string }
variable "remote_resource_group" { type = string }
variable "forwarded_traffic" {
  type    = bool
  default = false
}
# true: the remote network is a hub whose gateway the local network uses
variable "hub_gateway" {
  type    = bool
  default = false
}

locals {
  near_id   = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group}/providers/Microsoft.Network/virtualNetworks/${var.local_vnet}"
  remote_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.remote_resource_group}/providers/Microsoft.Network/virtualNetworks/${var.remote_vnet}"
}

resource "azurerm_virtual_network_peering" "remote_to_local" {
  name                         = "to-${var.local_vnet}"
  resource_group_name          = var.remote_resource_group
  virtual_network_name         = var.remote_vnet
  remote_virtual_network_id    = local.near_id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = var.forwarded_traffic
  allow_gateway_transit        = var.hub_gateway
}

resource "azurerm_virtual_network_peering" "local_to_remote" {
  name                         = "to-${var.remote_vnet}"
  resource_group_name          = var.resource_group
  virtual_network_name         = var.local_vnet
  remote_virtual_network_id    = local.remote_id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = var.forwarded_traffic
  use_remote_gateways          = var.hub_gateway
  depends_on                   = [azurerm_virtual_network_peering.remote_to_local]
}

# private_endpoint: a private endpoint for one resource of this subscription, on an existing subnet, with its private
# DNS zone group. The DNS zone is made here unless an existing zone's ID is given; either way it is linked to the VNet.
# The subnet and the target are only referenced. The zone name follows Microsoft's private-endpoint DNS table:
# https://learn.microsoft.com/azure/private-link/private-endpoint-dns
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "vnet_name" { type = string }
variable "vnet_resource_group" {
  type    = string
  default = ""
}
variable "subnet_name" { type = string }
variable "target_id" { type = string }
variable "group_id" { type = string }
variable "zone_id" {
  type    = string
  default = ""
}
variable "tags" { type = map(string) }

locals {
  zones = {
    blob      = "privatelink.blob.core.windows.net"
    file      = "privatelink.file.core.windows.net"
    queue     = "privatelink.queue.core.windows.net"
    table     = "privatelink.table.core.windows.net"
    dfs       = "privatelink.dfs.core.windows.net"
    vault     = "privatelink.vaultcore.azure.net"
    sites     = "privatelink.azurewebsites.net"
    sqlServer = "privatelink.database.windows.net"
  }
  new_zone  = var.zone_id == ""
  zone_id   = local.new_zone ? azurerm_private_dns_zone.this[0].id : var.zone_id
  vnet_rg   = var.vnet_resource_group != "" ? var.vnet_resource_group : var.resource_group
}

data "azurerm_virtual_network" "this" {
  name                = var.vnet_name
  resource_group_name = local.vnet_rg
}

data "azurerm_subnet" "this" {
  name                 = var.subnet_name
  virtual_network_name = var.vnet_name
  resource_group_name  = local.vnet_rg
}

resource "azurerm_private_dns_zone" "this" {
  count               = local.new_zone ? 1 : 0
  name                = local.zones[var.group_id]
  resource_group_name = var.resource_group
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                = "link-${var.vnet_name}"
  # azurerm 5.x names the zone by its ID, and takes no resource group here (tofu validate on the pinned provider, 2026-10-04)
  private_dns_zone_id = local.zone_id
  virtual_network_id  = data.azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = var.tags
  depends_on            = [azurerm_private_dns_zone.this]
}

resource "azurerm_private_endpoint" "this" {
  name                = var.name
  resource_group_name = var.resource_group
  location            = var.region
  subnet_id           = data.azurerm_subnet.this.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = var.target_id
    subresource_names              = [var.group_id]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [local.zone_id]
  }
}

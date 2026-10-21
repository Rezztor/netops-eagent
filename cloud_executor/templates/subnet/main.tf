# subnet: one subnet in a virtual network, optionally tied to an NSG and route table of this resource group by name.
# Nothing is read: the network, the NSG and the route table may already exist or be made by an earlier step of the same
# change set, so the code names them (the NSG and route table by a built ID) and the plan is the same either way. The
# executor proves the parents exist (templates.yaml `parents`). An NSG or route table is attached only to the subnet this
# change makes. The five names Azure reserves for gateways, firewalls, Bastion and Route Server take no NSG or route table
# from here and a minimum size.
variable "subscription_id" { type = string }
variable "resource_group" { type = string }
variable "vnet_name" { type = string }
variable "name" { type = string }
variable "prefix" { type = string }
variable "nsg_name" {
  type    = string
  default = ""
}
variable "route_table_name" {
  type    = string
  default = ""
}

locals {
  # unverified: the smallest prefixes. /26 for the four, /27 for GatewaySubnet; Microsoft has published both /26 and /27
  # for RouteServerSubnet and the stricter one is used. Smaller prefix number = larger subnet.
  reserved = {
    AzureFirewallSubnet           = 26
    AzureFirewallManagementSubnet = 26
    AzureBastionSubnet            = 26
    RouteServerSubnet             = 26
    GatewaySubnet                 = 27
  }
  base = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group}/providers/Microsoft.Network"
}

# unverified: default_outbound_access_enabled = false on the reserved subnets; Azure may refuse it there.
resource "azurerm_subnet" "this" {
  name                              = var.name
  resource_group_name               = var.resource_group
  virtual_network_name              = var.vnet_name
  address_prefixes                  = [var.prefix]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = "Enabled"

  lifecycle {
    precondition {
      condition     = !contains(keys(local.reserved), var.name) || tonumber(split("/", var.prefix)[1]) <= local.reserved[var.name]
      error_message = "A reserved subnet name needs at least a /26 (GatewaySubnet a /27)."
    }
    precondition {
      condition     = !contains(keys(local.reserved), var.name) || (var.nsg_name == "" && var.route_table_name == "")
      error_message = "A reserved subnet takes no NSG and no route table from this template."
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "this" {
  count                     = var.nsg_name != "" ? 1 : 0
  subnet_id                 = azurerm_subnet.this.id
  network_security_group_id = "${local.base}/networkSecurityGroups/${var.nsg_name}"
}

resource "azurerm_subnet_route_table_association" "this" {
  count          = var.route_table_name != "" ? 1 : 0
  subnet_id      = azurerm_subnet.this.id
  route_table_id = "${local.base}/routeTables/${var.route_table_name}"
}

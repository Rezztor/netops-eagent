# vnet: a virtual network and, optionally, its first subnets. Subnets are separate resources, so adding one later
# never rewrites the network. The five names Azure reserves (AzureFirewallSubnet, AzureFirewallManagementSubnet,
# AzureBastionSubnet, RouteServerSubnet, GatewaySubnet) are accepted at their smallest prefix.
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "address_space" { type = list(object({ cidr = string })) }
variable "subnets" {
  type    = list(object({ name = string, prefix = string }))
  default = []
  # unverified: the smallest prefixes. /26 for the four, /27 for GatewaySubnet; Microsoft has published both /26 and
  # /27 for RouteServerSubnet and the stricter one is used. Smaller prefix number = larger subnet.
  validation {
    condition = alltrue([for s in var.subnets : tonumber(split("/", s.prefix)[1]) <= lookup({
      AzureFirewallSubnet           = 26
      AzureFirewallManagementSubnet = 26
      AzureBastionSubnet            = 26
      RouteServerSubnet             = 26
      GatewaySubnet                 = 27
    }, s.name, 32)])
    error_message = "A reserved subnet name needs at least a /26 (GatewaySubnet a /27)."
  }
}
variable "tags" { type = map(string) }

resource "azurerm_virtual_network" "this" {
  name                = var.name
  resource_group_name = var.resource_group
  location            = var.region
  address_space       = [for a in var.address_space : a.cidr]
  tags                = var.tags
}

# unverified: default_outbound_access_enabled = false on the reserved subnets; Azure may refuse it there.
resource "azurerm_subnet" "this" {
  for_each                          = { for s in var.subnets : s.name => s }
  name                              = each.value.name
  resource_group_name               = var.resource_group
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = [each.value.prefix]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = "Enabled"
}

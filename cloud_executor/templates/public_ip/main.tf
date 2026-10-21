# public_ip: one static Standard public IPv4 address, not attached to anything.
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "tags" { type = map(string) }

resource "azurerm_public_ip" "this" {
  name                = var.name
  resource_group_name = var.resource_group
  location            = var.region
  allocation_method   = "Static"
  sku                 = "Standard"
  ip_version          = "IPv4"
  tags                = var.tags
}

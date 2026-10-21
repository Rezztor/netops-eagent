# nsg: a network security group of its own, with no rules in it. Rules are separate resources (template nsg_rule), so a rule
# added later never rewrites the group. The group does nothing until a subnet is tied to it, and this template ties it to
# nothing: the subnet template attaches it, only to a subnet that change makes.
variable "resource_group" { type = string }
variable "name" { type = string }
variable "region" { type = string }
variable "tags" { type = map(string) }

resource "azurerm_network_security_group" "this" {
  name                = var.name
  resource_group_name = var.resource_group
  location            = var.region
  tags                = var.tags
}

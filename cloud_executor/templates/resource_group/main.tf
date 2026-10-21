# resource_group: one resource group. Adds, and may change its tags.
variable "resource_group" { type = string } # the workspace's, never the model's
variable "region" { type = string }
variable "tags" { type = map(string) }

resource "azurerm_resource_group" "this" {
  name     = var.resource_group
  location = var.region
  tags     = var.tags
}

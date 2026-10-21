# tags: add or change tags on an existing resource group. azapi_update_resource merges this body into what Azure
# already has, so no existing tag is dropped, and it makes no resource of its own.
variable "subscription_id" { type = string } # the workspace's
variable "resource_group" { type = string }
variable "tags" { type = map(string) }

# unverified: that azapi 2.13 merges the tag map into an existing group and sends no PUT that drops a tag; the plan
# policy still refuses any plan that shrinks the map.
resource "azapi_update_resource" "this" {
  type        = "Microsoft.Resources/resourceGroups@2024-03-01"
  resource_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group}"
  body = {
    tags = var.tags
  }
}

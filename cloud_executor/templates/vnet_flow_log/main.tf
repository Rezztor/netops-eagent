# vnet_flow_log: a VNet flow log into a storage account, with optional Traffic Analytics. NSG flow logs cannot be
# created any more and retire on 30 September 2027, so this template never makes one. The Network Watcher is only
# named, the storage account and workspace only referenced.
variable "region" { type = string }
variable "name" { type = string }
variable "network_watcher" { type = string }
variable "network_watcher_rg" {
  type    = string
  default = "NetworkWatcherRG"
}
variable "vnet_id" { type = string }
variable "storage_id" { type = string }
variable "retention_days" {
  type    = number
  default = 30
}
variable "analytics_workspace_guid" {
  type    = string
  default = ""
}
variable "analytics_workspace_id" {
  type    = string
  default = ""
}
variable "tags" { type = map(string) }

# unverified: argument names written from the azurerm 4.x and 5.x documentation (target_resource_id, retention_policy);
# `tofu validate` in test session 2 settles them.
resource "azurerm_network_watcher_flow_log" "this" {
  name                 = var.name
  network_watcher_name = var.network_watcher
  resource_group_name  = var.network_watcher_rg
  location             = var.region
  target_resource_id   = var.vnet_id
  storage_account_id   = var.storage_id
  enabled              = true
  version              = 2
  tags                 = var.tags

  retention_policy {
    enabled = true
    days    = var.retention_days
  }

  dynamic "traffic_analytics" {
    for_each = var.analytics_workspace_id != "" ? [1] : []
    content {
      enabled               = true
      workspace_id          = var.analytics_workspace_guid
      workspace_region      = var.region
      workspace_resource_id = var.analytics_workspace_id
      interval_in_minutes   = 10
    }
  }
}

# log_workspace: a Log Analytics workspace with a daily cap. Ingestion and query over the public network stay on
# (Azure's default) because diagnostic settings, agents and the product's log queries need them.
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "retention_days" {
  type    = number
  default = 30
}
variable "daily_quota_gb" { type = number }
variable "tags" { type = map(string) }

resource "azurerm_log_analytics_workspace" "this" {
  name                = var.name
  resource_group_name = var.resource_group
  location            = var.region
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  daily_quota_gb      = var.daily_quota_gb
  tags                = var.tags
}

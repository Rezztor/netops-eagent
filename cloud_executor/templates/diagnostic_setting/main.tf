# diagnostic_setting: send one existing resource's logs and metrics to a Log Analytics workspace. A web app is refused
# by the target pattern: Azure cycles a running web app when the setting lands, so there it is a suggestion.
variable "name" { type = string }
variable "target_id" {
  type = string
  validation {
    condition     = !can(regex("(?i)/providers/microsoft\\.web/sites", var.target_id))
    error_message = "A web app is not a target for this template; ask for a suggestion instead."
  }
}
variable "workspace_id" { type = string }
variable "logs" { type = string }
variable "metrics" {
  type    = bool
  default = true
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = var.name
  target_resource_id         = var.target_id
  log_analytics_workspace_id = var.workspace_id

  enabled_log {
    category_group = var.logs
  }

  dynamic "enabled_metric" {
    for_each = var.metrics ? ["AllMetrics"] : []
    content {
      category = enabled_metric.value
    }
  }
}

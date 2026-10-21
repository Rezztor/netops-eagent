# budget: a monthly Cost Management budget on the subscription or on the workspace's resource group, with e-mail
# alerts. A budget only warns; it caps nothing.
variable "subscription_id" { type = string } # the workspace's
variable "resource_group" { type = string }
variable "name" { type = string }
variable "scope" { type = string }
variable "amount" { type = number }
variable "start_month" { type = string }
variable "alert_percents" { type = list(object({ percent = number })) }
variable "emails" { type = list(object({ address = string })) }

locals {
  emails = [for e in var.emails : e.address]
  start  = "${var.start_month}T00:00:00Z"
}

# unverified: resource and argument names written from the azurerm documentation, not validated.
resource "azurerm_consumption_budget_subscription" "this" {
  count           = var.scope == "subscription" ? 1 : 0
  name            = var.name
  subscription_id = "/subscriptions/${var.subscription_id}"
  amount          = var.amount
  time_grain      = "Monthly"

  time_period {
    start_date = local.start
  }

  dynamic "notification" {
    for_each = var.alert_percents
    content {
      enabled        = true
      threshold      = notification.value.percent
      operator       = "GreaterThan"
      threshold_type = "Actual"
      contact_emails = local.emails
    }
  }
}

resource "azurerm_consumption_budget_resource_group" "this" {
  count             = var.scope == "resource_group" ? 1 : 0
  name              = var.name
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group}"
  amount            = var.amount
  time_grain        = "Monthly"

  time_period {
    start_date = local.start
  }

  dynamic "notification" {
    for_each = var.alert_percents
    content {
      enabled        = true
      threshold      = notification.value.percent
      operator       = "GreaterThan"
      threshold_type = "Actual"
      contact_emails = local.emails
    }
  }
}

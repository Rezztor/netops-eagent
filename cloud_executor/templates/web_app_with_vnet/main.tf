# web_app_with_vnet: a resource group, a VNet with subnets, an NSG on each subnet that is not delegated to the web app,
# a Linux plan and a Linux web app joined to the delegated subnet. Subnets and NSG associations are separate resources.
# The web app is closed to the public network and HTTPS only; reach it through a private endpoint (template
# private_endpoint) on a subnet of this network.
variable "resource_group" { type = string }
variable "name" { type = string }
variable "env" { type = string }
variable "region" { type = string }
variable "vnet" { type = string }
variable "subnets" {
  type = list(object({
    name       = string
    prefix     = string
    delegation = optional(string, "")
  }))
  validation {
    condition     = length([for s in var.subnets : s if s.delegation == "web"]) == 1
    error_message = "Exactly one subnet must be delegated to the web app (delegation web)."
  }
}
variable "plan_sku" { type = string }
variable "runtime" { type = string }
variable "tags" { type = map(string) }

locals {
  suffix       = "${var.name}-${var.env}"
  web_subnet   = [for s in var.subnets : s.name if s.delegation == "web"][0]
  plain        = { for s in var.subnets : s.name => s if s.delegation == "" }
  runtime_kind = split("|", var.runtime)[0]
  runtime_ver  = split("|", var.runtime)[1]
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group
  location = var.region
  tags     = var.tags
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.vnet]
  tags                = var.tags
}

resource "azurerm_subnet" "this" {
  for_each                          = { for s in var.subnets : s.name => s }
  name                              = each.value.name
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = [each.value.prefix]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = "Enabled"

  dynamic "delegation" {
    for_each = each.value.delegation == "web" ? [1] : []
    content {
      name = "web"
      service_delegation {
        name    = "Microsoft.Web/serverFarms"
        actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
      }
    }
  }
}

resource "azurerm_network_security_group" "this" {
  for_each            = local.plain
  name                = "nsg-${each.key}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = var.tags
}

resource "azurerm_subnet_network_security_group_association" "this" {
  for_each                  = local.plain
  subnet_id                 = azurerm_subnet.this[each.key].id
  network_security_group_id = azurerm_network_security_group.this[each.key].id
}

resource "azurerm_service_plan" "this" {
  name                = "asp-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  os_type             = "Linux"
  sku_name            = var.plan_sku
  tags                = var.tags
}

# unverified: azurerm 5.8 argument names written from the 4.x documentation, not validated.
resource "azurerm_linux_web_app" "this" {
  name                                           = "app-${local.suffix}"
  resource_group_name                            = azurerm_resource_group.this.name
  location                                       = azurerm_resource_group.this.location
  service_plan_id                                = azurerm_service_plan.this.id
  virtual_network_subnet_id                      = azurerm_subnet.this[local.web_subnet].id
  https_only                                     = true
  public_network_access_enabled                  = false
  client_affinity_enabled                        = false
  ftp_publish_basic_authentication_enabled       = false
  webdeploy_publish_basic_authentication_enabled = false
  tags                                           = var.tags

  site_config {
    minimum_tls_version    = "1.2"
    ftps_state             = "Disabled"
    http2_enabled          = true
    vnet_route_all_enabled = true

    application_stack {
      python_version = local.runtime_kind == "PYTHON" ? local.runtime_ver : null
      node_version   = local.runtime_kind == "NODE" ? local.runtime_ver : null
      dotnet_version = local.runtime_kind == "DOTNETCORE" ? local.runtime_ver : null
    }
  }
}

# storage_account: a StorageV2 account closed to the public network, HTTPS and TLS 1.2 only, no shared key (Entra ID
# only), no public blobs, soft delete kept for seven days. Reach it through a private endpoint (template private_endpoint).
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "replication" { type = string }
variable "tags" { type = map(string) }

# unverified: with no shared key the provider block must use Entra ID for the data plane (storage_use_azuread = true);
# whether azurerm then creates the account with the change role alone is proven in test session 2.
resource "azurerm_storage_account" "this" {
  name                             = var.name
  resource_group_name              = var.resource_group
  location                         = var.region
  account_kind                     = "StorageV2"
  account_tier                     = "Standard"
  account_replication_type         = var.replication
  https_traffic_only_enabled       = true
  min_tls_version                  = "TLS1_2"
  shared_access_key_enabled        = false
  default_to_oauth_authentication  = true
  allow_nested_items_to_be_public  = false
  cross_tenant_replication_enabled = false
  public_network_access_enabled    = false
  tags                             = var.tags

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }
}

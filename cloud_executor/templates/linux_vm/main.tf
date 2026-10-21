# linux_vm: a Linux VM on an existing subnet. No public IP, SSH key only (public half, validated), no custom data, no
# extension, Trusted Launch. The subnet is only read.
variable "resource_group" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "vnet_name" { type = string }
variable "vnet_resource_group" {
  type    = string
  default = ""
}
variable "subnet_name" { type = string }
variable "size" { type = string }
variable "image" { type = string }
variable "admin_username" { type = string }
variable "ssh_public_key" { type = string }
variable "os_disk_gb" {
  type    = number
  default = 30
}
variable "tags" { type = map(string) }

locals {
  images = {
    ubuntu_2404 = { publisher = "Canonical", offer = "ubuntu-24_04-lts", sku = "server" }
    debian_12   = { publisher = "Debian", offer = "debian-12", sku = "12-gen2" }
  }
}

data "azurerm_subnet" "this" {
  name                 = var.subnet_name
  virtual_network_name = var.vnet_name
  resource_group_name  = var.vnet_resource_group != "" ? var.vnet_resource_group : var.resource_group
}

resource "azurerm_network_interface" "this" {
  name                = "nic-${var.name}"
  resource_group_name = var.resource_group
  location            = var.region
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = data.azurerm_subnet.this.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "this" {
  name                            = var.name
  resource_group_name             = var.resource_group
  location                        = var.region
  size                            = var.size
  admin_username                  = var.admin_username
  network_interface_ids           = [azurerm_network_interface.this.id]
  disable_password_authentication = true
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  tags                            = var.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.os_disk_gb
  }

  source_image_reference {
    publisher = local.images[var.image].publisher
    offer     = local.images[var.image].offer
    sku       = local.images[var.image].sku
    version   = "latest"
  }

  boot_diagnostics {}
}

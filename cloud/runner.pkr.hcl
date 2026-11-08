packer {
  required_version = ">= 1.16.2"
  required_plugins {
    googlecompute = {
      version = "~> 1.2.9"
      source  = "github.com/hashicorp/googlecompute"
    }
    azure = {
      version = "~> 2.6.5"
      source  = "github.com/hashicorp/azure"
    }
  }
}
variable "provider" { type = string }
variable "build_id" { type = string }
variable "version" { type = string }
variable "source_image" { type = string }
variable "source_version" { type = string }
variable "output_directory" { type = string }
variable "source_commit" { type = string }
variable "input_id" { type = string }
variable "toolchains" { type = map(map(string)) }
variable "builder_ip" { type = string }
variable "gcp_project" { type = string }
variable "gcp_zone" { type = string }
variable "gcp_network" { type = string }
variable "gcp_machine_type" { type = string }
variable "azure_subscription" { type = string }
variable "azure_location" { type = string }
variable "azure_vm_size" { type = string }
variable "azure_build_resource_group" { type = string }
variable "azure_resource_group" { type = string }
variable "azure_gallery" { type = string }
variable "azure_definition" { type = string }

source "googlecompute" "ubuntu" {
  project_id                      = var.gcp_project
  zone                            = var.gcp_zone
  network                         = var.gcp_network
  source_image                    = var.source_image
  source_image_project_id         = ["ubuntu-os-cloud"]
  instance_name                   = var.build_id
  image_name                      = var.build_id
  image_description               = "Ubuntu 24.04 runner base; provisioning smoke checks only"
  image_labels                    = { recipe = substr(var.input_id, 0, 40), source = var.source_commit, owner = "runner-images", build = var.build_id }
  labels                          = { owner = "runner-images", build = var.build_id }
  tags                            = [var.build_id]
  metadata                        = { enable-oslogin = "FALSE", block-project-ssh-keys = "TRUE" }
  machine_type                    = var.gcp_machine_type
  disk_size                       = 64
  disk_type                       = "pd-balanced"
  ssh_username                    = "runnerbuilder"
  disable_default_service_account = true
  ssh_timeout                     = "10m"
}
source "azure-arm" "ubuntu" {
  use_azure_cli_auth           = true
  subscription_id              = var.azure_subscription
  build_resource_group_name    = var.azure_build_resource_group
  os_type                      = "Linux"
  image_publisher              = "Canonical"
  image_offer                  = "ubuntu-24_04-lts"
  image_sku                    = "server"
  image_version                = var.source_version
  vm_size                      = var.azure_vm_size
  os_disk_size_gb              = 64
  ssh_username                 = "runnerbuilder"
  allowed_inbound_ip_addresses = ["${var.builder_ip}/32"]
  azure_tags                   = { owner = "runner-images", build = var.build_id, recipe = var.input_id, source = var.source_commit }
  shared_image_gallery_destination {
    subscription         = var.azure_subscription
    resource_group       = var.azure_resource_group
    gallery_name         = var.azure_gallery
    image_name           = var.azure_definition
    image_version        = var.version
    replication_regions  = [var.azure_location]
    storage_account_type = "Standard_LRS"
  }
}
build {
  sources = ["source.googlecompute.ubuntu", "source.azure-arm.ubuntu"]
  provisioner "shell-local" {
    only   = ["googlecompute.ubuntu"]
    inline = ["gcloud compute disks add-labels '${var.build_id}' --project '${var.gcp_project}' --zone '${var.gcp_zone}' --labels 'owner=runner-images,build=${var.build_id}' --quiet"]
  }
  provisioner "shell" {
    inline = ["mkdir -p /tmp/runner-source /tmp/runner-evidence"]
  }
  provisioner "file" {
    source      = "runner_images"
    destination = "/tmp/runner-source/"
  }
  provisioner "file" {
    source      = "cloud/linux/install-toolchains.py"
    destination = "/tmp/runner-source/install-toolchains.py"
  }
  provisioner "file" {
    content     = jsonencode(var.toolchains)
    destination = "/tmp/runner-source/toolchains-plan.json"
  }
  provisioner "shell" {
    scripts = ["cloud/linux/provision.sh", "cloud/linux/verify.sh"]
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/runner-evidence/inventory.json"
    destination = "${var.output_directory}inventory.json"
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/runner-evidence/toolchain-check.txt"
    destination = "${var.output_directory}toolchain-check.txt"
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/runner-evidence/sbom.spdx.json"
    destination = "${var.output_directory}sbom.spdx.json"
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/runner-evidence/sbom.syft.json"
    destination = "${var.output_directory}sbom.syft.json"
  }
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/runner-evidence/toolchains.json"
    destination = "${var.output_directory}toolchains.json"
  }
  provisioner "shell" {
    inline = ["sudo rm -rf /tmp/runner-source /tmp/runner-evidence /tmp/runner-tools /tmp/actions-runner.tar.gz /tmp/syft.tar.gz /tmp/runner-release.json /tmp/syft-release.json /tmp/runner-docker.asc", "sudo rm -f /home/runnerbuilder/.ssh/authorized_keys", "sudo apt-get clean", "sudo cloud-init clean --logs --seed", "sudo truncate -s 0 /etc/machine-id"]
  }
  provisioner "shell" {
    only   = ["googlecompute.ubuntu"]
    inline = ["sudo rm -f /etc/ssh/ssh_host_*", "sudo sync"]
  }
  provisioner "shell" {
    only   = ["azure-arm.ubuntu"]
    inline = ["sudo /usr/sbin/waagent -force -deprovision+user && sync"]
  }
  post-processor "manifest" {
    output     = "${var.output_directory}/packer-manifest.json"
    strip_path = true
  }
}

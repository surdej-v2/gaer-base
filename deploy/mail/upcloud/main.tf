# Phase 1 of the mail module (specs/mail): Stalwart on UpCloud, in one EU zone.
#
#   private network   Stalwart, the managed database and (attach it yourself) the app server talk privately
#   cloud server      Stalwart in Docker: MX, IMAP, submission, JMAP/CalDAV/CardDAV, outbound queue
#   PostgreSQL        UpCloud Managed Database: accounts, folders, calendars, address books, metadata, search
#   Object Storage    UpCloud Managed Object Storage: message bodies, attachments, events and contact cards
#
# Lettermint is SaaS: Stalwart relays every outbound mail to it (deploy/mail/config.toml). No lifecycle rule goes on
# the Stalwart bucket: it holds events and contacts too, and mail expires through Stalwart (specs/mail/README.md).
#
#   cd deploy/mail/upcloud
#   export UPCLOUD_TOKEN=…                 # API token of a sub-account with server, database and storage rights
#   cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars
#   terraform init && terraform apply
#   terraform output -json > ../outputs.json   # read by deploy/mail/render-env.sh

terraform {
  required_version = ">= 1.6"
  required_providers {
    upcloud = {
      source  = "UpCloudLtd/upcloud"
      version = "~> 5.15"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "upcloud" {}

locals {
  name = "${var.prefix}-mail"
  # Ports clients and other mail servers use. Everything else is closed, apart from SSH from admin_cidrs and the
  # management listener (8443) from the private network and management_cidrs.
  public_tcp = {
    "25"  = "SMTP (MX, indgående mail)"
    "465" = "SMTP submission med TLS"
    "587" = "SMTP submission med STARTTLS"
    "993" = "IMAP med TLS"
    "443" = "HTTPS: JMAP, CalDAV, CardDAV, autoconfig, ACME"
  }
}

# ── Network ─────────────────────────────────────────────────────────────────

resource "upcloud_router" "mail" {
  name = "${local.name}-router"
}

resource "upcloud_network" "mail" {
  name   = "${local.name}-net"
  zone   = var.zone
  router = upcloud_router.mail.id

  ip_network {
    address = var.private_cidr
    dhcp    = true
    family  = "IPv4"
  }
}

# ── Stalwart server ─────────────────────────────────────────────────────────

resource "upcloud_server" "stalwart" {
  hostname = var.mail_hostname
  title    = "${local.name} (Stalwart)"
  zone     = var.zone
  plan     = var.server_plan
  firewall = true
  metadata = true

  template {
    storage = "Ubuntu Server 24.04 LTS (Noble Numbat)"
    size    = var.server_disk_gb
    backup_rule {
      interval  = "daily"
      time      = "0300"
      retention = 7
    }
  }

  network_interface {
    type = "public"
  }

  network_interface {
    type    = "private"
    network = upcloud_network.mail.id
  }

  login {
    user = "root"
    keys = var.ssh_keys
  }

  user_data = templatefile("${path.module}/cloud-init.yaml", { hostname = var.mail_hostname })

  labels = { app = "gaer", role = "mail" }
}

resource "upcloud_firewall_rules" "stalwart" {
  server_id = upcloud_server.stalwart.id

  dynamic "firewall_rule" {
    for_each = local.public_tcp
    content {
      action                 = "accept"
      direction              = "in"
      family                 = "IPv4"
      protocol               = "tcp"
      destination_port_start = firewall_rule.key
      destination_port_end   = firewall_rule.key
      comment                = firewall_rule.value
    }
  }

  dynamic "firewall_rule" {
    for_each = local.public_tcp
    content {
      action                 = "accept"
      direction              = "in"
      family                 = "IPv6"
      protocol               = "tcp"
      destination_port_start = firewall_rule.key
      destination_port_end   = firewall_rule.key
      comment                = firewall_rule.value
    }
  }

  dynamic "firewall_rule" {
    for_each = var.admin_cidrs
    content {
      action                 = "accept"
      direction              = "in"
      family                 = "IPv4"
      protocol               = "tcp"
      source_address_start   = cidrhost(firewall_rule.value, 0)
      source_address_end     = cidrhost(firewall_rule.value, -1)
      destination_port_start = "22"
      destination_port_end   = "22"
      comment                = "SSH fra drift"
    }
  }

  # Stalwart's management listener (8443, TLS) on the public interface: only the app's public IP. This firewall
  # filters the public interface; over the private network the app reaches 8443 directly.
  dynamic "firewall_rule" {
    for_each = var.management_cidrs
    content {
      action                 = "accept"
      direction              = "in"
      family                 = "IPv4"
      protocol               = "tcp"
      source_address_start   = cidrhost(firewall_rule.value, 0)
      source_address_end     = cidrhost(firewall_rule.value, -1)
      destination_port_start = "8443"
      destination_port_end   = "8443"
      comment                = "Stalwart-administration fra appen"
    }
  }

  firewall_rule {
    action    = "accept"
    direction = "in"
    family    = "IPv4"
    protocol  = "icmp"
    comment   = "Ping"
  }

  # Answers from DNS and NTP servers (UDP).
  firewall_rule {
    action            = "accept"
    direction         = "in"
    family            = "IPv4"
    protocol          = "udp"
    source_port_start = "53"
    source_port_end   = "53"
    comment           = "DNS-svar"
  }

  firewall_rule {
    action            = "accept"
    direction         = "in"
    family            = "IPv4"
    protocol          = "udp"
    source_port_start = "123"
    source_port_end   = "123"
    comment           = "NTP-svar"
  }

  firewall_rule {
    action    = "drop"
    direction = "in"
    comment   = "Alt andet"
  }
}

# ── PostgreSQL (Stalwart's data store) ──────────────────────────────────────

resource "upcloud_managed_database_postgresql" "stalwart" {
  name  = "${local.name}-pg"
  title = "${local.name} PostgreSQL (Stalwart)"
  plan  = var.database_plan
  zone  = var.zone

  properties {
    version                             = "16"
    public_access                       = false
    ip_filter                           = [var.private_cidr]
    automatic_utility_network_ip_filter = false
    # Backups stay short: a restore must never bring back mail older than the 30-day retention (specs/mail).
    backup_hour   = 2
    backup_minute = 30
  }

  network {
    family = "IPv4"
    name   = "${local.name}-pg-net"
    type   = "private"
    uuid   = upcloud_network.mail.id
  }

  labels = { app = "gaer", role = "mail" }
}

resource "upcloud_managed_database_logical_database" "stalwart" {
  service = upcloud_managed_database_postgresql.stalwart.id
  name    = "stalwart"
}

resource "random_password" "db" {
  length  = 40
  special = false
}

resource "upcloud_managed_database_user" "stalwart" {
  service  = upcloud_managed_database_postgresql.stalwart.id
  username = "stalwart"
  password = random_password.db.result
}

# ── Object Storage (Stalwart's blob store) ──────────────────────────────────

resource "upcloud_managed_object_storage" "mail" {
  name              = "${local.name}-s3"
  region            = var.object_storage_region
  configured_status = "started"

  network {
    family = "IPv4"
    name   = "public"
    type   = "public"
  }

  labels = { app = "gaer", role = "mail" }
}

resource "upcloud_managed_object_storage_bucket" "blobs" {
  service_uuid = upcloud_managed_object_storage.mail.id
  name         = var.blob_bucket
}

resource "upcloud_managed_object_storage_user" "stalwart" {
  service_uuid = upcloud_managed_object_storage.mail.id
  username     = "stalwart"
}

resource "upcloud_managed_object_storage_policy" "stalwart" {
  service_uuid = upcloud_managed_object_storage.mail.id
  name         = "stalwart-blobs"
  description  = "Stalwart: read and write its own bucket only"
  document = urlencode(jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.blob_bucket}", "arn:aws:s3:::${var.blob_bucket}/*"]
      }
    ]
  }))
}

resource "upcloud_managed_object_storage_user_policy" "stalwart" {
  service_uuid = upcloud_managed_object_storage.mail.id
  username     = upcloud_managed_object_storage_user.stalwart.username
  name         = upcloud_managed_object_storage_policy.stalwart.name
}

resource "upcloud_managed_object_storage_user_access_key" "stalwart" {
  service_uuid = upcloud_managed_object_storage.mail.id
  username     = upcloud_managed_object_storage_user.stalwart.username
  status       = "Active"
}

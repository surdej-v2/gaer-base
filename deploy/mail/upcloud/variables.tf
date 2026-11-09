variable "prefix" {
  description = "Prefix for every resource name, e.g. the customer: \"kunde\""
  type        = string
}

variable "zone" {
  description = "UpCloud zone (EU): fi-hel1, fi-hel2, se-sto1, nl-ams1, de-fra1 …"
  type        = string
  default     = "de-fra1"
}

variable "object_storage_region" {
  description = "Managed Object Storage region in the same part of the EU: europe-1 (Helsinki), europe-2 (Frankfurt), europe-3 (Stockholm)"
  type        = string
  default     = "europe-2"
}

variable "mail_hostname" {
  description = "The public host: MX target, client host and TLS certificate, e.g. mail.kunde.dk"
  type        = string
}

variable "private_cidr" {
  description = "The private network shared by Stalwart, the database and the app server"
  type        = string
  default     = "10.42.0.0/24"
}

variable "server_plan" {
  description = "Stalwart server plan (one node to start with)"
  type        = string
  default     = "2xCPU-4GB"
}

variable "server_disk_gb" {
  description = "System disk; mail lives in PostgreSQL and Object Storage, so this only holds Docker, config and certificates"
  type        = number
  default     = 50
}

variable "database_plan" {
  description = "Managed PostgreSQL plan (list them with: upctl database plans pg)"
  type        = string
  default     = "1x1xCPU-2GB-25GB"
}

variable "blob_bucket" {
  description = "Bucket for Stalwart's blobs (message bodies, attachments, events, contact cards). Never a lifecycle rule here."
  type        = string
  default     = "stalwart-blobs"
}

variable "ssh_keys" {
  description = "Public SSH keys for root on the Stalwart server"
  type        = list(string)
}

variable "admin_cidrs" {
  description = "Where SSH is allowed from (operators, the CI runner if it deploys)"
  type        = list(string)
  default     = []
}

variable "management_cidrs" {
  description = "Public IPs (CIDR) that may reach Stalwart's management listener (8443) on the public interface: the app server, if it is not attached to the private network"
  type        = list(string)
  default     = []
}

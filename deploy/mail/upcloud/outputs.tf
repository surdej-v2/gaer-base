output "server_public_ip" {
  description = "Point MAIL_HOSTNAME's A record here, and set reverse DNS to MAIL_HOSTNAME in the UpCloud panel"
  value       = [for i in upcloud_server.stalwart.network_interface : i.ip_address if i.type == "public"][0]
}

output "server_private_ip" {
  value = [for i in upcloud_server.stalwart.network_interface : i.ip_address if i.type == "private"][0]
}

output "network_id" {
  description = "Attach the app server to this network (UpCloud panel → server → Network), then add its private IP to management_cidrs"
  value       = upcloud_network.mail.id
}

output "db_host" {
  value = upcloud_managed_database_postgresql.stalwart.service_host
}

output "db_port" {
  value = upcloud_managed_database_postgresql.stalwart.service_port
}

output "db_name" {
  value = upcloud_managed_database_logical_database.stalwart.name
}

output "db_user" {
  value = upcloud_managed_database_user.stalwart.username
}

output "db_password" {
  value     = random_password.db.result
  sensitive = true
}

output "s3_endpoint" {
  value = "https://${[for e in upcloud_managed_object_storage.mail.endpoint : e.domain_name if e.type == "public"][0]}"
}

output "s3_region" {
  value = var.object_storage_region
}

output "s3_bucket" {
  value = upcloud_managed_object_storage_bucket.blobs.name
}

output "s3_access_key" {
  value     = upcloud_managed_object_storage_user_access_key.stalwart.access_key_id
  sensitive = true
}

output "s3_secret_key" {
  value     = upcloud_managed_object_storage_user_access_key.stalwart.secret_access_key
  sensitive = true
}

output "mail_hostname" {
  value = var.mail_hostname
}

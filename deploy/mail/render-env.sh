#!/usr/bin/env bash
# Writes stalwart.env from the Terraform outputs (run on your machine in deploy/mail after terraform apply).
# Generates the admin and master secrets; leaves STALWART_IMAGE, ACME_CONTACT and the Lettermint SMTP login for you.
#
#   (cd upcloud && terraform output -json) > outputs.json
#   ./render-env.sh outputs.json > stalwart.env      # then fill in the empty values, and keep it out of git
set -euo pipefail
out=${1:?usage: render-env.sh outputs.json}
get() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["value"])' "$out" "$1"; }
secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
host=$(get mail_hostname)
cat <<ENV
STALWART_IMAGE=
MAIL_HOSTNAME=$host
ACME_CONTACT=mailto:postmaster@${host#*.}
STALWART_DB_HOST=$(get db_host)
STALWART_DB_PORT=$(get db_port)
STALWART_DB_NAME=$(get db_name)
STALWART_DB_USER=$(get db_user)
STALWART_DB_PASSWORD=$(get db_password)
STALWART_S3_ENDPOINT=$(get s3_endpoint)
STALWART_S3_REGION=$(get s3_region)
STALWART_S3_BUCKET=$(get s3_bucket)
STALWART_S3_ACCESS_KEY=$(get s3_access_key)
STALWART_S3_SECRET_KEY=$(get s3_secret_key)
STALWART_ADMIN_SECRET=$(secret)
STALWART_MASTER_USER=gaer-master
STALWART_MASTER_SECRET=$(secret)
LETTERMINT_SMTP_HOST=smtp.lettermint.co
LETTERMINT_SMTP_USERNAME=
LETTERMINT_SMTP_PASSWORD=
ENV

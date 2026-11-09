# Mail server — phase 1 (Stalwart + Lettermint on UpCloud)

Phase 1 of `specs/mail`: Stalwart on UpCloud with Managed PostgreSQL and Managed Object Storage, one domain, all
outbound mail through Lettermint, mail expiring after 30 days, a personal calendar and address book per user over
CalDAV/CardDAV, and a nightly calendar/contacts export. The app (phases 2–3) then manages it from `/user/mail` and
`/admin/mail`.

| File | What |
| --- | --- |
| `upcloud/` | Terraform: private network, Stalwart server + firewall, PostgreSQL, Object Storage bucket and user |
| `config.toml` | Stalwart: listeners, ACME, PostgreSQL + S3 stores, fallback admin, master user, Lettermint relay |
| `compose.yml`, `stalwart.env.example` | Stalwart in Docker on the server, its secrets |
| `render-env.sh` | `stalwart.env` from the Terraform outputs, with generated admin and master secrets |
| `deploy.sh` | on the server: `up` (pull, start, wait for ready), `status`, `logs` |
| `bootstrap.sh` | on the server, once: the mail domain and the `gaer-api` principal the app logs in with |

Before go-live, settle the open questions in the spec (region, Lettermint plan, single node), sign data processing
agreements with UpCloud and Lettermint, and check `config.toml` against the docs of the Stalwart version you pin.

## 1. Infrastructure (UpCloud)

```bash
cd deploy/mail/upcloud
export UPCLOUD_TOKEN=…            # API token of an UpCloud sub-account with server, database and storage rights
cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars
terraform init && terraform apply
(terraform output -json) > ../outputs.json
```

Then, in the UpCloud panel:
- attach the **app server** to the new private network (output `network_id`) and note its private IP;
- set **reverse DNS** of the Stalwart server's public IP (output `server_public_ip`) to `MAIL_HOSTNAME`.

## 2. DNS

`A` (and `AAAA`) for `MAIL_HOSTNAME` → `server_public_ip`. The rest (MX, SPF, DKIM, DMARC, autoconfig and the SRV
records for IMAP, submission, JMAP, CalDAV and CardDAV) are listed per domain in `/admin/mail → Domæner` with a live
check, once step 5 is done.

## 3. Lettermint

Add the domain in Lettermint, publish its DKIM and return-path records, and create SMTP credentials for the
project. Create an incoming webhook in the app (`/admin/webhooks → Indgående`, handler `lettermint`, HMAC
verification) and give its URL to Lettermint for delivery, bounce and complaint events.

## 4. Stalwart

```bash
cd deploy/mail
./render-env.sh outputs.json > stalwart.env
$EDITOR stalwart.env          # STALWART_IMAGE (pin an exact version), ACME_CONTACT, LETTERMINT_SMTP_*
ssh root@MAIL_HOSTNAME mkdir -p /opt/gaer-mail
scp compose.yml config.toml deploy.sh bootstrap.sh stalwart.env root@MAIL_HOSTNAME:/opt/gaer-mail/
ssh root@MAIL_HOSTNAME /opt/gaer-mail/deploy.sh up
ssh root@MAIL_HOSTNAME /opt/gaer-mail/bootstrap.sh kunde.dk     # prints STALWART_API_KEY once
```

The first start fetches the Let's Encrypt certificate over port 443 (TLS-ALPN-01), so DNS from step 2 must be live.

## 5. The app

In `~/gaer/.env` on the app server: `MAIL_HOSTNAME` and `MAIL_PRIVATE_IP` (the Stalwart server's private IP), so the
app reaches Stalwart's management listener over the private network with a valid certificate. In `~/gaer/app.env`:

```
MAILBOX_BACKEND=stalwart
STALWART_URL=https://mail.kunde.dk:8443
STALWART_API_KEY=gaer-api:…                 # from bootstrap.sh
STALWART_MASTER_USER=gaer-master            # same as stalwart.env
STALWART_MASTER_SECRET=…                    # same as stalwart.env
MAIL_HOSTNAME=mail.kunde.dk
```

Redeploy the app, then in `/admin/mail`: add the domain, create the DNS records it lists and press "Tjek igen";
create mailboxes (one by one or from the user list). Each gets a default calendar and address book.

## Running it

- **Retention and backup** run in the app as the jobs `mail.retention` (mail older than the policy, at most 30
  days, deleted through Stalwart), `mail.backup` (every calendar as .ics and address book as .vcf to
  `MAIL_BACKUP_PREFIX`) and `mail.sync` (drift between the app and Stalwart), nightly; see `/admin/jobs`.
- **Database backups** stay short (UpCloud's default retention), so a restore cannot bring back expired mail.
  Never add a lifecycle rule to the Stalwart bucket: it holds calendars and contacts too.
- **Outbound queue**: `/admin/mail → Drift` (pause, resume, flush) or `./deploy.sh status` on the server.
- **Updates**: change `STALWART_IMAGE` in `stalwart.env`, read Stalwart's upgrade notes, `./deploy.sh up`.
- **Relay credentials**: rotate them in Lettermint, then `/admin/mail → Afsendelse`.

## Verify against the pinned Stalwart version

`config.toml` follows Stalwart 0.13. Check in its docs: `%{env:…}%` macros, `[http] allowed-endpoint`,
`[authentication.master]`, `[queue.route.*]` and `[queue.strategy]`, `[store."s3"] purge.frequency`,
`[auth.dkim] sign`, and the principal type `domain` used by `bootstrap.sh`. The app's calls are listed in
`specs/mail/README.md`.

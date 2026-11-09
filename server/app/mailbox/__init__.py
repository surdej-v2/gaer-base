"""Mail, calendar and contacts (specs/mail): personal and shared mailboxes in Stalwart, all outbound mail through
Lettermint, mail kept at most 30 days.

    backend.py   the mail server (Stalwart, or in memory for development and tests)
    service.py   rules: provisioning, roles, identities, calendars and address books, app passwords, retention
    dns.py       the DNS records a domain needs, and the live check of them
    clients.py   client settings and the Apple configuration profile
    tasks.py     background jobs (provision, retention, sync, backup, archive export, delete) and the Lettermint
                 handler for incoming webhooks
"""

"""The DNS records a mail domain needs, and a live check of them (/admin/mail → Domæner).

MX points at Stalwart (MAIL_HOSTNAME); SPF and DKIM are Lettermint's, since all outbound mail leaves through it;
DMARC is the domain's own policy; the SRV and CNAME records let mail, calendar and contacts clients find the server.
Tests replace `resolve` with a fake.
"""

import asyncio

import dns.asyncresolver
import dns.exception
import dns.resolver

from app import config

TIMEOUT = 4.0


async def resolve(name: str, rdtype: str) -> list[str]:
    """The record values as text, [] when there are none."""
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = TIMEOUT
    try:
        answer = await resolver.resolve(name, rdtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException as e:
        raise LookupError(type(e).__name__) from e
    out = []
    for r in answer:
        if rdtype == "TXT":
            out.append(b"".join(r.strings).decode(errors="replace"))
        else:
            out.append(r.to_text())
    return out


def required_records(domain: str, dkim_selector: str) -> list[dict]:
    host = config.settings.mail_hostname or f"mail.{domain}"
    spf = config.settings.mail_spf_include
    return [
        {"key": "mx", "label": "MX", "type": "MX", "name": domain, "value": f"10 {host}.",
         "why": "Indgående mail leveres til Stalwart"},
        {"key": "spf", "label": "SPF", "type": "TXT", "name": domain, "value": f"v=spf1 include:{spf} -all",
         "why": "Kun Lettermint må sende mail fra domænet"},
        {"key": "dkim", "label": "DKIM", "type": "CNAME/TXT", "name": f"{dkim_selector}._domainkey.{domain}",
         "value": "Værdien fra domænets side i Lettermint", "why": "Lettermint signerer udgående mail"},
        {"key": "dmarc", "label": "DMARC", "type": "TXT", "name": f"_dmarc.{domain}",
         "value": f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{domain}", "why": "Modtagere afviser forfalsket mail"},
        {"key": "autoconfig", "label": "Autoconfig", "type": "CNAME", "name": f"autoconfig.{domain}",
         "value": f"{host}.", "why": "Thunderbird og andre finder selv indstillingerne"},
        {"key": "imaps", "label": "IMAP (SRV)", "type": "SRV", "name": f"_imaps._tcp.{domain}",
         "value": f"0 1 993 {host}.", "why": "Mailklienter finder IMAP"},
        {"key": "submission", "label": "SMTP (SRV)", "type": "SRV", "name": f"_submissions._tcp.{domain}",
         "value": f"0 1 465 {host}.", "why": "Mailklienter finder afsendelse"},
        {"key": "jmap", "label": "JMAP (SRV)", "type": "SRV", "name": f"_jmap._tcp.{domain}",
         "value": f"0 1 443 {host}.", "why": "JMAP-klienter finder serveren"},
        {"key": "caldav", "label": "CalDAV (SRV)", "type": "SRV", "name": f"_caldavs._tcp.{domain}",
         "value": f"0 1 443 {host}.", "why": "Kalenderklienter finder serveren"},
        {"key": "carddav", "label": "CardDAV (SRV)", "type": "SRV", "name": f"_carddavs._tcp.{domain}",
         "value": f"0 1 443 {host}.", "why": "Kontaktklienter finder serveren"},
    ]


def _host(value: str) -> str:
    return value.rstrip(".").split()[-1].lower()


async def _check(rec: dict, host: str, spf: str) -> dict:
    name = rec["name"]
    try:
        if rec["key"] == "mx":
            found = await resolve(name, "MX")
            ok = any(_host(v) == host for v in found)
        elif rec["key"] == "spf":
            found = [v for v in await resolve(name, "TXT") if v.lower().startswith("v=spf1")]
            ok = len(found) == 1 and f"include:{spf}" in found[0]
        elif rec["key"] == "dkim":
            found = await resolve(name, "CNAME") or [v for v in await resolve(name, "TXT") if "p=" in v]
            ok = bool(found)
        elif rec["key"] == "dmarc":
            found = [v for v in await resolve(name, "TXT") if v.upper().startswith("V=DMARC1")]
            ok = len(found) == 1 and "p=none" not in found[0].replace(" ", "").lower()
        elif rec["key"] == "autoconfig":
            found = await resolve(name, "CNAME") or await resolve(name, "A")
            ok = bool(found)
        else:
            found = await resolve(name, "SRV")
            ok = any(_host(v) == host for v in found)
    except LookupError as e:
        return {"ok": False, "found": [], "error": f"DNS-opslag fejlede ({e})"}
    return {"ok": ok, "found": found[:5]}


async def check_domain(domain: str, dkim_selector: str) -> dict:
    """{key: {ok, found}} for every required record, plus `verified`: MX, SPF, DKIM and DMARC are in place."""
    host = (config.settings.mail_hostname or f"mail.{domain}").lower()
    recs = required_records(domain, dkim_selector)
    results = await asyncio.gather(*(_check(r, host, config.settings.mail_spf_include) for r in recs))
    out = {r["key"]: res for r, res in zip(recs, results)}
    out["verified"] = all(out[k]["ok"] for k in ("mx", "spf", "dkim", "dmarc"))
    return out

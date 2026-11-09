"""Client settings for /user/mail → Opsætning af klienter, and the Apple configuration profile (mail, calendar and
contacts in one). The profile never contains a password: the device asks for an app password when it is installed."""

import plistlib
import uuid

from app import config


def host() -> str | None:
    return config.settings.mail_hostname


def settings(address: str) -> dict:
    h = host()
    if not h:
        return {"configured": False}
    return {
        "configured": True,
        "username": address,
        "imap": {"host": h, "port": 993, "security": "SSL/TLS"},
        "smtp": {"host": h, "port": 465, "security": "SSL/TLS", "alt_port": 587, "alt_security": "STARTTLS"},
        "jmap": {"url": f"https://{h}/.well-known/jmap"},
        "caldav": {"url": f"https://{h}/dav/cal/{address}/"},
        "carddav": {"url": f"https://{h}/dav/card/{address}/"},
        "autoconfig": f"https://autoconfig.{address.split('@', 1)[1]}/mail/config-v1.1.xml?emailaddress={address}",
    }


def mobileconfig(address: str, display_name: str, app_name: str) -> bytes:
    h = host()
    ns = uuid.uuid5(uuid.NAMESPACE_DNS, f"{address}.{h}")

    def payload(kind: str, ident: str, extra: dict) -> dict:
        return {"PayloadType": kind, "PayloadVersion": 1, "PayloadIdentifier": f"{h}.{ident}",
                "PayloadUUID": str(uuid.uuid5(ns, ident)).upper(), "PayloadDisplayName": f"{app_name} {ident}", **extra}

    content = [
        payload("com.apple.mail.managed", "mail", {
            "EmailAccountDescription": f"{app_name} mail", "EmailAccountName": display_name or address,
            "EmailAccountType": "EmailTypeIMAP", "EmailAddress": address,
            "IncomingMailServerAuthentication": "EmailAuthPassword", "IncomingMailServerHostName": h,
            "IncomingMailServerPortNumber": 993, "IncomingMailServerUseSSL": True, "IncomingMailServerUsername": address,
            "OutgoingMailServerAuthentication": "EmailAuthPassword", "OutgoingMailServerHostName": h,
            "OutgoingMailServerPortNumber": 465, "OutgoingMailServerUseSSL": True, "OutgoingMailServerUsername": address,
            "OutgoingPasswordSameAsIncomingPassword": True}),
        payload("com.apple.caldav.account", "calendar", {
            "CalDAVAccountDescription": f"{app_name} kalender", "CalDAVHostName": h, "CalDAVPort": 443,
            "CalDAVUseSSL": True, "CalDAVUsername": address, "CalDAVPrincipalURL": f"/dav/cal/{address}/"}),
        payload("com.apple.carddav.account", "contacts", {
            "CardDAVAccountDescription": f"{app_name} kontakter", "CardDAVHostName": h, "CardDAVPort": 443,
            "CardDAVUseSSL": True, "CardDAVUsername": address, "CardDAVPrincipalURL": f"/dav/card/{address}/"}),
    ]
    profile = {"PayloadType": "Configuration", "PayloadVersion": 1, "PayloadIdentifier": f"{h}.profile.{address}",
               "PayloadUUID": str(uuid.uuid5(ns, "profile")).upper(), "PayloadDisplayName": f"{app_name}: {address}",
               "PayloadDescription": "Mail, kalender og kontakter. Brug en app-adgangskode, når enheden spørger.",
               "PayloadContent": content}
    return plistlib.dumps(profile)

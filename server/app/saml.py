"""SAML 2.0 for login providers (app/login_providers.py): Gær is the service provider (SP).

- Login starts with an AuthnRequest over HTTP-Redirect (RelayState = the flow); the IdP answers with a Response over
  HTTP-POST to /api/auth/saml/<key>/acs.
- **The assertion must be signed** by a certificate from the IdP's metadata (always on, signxml). Only what the
  signature covers is read, so a response with extra or wrapped assertions gets nowhere. Optionally the assertion
  must also be encrypted to the SP certificate (RSA-OAEP + AES-GCM/CBC).
- Checked: status, destination and recipient (our ACS), audience (our entity ID), the time window (±2 minutes),
  InResponseTo against the flow, and that the assertion id has not been used before (login_assertions_seen).
  IdP-initiated logins (no InResponseTo) only when the admin allowed them.
- XML is parsed without entities, DTD loading or network access.
- The SP key pair is generated per connection; the private key is encrypted with app.secrets_box.
"""

import base64
import hashlib
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from lxml import etree

from app import login_providers as lp
from app import secrets_box, webhooks

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xenc": "http://www.w3.org/2001/04/xmlenc#",
    "xenc11": "http://www.w3.org/2009/xmlenc11#",
}
REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NAMEID = {"persistent": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
          "email": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"}
SKEW = timedelta(minutes=2)
MAX_RESPONSE = 512 * 1024


class SamlError(lp.Refused):
    def __init__(self, detail: str):
        super().__init__("failed", detail)


def parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_pis=True)


def parse_xml(data: bytes) -> etree._Element:
    if b"<!DOCTYPE" in data[:2048].upper() or b"<!ENTITY" in data.upper():
        raise SamlError("doctype")
    try:
        return etree.fromstring(data, parser())
    except etree.XMLSyntaxError as e:
        raise SamlError(f"xml: {str(e)[:80]}")


def text(el) -> str:
    """All text of an element without comments (a comment inside a NameID must not cut it short)."""
    return "".join(el.itertext()).strip() if el is not None else ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _instant(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if "." in v:  # at most microseconds
        head, _, rest = v.partition(".")
        digits = "".join(c for c in rest if c.isdigit())
        v = f"{head}.{digits[:6]}{rest[len(digits):]}"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise SamlError("time format")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── The SP's key pair and metadata ──────────────────────────────────────────

def generate_sp_keypair(common_name: str) -> tuple[str, str]:
    """A new RSA key and a self-signed certificate (10 years) for one connection: (private key PEM, certificate PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64])])
    now = _now()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650)).sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


def encrypt_sp_key(provider_id: int, key_pem: str) -> bytes:
    return secrets_box.encrypt(key_pem, f"login-provider-sp:{provider_id}")


def sp_key(provider_id: int, blob) -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(secrets_box.decrypt(blob, f"login-provider-sp:{provider_id}").encode(),
                                              password=None)


def _cert_b64(pem: str) -> str:
    return base64.b64encode(x509.load_pem_x509_certificate(pem.encode()).public_bytes(serialization.Encoding.DER)).decode()


def sp_metadata(provider: dict) -> str:
    u = lp.urls(provider)
    cert = _cert_b64(provider["sp_cert"])
    key_info = (f'<ds:KeyInfo xmlns:ds="{NS["ds"]}"><ds:X509Data><ds:X509Certificate>{cert}'
                f'</ds:X509Certificate></ds:X509Data></ds:KeyInfo>')
    fmt = NAMEID[provider["config"].get("nameid_format", "persistent")]
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID={quoteattr(u["entity_id"])}>'
            f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
            f'protocolSupportEnumeration="{NS["samlp"]}">'
            f'<md:KeyDescriptor use="signing">{key_info}</md:KeyDescriptor>'
            f'<md:KeyDescriptor use="encryption">{key_info}'
            f'<md:EncryptionMethod Algorithm="http://www.w3.org/2009/xmlenc11#aes256-gcm"/>'
            f'<md:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>'
            f'<md:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#rsa-oaep-mgf1p"/></md:KeyDescriptor>'
            f'<md:SingleLogoutService Binding="{REDIRECT}" Location={quoteattr(u["slo"])}/>'
            f'<md:NameIDFormat>{fmt}</md:NameIDFormat>'
            f'<md:AssertionConsumerService Binding="{POST}" Location={quoteattr(u["acs"])} index="0" isDefault="true"/>'
            f'</md:SPSSODescriptor></md:EntityDescriptor>\n')


def authn_request_url(provider: dict, *, request_id: str, relay_state: str) -> str:
    meta = provider.get("idp_meta") or {}
    u = lp.urls(provider)
    fmt = NAMEID[provider["config"].get("nameid_format", "persistent")]
    xml = (f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="{request_id}" Version="2.0" '
           f'IssueInstant="{_instant(_now())}" Destination={quoteattr(meta["sso_url"])} '
           f'AssertionConsumerServiceURL={quoteattr(u["acs"])} ProtocolBinding="{POST}">'
           f'<saml:Issuer>{escape(u["entity_id"])}</saml:Issuer>'
           f'<samlp:NameIDPolicy Format="{fmt}" AllowCreate="true"/></samlp:AuthnRequest>')
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = base64.b64encode(deflater.compress(xml.encode()) + deflater.flush()).decode()
    sep = "&" if "?" in meta["sso_url"] else "?"
    return meta["sso_url"] + sep + urlencode({"SAMLRequest": payload, "RelayState": relay_state})


# ── The IdP's metadata ──────────────────────────────────────────────────────

def cert_info(b64_or_pem: str) -> dict:
    raw = b64_or_pem.strip()
    try:
        if "BEGIN CERTIFICATE" in raw:
            cert = x509.load_pem_x509_certificate(raw.encode())
        else:
            cert = x509.load_der_x509_certificate(base64.b64decode("".join(raw.split())))
    except (ValueError, TypeError):
        raise SamlError("certificate")
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return {"pem": pem, "not_after": cert.not_valid_after_utc.isoformat(),
            "sha256": ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))}


def parse_metadata(data: bytes) -> dict:
    """entity_id, sso_url (HTTP-Redirect), slo_url and the signing certificates of the first IdP in the document."""
    root = parse_xml(data)
    entities = [root] if root.tag == f"{{{NS['md']}}}EntityDescriptor" else root.findall(".//md:EntityDescriptor", NS)
    for ent in entities:
        idp = ent.find("md:IDPSSODescriptor", NS)
        if idp is None:
            continue
        sso = next((s.get("Location") for s in idp.findall("md:SingleSignOnService", NS) if s.get("Binding") == REDIRECT), None)
        slo = next((s.get("Location") for s in idp.findall("md:SingleLogoutService", NS) if s.get("Binding") == REDIRECT), None)
        certs = []
        for kd in idp.findall("md:KeyDescriptor", NS):
            if kd.get("use") not in (None, "signing"):
                continue
            for c in kd.findall(".//ds:X509Certificate", NS):
                info = cert_info(text(c))
                if info["sha256"] not in {x["sha256"] for x in certs}:
                    certs.append(info)
        if not sso:
            raise lp.Refused("failed", "IdP'en har ingen SSO-adresse med HTTP-Redirect")
        return {"entity_id": ent.get("entityID"), "sso_url": sso, "slo_url": slo, "certs": certs}
    raise lp.Refused("failed", "Dokumentet beskriver ingen IdP (IDPSSODescriptor)")


async def fetch_metadata(url: str) -> bytes:
    """The IdP's metadata from its URL (https to a public address only, no redirects, at most 1 MB)."""
    try:
        webhooks.check_url(url, resolve=True)
    except webhooks.UnsafeUrl as e:
        raise lp.Refused("failed", str(e))
    async with lp.http_client() as client:
        r = await client.get(url, follow_redirects=False)
    if r.status_code != 200:
        raise lp.Refused("failed", f"HTTP {r.status_code}")
    if len(r.content) > 1_000_000:
        raise lp.Refused("failed", "too large")
    return r.content


def manual_metadata(entity_id: str, sso_url: str, cert_pem: str) -> dict:
    return {"entity_id": entity_id.strip(), "sso_url": sso_url.strip(), "slo_url": None,
            "certs": [cert_info(cert_pem)] if cert_pem.strip() else []}


# ── The response ────────────────────────────────────────────────────────────

@dataclass
class Result:
    identity: "lp.Identity"
    assertion_id: str
    expires_at: datetime
    in_response_to: str | None


def _rsa_decrypt(key: rsa.RSAPrivateKey, ek) -> bytes:
    method = ek.find("xenc:EncryptionMethod", NS)
    alg = method.get("Algorithm") if method is not None else ""
    digest = method.find("ds:DigestMethod", NS) if method is not None else None
    hash_alg = {"http://www.w3.org/2001/04/xmlenc#sha256": hashes.SHA256(),
                "http://www.w3.org/2001/04/xmlenc#sha512": hashes.SHA512()}.get(
        digest.get("Algorithm") if digest is not None else "", hashes.SHA1())
    if alg == "http://www.w3.org/2001/04/xmlenc#rsa-oaep-mgf1p":
        mgf = hashes.SHA1()
    elif alg == "http://www.w3.org/2009/xmlenc11#rsa-oaep":
        m = method.find("xenc11:MGF", NS)
        mgf = {"http://www.w3.org/2009/xmlenc11#mgf1sha256": hashes.SHA256(),
               "http://www.w3.org/2009/xmlenc11#mgf1sha512": hashes.SHA512()}.get(
            m.get("Algorithm") if m is not None else "", hashes.SHA1())
    else:
        raise SamlError(f"key transport {alg or 'missing'}")  # RSA 1.5 is refused
    value = base64.b64decode(text(ek.find("xenc:CipherData/xenc:CipherValue", NS)))
    try:
        return key.decrypt(value, padding.OAEP(mgf=padding.MGF1(mgf), algorithm=hash_alg, label=None))
    except ValueError:
        raise SamlError("key decrypt")


def decrypt_assertion(encrypted, key: rsa.RSAPrivateKey):
    data = encrypted.find("xenc:EncryptedData", NS)
    if data is None:
        raise SamlError("no EncryptedData")
    ek = data.find("ds:KeyInfo/xenc:EncryptedKey", NS)
    if ek is None:
        ek = encrypted.find("xenc:EncryptedKey", NS)
    if ek is None:
        raise SamlError("no EncryptedKey")
    cek = _rsa_decrypt(key, ek)
    alg = data.find("xenc:EncryptionMethod", NS).get("Algorithm", "")
    blob = base64.b64decode(text(data.find("xenc:CipherData/xenc:CipherValue", NS)))
    try:
        if alg.endswith(("#aes128-gcm", "#aes192-gcm", "#aes256-gcm")):
            plain = AESGCM(cek).decrypt(blob[:12], blob[12:], None)
        elif alg.endswith(("#aes128-cbc", "#aes192-cbc", "#aes256-cbc")):
            dec = Cipher(algorithms.AES(cek), modes.CBC(blob[:16])).decryptor()
            padded = dec.update(blob[16:]) + dec.finalize()
            n = padded[-1]
            if not 1 <= n <= 16:
                raise SamlError("padding")
            plain = padded[:-n]
        else:
            raise SamlError(f"cipher {alg}")
    except SamlError:
        raise
    except Exception:
        raise SamlError("decrypt")
    root = parse_xml(plain)
    if root.tag != f"{{{NS['saml']}}}Assertion":
        raise SamlError("decrypted element is not an Assertion")
    return root


def verify_signature(assertion, certs: list[dict]):
    """The assertion as its signature covers it. Tries each of the IdP's certificates (rollover)."""
    from signxml import InvalidInput, InvalidSignature, XMLVerifier
    data = etree.tostring(assertion)
    last = "no certificate"
    for c in certs:
        try:
            result = XMLVerifier().verify(data, x509_cert=c["pem"], parser=parser())
        except (InvalidSignature, InvalidInput) as e:
            last = str(e)[:80]
            continue
        signed = result.signed_xml
        if signed is None or signed.tag != f"{{{NS['saml']}}}Assertion" or signed.get("ID") != assertion.get("ID"):
            raise SamlError("signature does not cover the assertion")
        return signed
    raise SamlError(f"signature: {last}")


def process_response(provider: dict, saml_response: str, key: rsa.RSAPrivateKey | None,
                     expected_request_id: str | None) -> Result:
    cfg = provider["config"]
    meta = provider.get("idp_meta") or {}
    u = lp.urls(provider)
    if not saml_response or len(saml_response) > MAX_RESPONSE * 2:
        raise SamlError("size")
    try:
        raw = base64.b64decode(saml_response, validate=False)
    except ValueError:
        raise SamlError("base64")
    root = parse_xml(raw)
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise SamlError("not a Response")
    if root.get("Destination") and root.get("Destination") != u["acs"]:
        raise SamlError("destination")
    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != SUCCESS:
        sub = status.find("samlp:StatusCode", NS) if status is not None else None
        denied = sub is not None and sub.get("Value", "").endswith(("AuthnFailed", "RequestDenied"))
        raise lp.Refused("denied" if denied else "failed",
                         f"status {status.get('Value') if status is not None else 'missing'}")
    in_response_to = root.get("InResponseTo")
    if expected_request_id is not None and in_response_to != expected_request_id:
        raise SamlError("InResponseTo")
    if expected_request_id is None and in_response_to:
        raise SamlError("unsolicited response with InResponseTo")
    plain = root.findall("saml:Assertion", NS)
    encrypted = root.findall("saml:EncryptedAssertion", NS)
    if len(plain) + len(encrypted) != 1:
        raise SamlError("exactly one assertion")
    if encrypted:
        if key is None:
            raise SamlError("no SP key")
        assertion = decrypt_assertion(encrypted[0], key)
    else:
        if cfg.get("want_encrypted"):
            raise SamlError("assertion not encrypted")
        assertion = plain[0]
    assertion = verify_signature(assertion, meta.get("certs") or [])

    now = _now()
    if text(assertion.find("saml:Issuer", NS)) != meta.get("entity_id"):
        raise SamlError("issuer")
    subject = assertion.find("saml:Subject", NS)
    name_id = subject.find("saml:NameID", NS) if subject is not None else None
    if name_id is None or not text(name_id):
        raise SamlError("no NameID")
    ok_confirmation = False
    for sc in subject.findall("saml:SubjectConfirmation", NS):
        if sc.get("Method") != BEARER:
            continue
        d = sc.find("saml:SubjectConfirmationData", NS)
        if d is None or d.get("Recipient") != u["acs"]:
            continue
        until = _parse_time(d.get("NotOnOrAfter"))
        if until is None or until + SKEW <= now:
            continue
        irt = d.get("InResponseTo")
        if irt != expected_request_id:  # None for an IdP-initiated login: then it must not have one either
            continue
        ok_confirmation = True
    if not ok_confirmation:
        raise SamlError("subject confirmation")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SamlError("no Conditions")
    not_before, not_after = _parse_time(conditions.get("NotBefore")), _parse_time(conditions.get("NotOnOrAfter"))
    if (not_before and not_before - SKEW > now) or (not_after and not_after + SKEW <= now):
        raise SamlError("outside the time window")
    audiences = [text(a) for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)]
    if u["entity_id"] not in audiences:
        raise SamlError("audience")

    attrs: dict[str, list[str]] = {}
    for a in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        attrs.setdefault(a.get("Name", ""), []).extend(text(v) for v in a.findall("saml:AttributeValue", NS))
    email = None
    if cfg.get("nameid_format") == "email":
        email = text(name_id)
    if cfg.get("attr_email") and attrs.get(cfg["attr_email"]):
        email = attrs[cfg["attr_email"]][0]
    email = email.strip().lower() if email and "@" in email else None
    name = (attrs.get(cfg.get("attr_name") or "") or [None])[0]
    groups = attrs.get(cfg.get("attr_groups") or "") or []
    # The IdP is the admin's own: its e-mail counts as verified on the domains the admin listed (like Entra).
    identity = lp.Identity(subject=text(name_id), email=email,
                           email_verified=bool(email) and lp.domain_matches(email, cfg.get("domains") or []),
                           name=name, groups=groups, claims=sorted(k for k in attrs if k) + ["NameID"])
    aid = assertion.get("ID") or ""
    if not aid:
        raise SamlError("no assertion ID")
    return Result(identity=identity, assertion_id=aid,
                  expires_at=max(not_after or now, now) + SKEW, in_response_to=in_response_to)


async def remember_assertion(conn, provider_id: int, result: Result) -> None:
    """Refuses an assertion id that was already used (replay)."""
    cur = await conn.execute("""insert into login_assertions_seen (provider_id, assertion_id, expires_at)
                                values (%s, %s, %s) on conflict do nothing""",
                             (provider_id, result.assertion_id, result.expires_at))
    if not cur.rowcount:
        raise SamlError("replayed assertion")

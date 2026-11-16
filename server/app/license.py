"""The license key: a JWT issued by happymates.ai, checked here on the server.

Happy Mates Services ApS issues one license per customer instance at happymates.ai. The key is a
JWT signed with Ed25519 (alg EdDSA). It names the license id, the licensee, the domains the instance may run
on (claim `domains`, an array; `*.kunde.dk` covers subdomains) and how long it runs, and it may carry the exact license statement to publish. The instance gets it in the environment variable
LICENSE_KEY. The server verifies it with happymates.ai's public keys, which live in this file (ISSUER_KEYS)
and come with updates from gaer-base; they are not configuration, so a fork cannot swap in its own key.

Every HTML page under /w carries the statement in its <head> (LicenseHeadMiddleware in main.py): a comment,
<meta name="license"> and <link rel="license">. Without a valid key the statement says so. Nothing else stops
working, on purpose: no hard enforcement. The check is about attribution and audit, the LICENSE file is what
binds. Any error in this module fails open: the page is served as rendered.

happymates.ai issues and checks keys with the same module (on its own machine, private key never in a repo):

    python -m app.license keygen                      # new key pair: kid + public key for ISSUER_KEYS, private PEM
    python -m app.license issue --key private.pem --kid hm-2026-1 --id HM-2026-0001 \\
        --name "Kunde ApS" --cvr 12345678 --domain app.kunde.dk --domain www.kunde.dk --days 365
    python -m app.license verify <jwt> [--host app.kunde.dk]
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from html import escape
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app import config

ISSUER = "https://happymates.ai"
PRODUCT = "gaer"
OWNER = "Happy Mates Services ApS"
VERIFY_URL = "https://happymates.ai/licenses/"  # + license id: the public page for a license
LEEWAY_SECONDS = 300
CHECK_EVERY_SECONDS = 600
MAX_TOKEN_BYTES = 8192

# happymates.ai's signing keys: kid → raw Ed25519 public key, base64url. Add a key from `keygen` here before
# issuing with it, keep the old one while its licenses run, then remove it (rotation).
ISSUER_KEYS: dict[str, str] = {
    "hm-2026-1": "C1bOIRir-HUS0839kVlgYj5YjR1YuTkMPEAscvgFngc",
}


@dataclass(frozen=True)
class License:
    status: str  # valid | missing | invalid | expired | not_yet_valid | wrong_host
    reason: str = ""
    license_id: str | None = None
    licensee: str | None = None
    cvr: str | None = None
    expires_at: int | None = None
    domains: tuple[str, ...] = ()
    statement: str | None = None
    claims: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def valid(self) -> bool:
        return self.status == "valid"

    def public(self) -> dict:
        """What /health shows: the same facts every /w page already carries."""
        return {"status": self.status, "id": self.license_id, "expires_at": self.expires_at,
                "domains": list(self.domains)}


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _host_matches(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower().strip(), host.lower()
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host != pattern[2:]
    return pattern == host


def instance_host() -> str:
    return (urlparse(config.settings.origin).hostname or "").lower()


def verify(token: str | None, *, host: str | None = None, now: float | None = None,
           keys: dict[str, str] | None = None) -> License:
    """Check a license key. Never raises: the result says what is wrong."""
    token = (token or "").strip()
    if not token:
        return License("missing", "LICENSE_KEY er ikke sat")
    if len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
        return License("invalid", "Licensnøglen er ikke en JWT")
    keys = ISSUER_KEYS if keys is None else keys
    head_b64, body_b64, sig_b64 = token.split(".")
    try:
        header = json.loads(_b64decode(head_b64))
        claims = json.loads(_b64decode(body_b64))
        signature = _b64decode(sig_b64)
    except (ValueError, UnicodeDecodeError):
        return License("invalid", "Licensnøglen kan ikke læses")
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return License("invalid", "Licensnøglen kan ikke læses")
    # Only EdDSA: no "none", no HMAC with the public key as secret (algorithm confusion).
    if header.get("alg") != "EdDSA":
        return License("invalid", "Forkert signaturalgoritme")
    raw_key = keys.get(str(header.get("kid", "")))
    if not raw_key:
        return License("invalid", "Ukendt nøgle-id (kid) — ikke udstedt af happymates.ai")
    try:
        Ed25519PublicKey.from_public_bytes(_b64decode(raw_key)).verify(signature, f"{head_b64}.{body_b64}".encode())
    except (InvalidSignature, ValueError):
        return License("invalid", "Signaturen er ugyldig")

    lic = claims.get("lic") if isinstance(claims.get("lic"), dict) else {}
    domains = claims.get("domains")
    domains = tuple(d.lower().strip() for d in domains if isinstance(d, str) and d.strip()) \
        if isinstance(domains, list) else ()
    found = dict(license_id=str(claims.get("jti") or "") or None, licensee=lic.get("name"), cvr=lic.get("cvr"),
                 expires_at=claims.get("exp") if isinstance(claims.get("exp"), int) else None, domains=domains,
                 statement=claims.get("stmt") if isinstance(claims.get("stmt"), str) else None, claims=claims)
    if claims.get("iss") != ISSUER:
        return License("invalid", "Forkert udsteder", **found)
    if claims.get("prd") != PRODUCT:
        return License("invalid", "Licensen gælder et andet produkt", **found)
    if not found["license_id"]:
        return License("invalid", "Licens-id (jti) mangler", **found)
    if found["expires_at"] is None:
        return License("invalid", "Udløbstid (exp) mangler", **found)
    if not domains:
        return License("invalid", "Listen over domæner (domains) mangler", **found)
    now = time.time() if now is None else now
    if now > found["expires_at"] + LEEWAY_SECONDS:
        return License("expired", "Licensen er udløbet", **found)
    nbf = claims.get("nbf", claims.get("iat"))
    if isinstance(nbf, int) and now < nbf - LEEWAY_SECONDS:
        return License("not_yet_valid", "Licensen gælder ikke endnu", **found)
    return for_host(License("valid", **found), instance_host() if host is None else host)


def for_host(lic: License, host: str) -> License:
    """The license as seen from one domain: a valid key used on a domain it does not list is wrong_host."""
    host = host.lower().split(":")[0]
    if lic.valid and not any(_host_matches(d, host) for d in lic.domains):
        return replace(lic, status="wrong_host", reason=f"Licensen gælder ikke domænet {host}")
    return lic


_cache: tuple[float, str, License] | None = None


def current() -> License:
    """The instance's license, checked again every CHECK_EVERY_SECONDS (so expiry shows without a restart)."""
    global _cache
    token = config.settings.license_key or ""
    now = time.time()
    if _cache is None or _cache[1] != token or now - _cache[0] > CHECK_EVERY_SECONDS:
        try:
            result = verify(token, now=now)
        except Exception:  # a bug in the check must never break the site; report it as invalid
            logging.getLogger("gaer").exception("license: the check failed")
            result = License("invalid", "Licenstjekket fejlede")
        _cache = (now, token, result)
    return _cache[2]


def statement(lic: License) -> str:
    if lic.valid:
        if lic.statement:
            return lic.statement
        to = f"{lic.licensee} (CVR {lic.cvr})" if lic.cvr else (lic.licensee or "")
        return f"Gær © {OWNER}. Licens {lic.license_id} udstedt af happymates.ai til {to}. Alle rettigheder forbeholdes."
    return (f"Gær © {OWNER}. Alle rettigheder forbeholdes. UGYLDIG LICENS ({lic.status}): denne installation har ingen "
            "gyldig licens fra happymates.ai.")


def head_html(lic: License) -> str:
    """The block injected right after <head> on every /w page."""
    text = statement(lic).replace("--", "‐‐")  # an HTML comment must not contain "--"
    lid = lic.license_id if lic.valid else "UNLICENSED"
    lines = [f"<!-- {escape(text, quote=False)} Licens-id: {escape(lid, quote=False)}. Status: {lic.status}. -->",
             f'<meta name="license" content="{escape(lid)}">',
             f'<meta name="license-status" content="{escape(lic.status)}">',
             f'<meta name="copyright" content="{escape(statement(lic))}">']
    if lic.valid:
        lines.append(f'<link rel="license" href="{escape(VERIFY_URL + lic.license_id)}">')
    return "\n".join(lines) + "\n"


def header_value(lic: License) -> str:
    """The Gaer-License response header on /w pages."""
    return f"{lic.license_id if lic.valid else 'UNLICENSED'}; status={lic.status}"


def inject(html: bytes, lic: License) -> bytes:
    """Insert head_html right after the opening <head> tag (or at the top when a page has none)."""
    block = head_html(lic).encode()
    lower = html[:4096].lower()
    at = lower.find(b"<head")
    if at >= 0 and (end := lower.find(b">", at)) >= 0:
        return html[:end + 1] + b"\n" + block + html[end + 1:]
    return block + html


class LicenseHeadMiddleware:
    """ASGI middleware, the in-process stand-in for nginx `sub_filter '<head>' …`: every text/html response
    under /w gets the license statement in its <head>, whatever renders the page."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == "/w" or path.startswith("/w/")) or path.startswith("/w/static/"):
            return await self.app(scope, receive, send)
        start: dict | None = None
        chunks: list[bytes] = []

        async def capture(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body" or start is None:
                return await send(message)
            headers = [(k, v) for k, v in start["headers"]]
            ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
            if not ctype.startswith(b"text/html") or any(k.lower() == b"content-encoding" for k, _ in headers):
                await send(start)
                start = {"type": "_sent"}
                return await send(message)
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            original = b"".join(chunks)
            try:
                lic = current()
                host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
                if host and lic.status in ("valid", "wrong_host"):
                    lic = for_host(replace(lic, status="valid", reason=""), host) if lic.domains else lic
                body = inject(original, lic)
                extra = [(b"gaer-license", header_value(lic).encode())]
            except Exception:  # never let the license statement take a page down: serve it as rendered
                logging.getLogger("gaer").exception("license: could not add the statement to %s", path)
                body, extra = original, []
            headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
            headers += [(b"content-length", str(len(body)).encode()), *extra]
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        async def passthrough(message):
            if start is not None and start.get("type") == "_sent":
                return await send(message)
            return await capture(message)

        await self.app(scope, receive, passthrough)


def _issue(args) -> str:
    key = serialization.load_pem_private_key(open(args.key, "rb").read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("Nøglen skal være Ed25519")
    now = int(time.time())
    claims = {"iss": ISSUER, "prd": PRODUCT, "jti": args.id or f"HM-{uuid.uuid4().hex[:12].upper()}",
              "sub": args.cvr or args.name, "domains": args.domain, "iat": now, "nbf": now,
              "exp": now + args.days * 86400, "lic": {"name": args.name, "cvr": args.cvr}}
    if args.statement:
        claims["stmt"] = args.statement
    header = {"alg": "EdDSA", "typ": "JWT", "kid": args.kid}
    signing_input = f"{_b64encode(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64encode(json.dumps(claims, separators=(',', ':'), ensure_ascii=False).encode())}"
    return f"{signing_input}.{_b64encode(key.sign(signing_input.encode()))}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.license", description="Gær license keys (happymates.ai)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen", help="new Ed25519 key pair")
    kg.add_argument("--kid", default=f"hm-{time.strftime('%Y')}-{uuid.uuid4().hex[:6]}")
    kg.add_argument("--out", default="happymates-license-private.pem")
    iss = sub.add_parser("issue", help="sign a license key")
    iss.add_argument("--key", required=True, help="private key PEM from keygen")
    iss.add_argument("--kid", required=True)
    iss.add_argument("--id", help="license id (jti), e.g. HM-2026-0001")
    iss.add_argument("--name", required=True, help="licensee")
    iss.add_argument("--cvr")
    iss.add_argument("--domain", action="append", required=True,
                     help="a domain the instance may run on (repeat for each; *.kunde.dk covers subdomains)")
    iss.add_argument("--days", type=int, default=365)
    iss.add_argument("--statement", help="exact statement to publish (default: built from the claims)")
    ver = sub.add_parser("verify", help="check a license key against ISSUER_KEYS")
    ver.add_argument("token")
    ver.add_argument("--host", help="default: the host of ORIGIN")
    args = parser.parse_args(argv)

    if args.cmd == "keygen":
        private = Ed25519PrivateKey.generate()
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        with open(args.out, "xb") as f:
            f.write(pem)
        raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(f"Private key written to {args.out} — keep it at happymates.ai, never in a repository.")
        print(f'Add to ISSUER_KEYS in server/app/license.py:\n    "{args.kid}": "{_b64encode(raw)}",')
        return 0
    if args.cmd == "issue":
        print(_issue(args))
        return 0
    lic = verify(args.token, host=args.host)
    print(json.dumps({"status": lic.status, "reason": lic.reason, "id": lic.license_id, "licensee": lic.licensee,
                      "cvr": lic.cvr, "domains": list(lic.domains), "expires_at": lic.expires_at, "statement": statement(lic)},
                     ensure_ascii=False, indent=2))
    return 0 if lic.valid else 1


if __name__ == "__main__":
    sys.exit(main())

"""Login-udbydere (/admin/login-providers, app/login_providers.py, app/saml.py): a provider replaces only the one-time
code. Flows are bound to the browser and single use; the second-factor rules after the code still apply; nobody becomes
admin through a provider; accounts are linked on a verified e-mail or by the user; domains can require a provider."""

import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from lxml import etree

from app import gdpr, jobs
from app import login_providers as lp
from tests.helpers import full_user, otp_login, register_passkey, SoftAuthenticator, stepup

TID = "11111111-2222-3333-4444-555555555555"
ENTRA = f"https://login.microsoftonline.com/{TID}/v2.0"
ORIGIN = "http://localhost:5173"


# ── A fake OpenID Connect provider ──────────────────────────────────────────

class FakeIdP:
    """Microsoft Entra ID or Google behind httpx.MockTransport: discovery, JWKS and a token endpoint that signs an
    id_token for whatever the test put in `claims` (with the nonce from the authorize request)."""

    def __init__(self, issuer: str = ENTRA, client_id: str = "client-123"):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.issuer, self.client_id = issuer, client_id
        self.claims: dict = {}
        self.nonce = self.challenge = None
        self.token_calls: list[dict] = []
        self.issuer_override: str | None = None

    def jwk(self) -> dict:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            base = url.rsplit("/.well-known", 1)[0]
            return httpx.Response(200, json={"issuer": self.issuer, "authorization_endpoint": f"{base}/authorize",
                                             "token_endpoint": f"{base}/token", "jwks_uri": f"{base}/keys"})
        if url.endswith("/keys"):
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if url.endswith("/token"):
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            verifier = form["code_verifier"][0]
            ok = (base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == self.challenge
                  and form["client_secret"][0] == "s3cret" and form["code"][0] == "the-code")
            if not ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {"iss": self.issuer_override or self.issuer, "aud": self.client_id, "iat": now, "exp": now + 600,
                      "nonce": self.nonce, "sub": "pairwise-sub", **self.claims}
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": token, "access_token": "at"})
        return httpx.Response(404)


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    lp._cache.clear()
    monkeypatch.setattr(lp, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


async def _admin(make_client):
    """The super admin with a passkey and a fresh second factor (a step-up)."""
    boss = make_client()
    await full_user(boss, "11111111")
    return boss


async def _entra(boss, *, domains=("firma.dk",), new_users="existing", enable=True, **extra):
    r = await boss.post("/api/admin/login-providers", json={"kind": "entra"})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "entra"
    r = await boss.put("/api/admin/login-providers/entra", json={
        "name": "Microsoft Entra ID", "client_secret": "s3cret",
        "config": {"client_id": "client-123", "tenant_mode": "single", "tenant_id": TID, "domains": list(domains),
                   "new_users": new_users, **extra}})
    assert r.status_code == 200, r.text
    if enable:
        r = await boss.post("/api/admin/login-providers/entra/enabled", json={"enabled": True})
        assert r.status_code == 200, r.text


def _user(owner_conn, email: str, **cols) -> int:
    names = ", ".join(["email", *cols])
    marks = ", ".join(["%s"] * (1 + len(cols)))
    return owner_conn.execute(f"insert into users ({names}) values ({marks}) returning id",
                              (email, *cols.values())).fetchone()[0]


async def _start(client, idp: FakeIdP, key: str = "entra", next_path: str = "/klip") -> str:
    r = await client.get(f"/api/auth/providers/{key}/start", params={"next": next_path})
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    idp.nonce, idp.challenge = q["nonce"][0], q["code_challenge"][0]
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    return q["state"][0]


def _auth_events(owner_conn, event: str) -> list[tuple]:
    return owner_conn.execute("select result, detail->>'reason', detail->>'provider' from auth_log where event = %s order by id",
                              (event,)).fetchall()


# ── OpenID Connect ──────────────────────────────────────────────────────────

async def test_entra_login_links_on_verified_email_and_then_works_like_a_code(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    uid = _user(owner_conn, "a.hansen@firma.dk")
    idp.claims = {"tid": TID, "oid": "oid-1", "email": "A.Hansen@firma.dk", "name": "A. Hansen"}

    browser = make_client()
    shown = (await browser.get("/api/auth/providers", params={"email": "a.hansen@firma.dk"})).json()
    assert [p["key"] for p in shown["providers"]] == ["entra"] and shown["providers"][0]["label"] == "Fortsæt med Microsoft"
    state = await _start(browser, idp)
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert r.status_code == 303, r.text
    # No passkey yet: /login offers one, then continues to ?next.
    target = urlparse(r.headers["location"])
    assert target.path == "/login" and parse_qs(target.query) == {"sso": ["offer"], "next": ["/klip"]}
    me = (await browser.get("/api/auth/me")).json()
    assert me["user"]["id"] == uid and me["scope"] == "full" and me["user"]["role"] == "user"
    row = owner_conn.execute("select subject, email, linked_via from user_identities where user_id = %s", (uid,)).fetchone()
    assert row == (f"{TID}:oid-1", "a.hansen@firma.dk", "verified_email")
    assert _auth_events(owner_conn, "provider.login") == [("ok", None, "entra")]
    # The same identity again: found by its subject, not the e-mail.
    owner_conn.execute("update users set email = 'ny@firma.dk' where id = %s", (uid,))
    again = make_client()
    state = await _start(again, idp)
    r = await again.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert (await again.get("/api/auth/me")).json()["user"]["id"] == uid


async def test_flow_is_bound_to_the_browser_and_single_use(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    _user(owner_conn, "a.hansen@firma.dk")
    idp.claims = {"tid": TID, "oid": "oid-1", "email": "a.hansen@firma.dk"}
    victim = make_client()
    state = await _start(victim, idp)
    attacker = make_client()
    r = await attacker.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert r.status_code == 303 and "sso_error=expired" in r.headers["location"]
    assert (await attacker.get("/api/auth/me")).json()["user"] is None
    # The flow was used up by the failed attempt: the right browser cannot use it either.
    r = await victim.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=expired" in r.headers["location"]
    assert not idp.token_calls


async def test_wrong_nonce_or_tenant_is_refused(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    _user(owner_conn, "a.hansen@firma.dk")
    idp.claims = {"tid": TID, "oid": "oid-1", "email": "a.hansen@firma.dk"}
    browser = make_client()
    state = await _start(browser, idp)
    idp.nonce = "something-else"
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=failed" in r.headers["location"]
    state = await _start(browser, idp)
    idp.issuer_override = "https://login.microsoftonline.com/99999999-2222-3333-4444-555555555555/v2.0"
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=failed" in r.headers["location"]
    assert (await browser.get("/api/auth/me")).json()["user"] is None
    assert [e[1] for e in _auth_events(owner_conn, "provider.login")] == ["failed", "failed"]


async def test_second_factor_rules_still_apply(make_client, owner_conn, idp):
    """A provider is one factor: "Kræv passkey" gives a pending session, and /admin still needs a second factor."""
    boss = await _admin(make_client)
    await _entra(boss)
    # The super admin gets an e-mail on the provider's domain and logs in through Entra.
    boss_id = (await boss.get("/api/auth/me")).json()["user"]["id"]
    owner_conn.execute("update users set email = 'chef@firma.dk' where id = %s", (boss_id,))
    idp.claims = {"tid": TID, "oid": "oid-chef", "email": "chef@firma.dk"}
    b2 = make_client()
    state = await _start(b2, idp, next_path="/admin")
    r = await b2.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert r.headers["location"] == "/admin"   # has a passkey: nothing to offer
    me = (await b2.get("/api/auth/me")).json()
    assert me["user"]["role"] == "admin" and me["user"]["mfa"]["admin_ok"] is False
    r = await b2.get("/api/admin/login-providers")
    assert r.status_code == 403 and r.headers["x-gaer-error"] == "mfa_required"

    # "Kræv passkey": only a pending session until the second factor.
    uid = _user(owner_conn, "sikker@firma.dk", mfa_required=True)
    idp.claims = {"tid": TID, "oid": "oid-2", "email": "sikker@firma.dk"}
    b3 = make_client()
    state = await _start(b3, idp)
    r = await b3.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert parse_qs(urlparse(r.headers["location"]).query)["sso"] == ["mfa"]
    me = (await b3.get("/api/auth/me")).json()
    assert me["scope"] == "mfa" and me["user"]["id"] == uid
    assert (await b3.get("/api/me/security")).status_code == 403


async def test_who_may_get_in(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    browser = make_client()
    # Unknown person and new users not allowed.
    idp.claims = {"tid": TID, "oid": "oid-new", "email": "ny@firma.dk"}
    state = await _start(browser, idp)
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=no_account" in r.headers["location"]
    # E-mail outside the allowed domains.
    idp.claims = {"tid": TID, "oid": "oid-x", "email": "x@andet.dk"}
    state = await _start(browser, idp)
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=domain" in r.headers["location"]
    # Blocked.
    _user(owner_conn, "spaerret@firma.dk", blocked_at=datetime.now(timezone.utc))
    idp.claims = {"tid": TID, "oid": "oid-b", "email": "spaerret@firma.dk"}
    state = await _start(browser, idp)
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=blocked" in r.headers["location"]
    assert (await browser.get("/api/auth/me")).json()["user"] is None
    # With "Opret en konto": a new user, always role user.
    r = await boss.get("/api/admin/login-providers/entra")
    cfg = r.json()["config"] | {"new_users": "signup"}
    assert (await boss.put("/api/admin/login-providers/entra", json={"name": "Microsoft Entra ID", "config": cfg})).status_code == 200
    idp.claims = {"tid": TID, "oid": "oid-new", "email": "ny@firma.dk", "name": "Ny Person"}
    state = await _start(browser, idp)
    r = await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    me = (await browser.get("/api/auth/me")).json()["user"]
    assert me["email"] == "ny@firma.dk" and me["role"] == "user" and me["display_name"] == "Ny Person"
    # The rule "Aldrig – kun eksisterende brugere" wins over the provider.
    assert (await boss.put("/api/admin/login-providers/rules", json={"link": "verified_email", "new_users": "never"})).status_code == 200
    idp.claims = {"tid": TID, "oid": "oid-new2", "email": "ny2@firma.dk"}
    b2 = make_client()
    state = await _start(b2, idp)
    r = await b2.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=no_account" in r.headers["location"]


async def test_only_user_linked_rule_and_linking_from_konto(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    assert (await boss.put("/api/admin/login-providers/rules",
                           json={"link": "user_linked", "new_users": "signup"})).status_code == 200
    user = make_client()
    await otp_login(user, "22222222")
    uid = (await user.get("/api/auth/me")).json()["user"]["id"]
    owner_conn.execute("update users set email = 'b@firma.dk' where id = %s", (uid,))
    idp.claims = {"tid": TID, "oid": "oid-b", "email": "b@firma.dk"}
    # Not linked yet, and linking on e-mail is off.
    other = make_client()
    state = await _start(other, idp)
    r = await other.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert "sso_error=no_account" in r.headers["location"]
    # The user links it from /konto (no second factor yet: the code login is all there is).
    listed = (await user.get("/api/me/identities")).json()["providers"]
    assert listed[0]["key"] == "entra" and listed[0]["available"] and listed[0]["linked"] is None
    r = await user.post("/api/me/identities/entra/link")
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    idp.nonce, idp.challenge = q["nonce"][0], q["code_challenge"][0]
    r = await user.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": q["state"][0]})
    assert r.headers["location"] == "/konto?linked=entra#forbundne-logins"
    assert (await user.get("/api/me/identities")).json()["providers"][0]["linked"]["via"] == "user"
    # Now the provider logs them in.
    state = await _start(other, idp)
    await other.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert (await other.get("/api/auth/me")).json()["user"]["id"] == uid
    # With a passkey, removing the link needs a fresh second factor.
    auth = SoftAuthenticator()
    await register_passkey(user, auth)
    owner_conn.execute("update sessions set mfa_at = now() - interval '1 hour' where user_id = %s", (uid,))
    r = await user.delete("/api/me/identities/entra")
    assert r.status_code == 403 and r.headers["x-gaer-error"] == "stepup_required"
    await stepup(user, auth)
    assert (await user.delete("/api/me/identities/entra")).status_code == 200
    assert owner_conn.execute("select count(*) from user_identities").fetchone()[0] == 0
    export = await _export(uid)
    assert export["login_identities"] == []


async def _export(uid: int) -> dict:
    from app import db
    async with db.connection() as conn:
        return await gdpr.build_user_export(conn, uid)


async def test_required_domain_gets_no_code_except_super_admin(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    _user(owner_conn, "a@firma.dk")
    r = await boss.put("/api/admin/login-providers/rules",
                       json={"link": "verified_email", "new_users": "signup", "required": [{"domain": "firma.dk", "provider": "entra"}]})
    assert r.status_code == 200, r.text
    browser = make_client()
    shown = (await browser.get("/api/auth/providers", params={"email": "a@firma.dk"})).json()
    assert shown["required"]["key"] == "entra"
    r = await browser.post("/api/auth/otp/start", json={"email": "a@firma.dk"})
    assert r.status_code == 200 and "dev_code" not in r.json()
    assert ("fail", "provider_required", "entra") in _auth_events(owner_conn, "otp.sent")
    # The super admin keeps the code as emergency access.
    boss_id = (await boss.get("/api/auth/me")).json()["user"]["id"]
    owner_conn.execute("update users set email = 'chef@firma.dk' where id = %s", (boss_id,))
    r = await browser.post("/api/auth/otp/start", json={"email": "chef@firma.dk"})
    assert "dev_code" in r.json()
    # A provider a domain requires cannot be deleted.
    assert (await boss.post("/api/admin/login-providers/entra/enabled", json={"enabled": False})).status_code == 200
    assert (await boss.delete("/api/admin/login-providers/entra")).status_code == 409


async def test_admin_api_is_guarded_and_never_returns_the_secret(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss, enable=False)
    detail = (await boss.get("/api/admin/login-providers/entra")).json()
    assert detail["secret_set"] is True and "s3cret" not in str(detail)
    assert detail["urls"]["redirect_uri"] == f"{ORIGIN}/api/auth/oidc/entra/callback"
    assert detail["status"] == "ready"
    overview = (await boss.get("/api/admin/login-providers")).json()
    assert {c["kind"]: c["set_up"] for c in overview["catalog"]} == {
        "entra": True, "google": False, "github": False, "amazon": False, "saml": False}
    audit = owner_conn.execute("select action, after::text, detail::text from audit_log where table_name = 'login_providers'"
                               " order by id").fetchall()
    assert [a[0] for a in audit] == ["login_provider.created", "login_provider.updated"]
    assert all("s3cret" not in (a[1] or "") + (a[2] or "") for a in audit)
    assert '"client_secret_changed": true' in audit[1][2]
    # Only one Entra; enabling needs everything filled in; changes need a step-up.
    assert (await boss.post("/api/admin/login-providers", json={"kind": "entra"})).status_code == 409
    owner_conn.execute("update sessions set mfa_at = now() - interval '1 hour'")
    r = await boss.post("/api/admin/login-providers/entra/enabled", json={"enabled": True})
    assert r.status_code == 403 and r.headers["x-gaer-error"] == "stepup_required"
    # An ordinary user gets nothing; an admin with only the Support role neither.
    user = make_client()
    await otp_login(user, "33333333")
    assert (await user.get("/api/admin/login-providers")).status_code == 403
    from app import admin_roles
    assert admin_roles.allows(["drift"], "GET", "/api/admin/login-providers/entra")
    assert not admin_roles.allows(["support"], "GET", "/api/admin/login-providers")


async def test_test_login_logs_nobody_in(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss, enable=False)
    r = await boss.post("/api/admin/login-providers/entra/test")
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    idp.nonce, idp.challenge = q["nonce"][0], q["code_challenge"][0]
    idp.claims = {"tid": TID, "oid": "oid-t", "email": "test@firma.dk"}
    before = (await boss.get("/api/auth/me")).json()["user"]["id"]
    r = await boss.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": q["state"][0]})
    assert r.headers["location"] == "/admin/login-providers/entra?test=ok"
    test = (await boss.get("/api/admin/login-providers/entra")).json()["last_test"]
    assert test["ok"] and "email" in test["claims"] and test["email_verified"] is True
    assert (await boss.get("/api/auth/me")).json()["user"]["id"] == before
    assert owner_conn.execute("select count(*) from user_identities").fetchone()[0] == 0


async def test_google_needs_a_verified_email(make_client, owner_conn, monkeypatch):
    fake = FakeIdP(issuer="https://accounts.google.com", client_id="g-client")
    lp._cache.clear()
    monkeypatch.setattr(lp, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    boss = await _admin(make_client)
    assert (await boss.post("/api/admin/login-providers", json={"kind": "google"})).status_code == 200
    r = await boss.put("/api/admin/login-providers/google", json={
        "name": "Google", "client_secret": "s3cret",
        "config": {"client_id": "g-client", "domains": [], "hosted_domain": "firma.dk", "new_users": "existing"}})
    assert r.status_code == 200, r.text
    assert (await boss.post("/api/admin/login-providers/google/enabled", json={"enabled": True})).status_code == 200
    uid = _user(owner_conn, "g@firma.dk")
    browser = make_client()
    fake.claims = {"sub": "g-1", "email": "g@firma.dk", "email_verified": False, "hd": "firma.dk"}
    state = await _start(browser, fake, key="google")
    r = await browser.get("/api/auth/oidc/google/callback", params={"code": "the-code", "state": state})
    assert "sso_error=unverified" in r.headers["location"]
    fake.claims = {"sub": "g-1", "email": "g@firma.dk", "email_verified": True, "hd": "andet.dk"}
    state = await _start(browser, fake, key="google")
    r = await browser.get("/api/auth/oidc/google/callback", params={"code": "the-code", "state": state})
    assert "sso_error=domain" in r.headers["location"]
    fake.claims = {"sub": "g-1", "email": "g@firma.dk", "email_verified": True, "hd": "firma.dk"}
    state = await _start(browser, fake, key="google")
    await browser.get("/api/auth/oidc/google/callback", params={"code": "the-code", "state": state})
    assert (await browser.get("/api/auth/me")).json()["user"]["id"] == uid


async def test_group_mapping_adds_and_removes_only_its_own(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    gid = (await boss.post("/api/admin/groups", json={"name": "Salg"})).json()["id"]
    await _entra(boss, group_map=[{"claim": "sg-salg", "group_id": gid}])
    uid = _user(owner_conn, "s@firma.dk")
    idp.claims = {"tid": TID, "oid": "oid-s", "email": "s@firma.dk", "groups": ["sg-salg"]}
    browser = make_client()
    state = await _start(browser, idp)
    await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert owner_conn.execute("select via_provider_id is not null from group_members where user_id = %s", (uid,)).fetchone() == (True,)
    idp.claims = {**idp.claims, "groups": []}
    state = await _start(browser, idp)
    await browser.get("/api/auth/oidc/entra/callback", params={"code": "the-code", "state": state})
    assert owner_conn.execute("select count(*) from group_members where user_id = %s", (uid,)).fetchone()[0] == 0
    actions = [r[0] for r in owner_conn.execute(
        "select action from audit_log where table_name = 'groups' and detail->>'via' = 'provider:entra' order by id")]
    assert actions == ["group.member_added", "group.member_removed"]


# ── SAML 2.0 ────────────────────────────────────────────────────────────────

SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
IDP_ENTITY = "http://adfs.regionnord.dk/adfs/services/trust"
IDP_SSO = "https://adfs.regionnord.dk/adfs/ls/"


def _cert(key, cn: str) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=20)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class FakeSamlIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.cert = _cert(self.key, "ADFS Signing")

    def metadata(self) -> str:
        der = base64.b64encode(x509.load_pem_x509_certificate(self.cert.encode()).public_bytes(serialization.Encoding.DER)).decode()
        return (f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{IDP_ENTITY}">'
                f'<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
                f'<md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:X509Data>'
                f'<ds:X509Certificate>{der}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>'
                f'<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{IDP_SSO}"/>'
                f'</md:IDPSSODescriptor></md:EntityDescriptor>')

    def response(self, *, key: str, request_id: str | None, email: str, name_id: str = "nameid-1",
                 audience: str | None = None, sign: bool = True, encrypt_to: str | None = None,
                 assertion_id: str | None = None) -> str:
        from signxml import XMLSigner
        acs = f"{ORIGIN}/api/auth/saml/{key}/acs"
        now = datetime.now(timezone.utc)
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        irt = f' InResponseTo="{request_id}"' if request_id else ""
        aid = assertion_id or "_a" + uuid.uuid4().hex
        assertion = (
            f'<saml:Assertion xmlns:saml="{SAML_NS}" ID="{aid}" Version="2.0" IssueInstant="{fmt(now)}">'
            f'<saml:Issuer>{IDP_ENTITY}</saml:Issuer>'
            f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">{name_id}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData{irt} NotOnOrAfter="{fmt(now + timedelta(minutes=5))}" Recipient="{acs}"/>'
            f'</saml:SubjectConfirmation></saml:Subject>'
            f'<saml:Conditions NotBefore="{fmt(now - timedelta(minutes=1))}" NotOnOrAfter="{fmt(now + timedelta(minutes=5))}">'
            f'<saml:AudienceRestriction><saml:Audience>{audience or f"{ORIGIN}/api/auth/saml/{key}"}</saml:Audience>'
            f'</saml:AudienceRestriction></saml:Conditions>'
            f'<saml:AttributeStatement><saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress">'
            f'<saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>'
            f'</saml:Assertion>')
        el = etree.fromstring(assertion.encode())
        if sign:
            el = XMLSigner(c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(
                el, key=self.key, cert=self.cert, reference_uri=aid)
            # The signature goes after the Issuer, as SAML wants it.
            sig = el.find("{http://www.w3.org/2000/09/xmldsig#}Signature")
            el.remove(sig)
            el.insert(1, sig)
        body = etree.tostring(el).decode()
        if encrypt_to:
            body = _encrypt(body, encrypt_to)
        response = (f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="_r{uuid.uuid4().hex}" Version="2.0"'
                    f' IssueInstant="{fmt(now)}" Destination="{acs}"{irt}>'
                    f'<saml:Issuer xmlns:saml="{SAML_NS}">{IDP_ENTITY}</saml:Issuer>'
                    f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
                    f'{body}</samlp:Response>')
        return base64.b64encode(response.encode()).decode()


def _encrypt(assertion_xml: str, sp_cert_pem: str) -> str:
    cek = AESGCM.generate_key(bit_length=256)
    iv = b"\x01" * 12
    blob = iv + AESGCM(cek).encrypt(iv, assertion_xml.encode(), None)
    pub = x509.load_pem_x509_certificate(sp_cert_pem.encode()).public_key()
    ek = pub.encrypt(cek, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return (f'<saml:EncryptedAssertion xmlns:saml="{SAML_NS}"><xenc:EncryptedData xmlns:xenc="http://www.w3.org/2001/04/xmlenc#"'
            f' Type="http://www.w3.org/2001/04/xmlenc#Element"><xenc:EncryptionMethod Algorithm="http://www.w3.org/2009/xmlenc11#aes256-gcm"/>'
            f'<ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><xenc:EncryptedKey>'
            f'<xenc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#rsa-oaep-mgf1p"/>'
            f'<xenc:CipherData><xenc:CipherValue>{b64(ek)}</xenc:CipherValue></xenc:CipherData></xenc:EncryptedKey></ds:KeyInfo>'
            f'<xenc:CipherData><xenc:CipherValue>{b64(blob)}</xenc:CipherValue></xenc:CipherData></xenc:EncryptedData>'
            f'</saml:EncryptedAssertion>')


async def _saml(boss, idp: FakeSamlIdP, **cfg) -> str:
    r = await boss.post("/api/admin/login-providers", json={"kind": "saml", "name": "Region Nord"})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key == "region-nord"
    r = await boss.put(f"/api/admin/login-providers/{key}", json={"name": "Region Nord", "config": {
        "metadata_source": "xml", "domains": ["regionnord.dk"], "only_matching_domain": True, "new_users": "existing", **cfg}})
    assert r.status_code == 200, r.text
    r = await boss.post(f"/api/admin/login-providers/{key}/metadata", json={"xml": idp.metadata()})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["idp"]["entity_id"] == IDP_ENTITY and d["cert_days"] in (19, 20) and d["status"] == "ready"
    assert (await boss.post(f"/api/admin/login-providers/{key}/enabled", json={"enabled": True})).status_code == 200
    return key


async def _saml_start(browser, key: str) -> tuple[str, str]:
    r = await browser.get(f"/api/auth/providers/{key}/start")
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == IDP_SSO
    q = parse_qs(loc.query)
    import zlib
    request = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15).decode()
    request_id = etree.fromstring(request.encode()).get("ID")
    return request_id, q["RelayState"][0]


async def test_saml_login(make_client, owner_conn):
    idp = FakeSamlIdP()
    boss = await _admin(make_client)
    key = await _saml(boss, idp)
    uid = _user(owner_conn, "a@regionnord.dk")
    browser = make_client()
    # The button only shows for the connection's domain.
    assert (await browser.get("/api/auth/providers")).json() == {"providers": [], "required": None, "sso": True}
    assert [p["key"] for p in (await browser.get("/api/auth/providers", params={"email": "a@regionnord.dk"})).json()["providers"]] == [key]
    metadata = (await browser.get(f"/api/auth/saml/{key}/metadata")).text
    assert f'entityID="{ORIGIN}/api/auth/saml/{key}"' in metadata and "BEGIN" not in metadata

    request_id, relay = await _saml_start(browser, key)
    good = idp.response(key=key, request_id=request_id, email="a@regionnord.dk", assertion_id="_fixed")
    # Without the X-Gaer header: the IdP posts a plain form.
    raw = make_client()
    raw.headers.pop("x-gaer")
    raw.cookies = browser.cookies
    r = await raw.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": good, "RelayState": relay})
    assert r.status_code == 303, r.text
    assert parse_qs(urlparse(r.headers["location"]).query)["sso"] == ["offer"]
    assert (await raw.get("/api/auth/me")).json()["user"]["id"] == uid

    # Replaying the same response (new flow, same assertion id) is refused.
    other = make_client()
    request_id, relay = await _saml_start(other, key)
    replay = idp.response(key=key, request_id=request_id, email="a@regionnord.dk", assertion_id="_fixed")
    r = await other.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": replay, "RelayState": relay})
    assert "sso_error=failed" in r.headers["location"]
    assert _auth_events(owner_conn, "provider.login")[-1][:2] == ("fail", "failed")


@pytest.mark.parametrize("case", ["unsigned", "audience", "in_response_to", "other_key", "tampered"])
async def test_saml_refuses_bad_responses(make_client, owner_conn, case):
    idp = FakeSamlIdP()
    boss = await _admin(make_client)
    key = await _saml(boss, idp)
    _user(owner_conn, "a@regionnord.dk")
    browser = make_client()
    request_id, relay = await _saml_start(browser, key)
    if case == "unsigned":
        body = idp.response(key=key, request_id=request_id, email="a@regionnord.dk", sign=False)
    elif case == "audience":
        body = idp.response(key=key, request_id=request_id, email="a@regionnord.dk", audience="https://andet.example")
    elif case == "in_response_to":
        body = idp.response(key=key, request_id="_somebody-else", email="a@regionnord.dk")
    elif case == "other_key":
        body = FakeSamlIdP().response(key=key, request_id=request_id, email="a@regionnord.dk")
    else:
        signed = base64.b64decode(idp.response(key=key, request_id=request_id, email="b@regionnord.dk")).decode()
        body = base64.b64encode(signed.replace("b@regionnord.dk", "a@regionnord.dk").encode()).decode()
    r = await browser.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": body, "RelayState": relay})
    assert r.status_code == 303 and "sso_error=failed" in r.headers["location"], r.headers.get("location")
    assert (await browser.get("/api/auth/me")).json()["user"] is None


async def test_saml_encrypted_assertion_and_unsolicited(make_client, owner_conn):
    idp = FakeSamlIdP()
    boss = await _admin(make_client)
    key = await _saml(boss, idp, want_encrypted=True)
    uid = _user(owner_conn, "a@regionnord.dk")
    sp_cert = (await make_client().get(f"/api/auth/saml/{key}/cert")).text
    browser = make_client()
    request_id, relay = await _saml_start(browser, key)
    plain = idp.response(key=key, request_id=request_id, email="a@regionnord.dk")
    r = await browser.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": plain, "RelayState": relay})
    assert "sso_error=failed" in r.headers["location"]   # must be encrypted
    request_id, relay = await _saml_start(browser, key)
    enc = idp.response(key=key, request_id=request_id, email="a@regionnord.dk", encrypt_to=sp_cert)
    r = await browser.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": enc, "RelayState": relay})
    assert "sso_error" not in r.headers["location"], r.headers["location"]
    assert (await browser.get("/api/auth/me")).json()["user"]["id"] == uid
    # IdP-initiated (no request): refused unless allowed.
    b2 = make_client()
    unsolicited = idp.response(key=key, request_id=None, email="a@regionnord.dk", encrypt_to=sp_cert)
    r = await b2.post(f"/api/auth/saml/{key}/acs", data={"SAMLResponse": unsolicited})
    assert "sso_error=expired" in r.headers["location"]


async def test_cert_expiry_job_warns(make_client, owner_conn):
    idp = FakeSamlIdP()
    boss = await _admin(make_client)
    key = await _saml(boss, idp)
    from app import db
    async with db.connection() as conn:
        await jobs.enqueue(conn, "auth.saml_metadata", {})
    await jobs.run_pending()
    rows = owner_conn.execute("select action, detail->>'days' from audit_log where action = 'login_provider.cert_expiring'").fetchall()
    assert len(rows) == 1 and int(rows[0][1]) <= 30
    assert (await boss.get("/api/admin/login-providers")).json()["providers"][0]["cert_days"] <= 30
    assert key


async def test_code_sent_before_the_domain_rule_creates_no_account(make_client, owner_conn, idp):
    boss = await _admin(make_client)
    await _entra(boss)
    browser = make_client()
    r = await browser.post("/api/auth/otp/start", json={"email": "ny@firma.dk"})
    code = r.json()["dev_code"]
    assert (await boss.put("/api/admin/login-providers/rules", json={
        "link": "verified_email", "new_users": "signup", "required": [{"domain": "firma.dk", "provider": "entra"}]})).status_code == 200
    r = await browser.post("/api/auth/otp/verify", json={"email": "ny@firma.dk", "code": code})
    assert r.status_code == 403
    assert owner_conn.execute("select count(*) from users where email = 'ny@firma.dk'").fetchone()[0] == 0

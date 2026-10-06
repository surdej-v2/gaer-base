import dataclasses
import io
import xml.etree.ElementTree as ET
import zipfile

from app import config
from tests.helpers import full_user, otp_login


async def test_clips_crud_is_audited(client, owner_conn):
    await otp_login(client, "12345678")
    r = await client.post("/api/clips", json={"title": "Tilbud", "body": "Pris 1.500 kr.", "source": "extension",
                                              "url": "https://example.dk"})
    clip_id = r.json()["id"]
    assert (await client.get("/api/clips?q=1.500")).json()[0]["title"] == "Tilbud"
    assert (await client.patch(f"/api/clips/{clip_id}", json={"title": "Tilbud v2"})).status_code == 200
    assert (await client.delete(f"/api/clips/{clip_id}")).status_code == 200
    actions = owner_conn.execute("select action from audit_log where table_name = 'clips' order by id").fetchall()
    assert [a[0] for a in actions] == ["create", "update", "delete"]


async def test_clips_are_private(make_client):
    a, b = make_client(), make_client()
    await otp_login(a, "11111111")
    await otp_login(b, "22222222")
    clip_id = (await a.post("/api/clips", json={"title": "Mit", "body": "x"})).json()["id"]
    assert (await b.get("/api/clips")).json() == []
    assert (await b.delete(f"/api/clips/{clip_id}")).status_code == 404


async def test_bridge_consent_per_domain(client):
    await otp_login(client, "12345678")
    assert (await client.get("/api/bridge-consent/check?domain=https://www.dr.dk/nyheder")).json() == {"domain": "dr.dk", "allowed": False}
    await client.post("/api/bridge-consent", json={"domain": "https://www.dr.dk/nyheder"})
    assert (await client.get("/api/bridge-consent/check?domain=dr.dk")).json()["allowed"] is True
    consent_id = (await client.get("/api/bridge-consent")).json()[0]["id"]
    await client.delete(f"/api/bridge-consent/{consent_id}")
    assert (await client.get("/api/bridge-consent/check?domain=dr.dk")).json()["allowed"] is False


async def test_office_manifests_are_valid_xml(client):
    listing = (await client.get("/api/office/manifests")).json()
    assert [m["app"] for m in listing] == ["word", "excel", "powerpoint", "outlook"]
    ids = set()
    for m in listing:
        r = await client.get(m["url"])
        assert r.status_code == 200
        root = ET.fromstring(r.content)
        ns = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1"}
        ids.add(root.find("o:Id", ns).text)
        assert "http://localhost:5173/office-addin?app=" + m["app"] in r.text.replace("&amp;", "&")
    assert len(ids) == 4


async def test_website_languages_and_sanitizing(client, owner_conn):
    r = await client.get("/w/")
    assert r.status_code == 200 and "<h1>Velkommen</h1>" in r.text and 'lang="da"' in r.text
    r = await client.get("/w/?lang=en")
    assert "<h1>Welcome</h1>" in r.text and r.cookies.get("w_lang") == "en"
    assert "<h1>Welcome</h1>" in (await client.get("/w/")).text  # remembered
    assert (await client.get("/w/findes-ikke")).status_code == 404

    owner_conn.execute("""insert into web_pages (slug, lang, title, body, published)
                          values ('xss', 'en', 'X', '<script>alert(1)</script>[x](javascript:alert(1))', true)""")
    body = (await client.get("/w/xss?lang=en")).text
    assert "<script>alert" not in body and "javascript:" not in body


async def test_anonymous_root_goes_to_website(client):
    r = await client.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/w/"
    await otp_login(client, "12345678")
    assert (await client.get("/")).status_code != 302


async def test_admin_edits_website(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    r = await admin.post("/api/admin/web", json={"slug": "priser", "lang": "da", "title": "Priser",
                                                 "body": "# Priser\n\n1.500 kr.", "published": True, "nav_order": 2})
    page_id = r.json()["id"]
    assert (await admin.post("/api/admin/web", json={"slug": "priser", "lang": "da", "title": "Igen"})).status_code == 409
    assert "<h1>Priser</h1>" in (await admin.get("/w/priser")).text
    assert ">Priser</a>" in (await admin.get("/w/")).text  # in the menu
    await admin.put(f"/api/admin/web/{page_id}", json={"slug": "priser", "lang": "da", "title": "Priser",
                                                       "body": "x", "published": False})
    assert (await admin.get("/w/priser")).status_code == 404
    assert (await admin.post("/api/admin/web/preview", json={"body": "**fed**"})).json()["html"] == "<p><strong>fed</strong></p>"


async def test_extension_download_has_endpoint(client):
    r = await client.get("/api/extension/download")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert '"http://localhost:5173"' in z.read("gaer-extension/config.js").decode()
    assert "__GAER_NAME__" not in z.read("gaer-extension/manifest.json").decode()


async def test_embeddable_routes_allow_extension_and_office(client):
    csp = (await client.get("/extension")).headers["content-security-policy"]
    assert "chrome-extension:" in csp and "officeapps.live.com" in csp
    assert "frame-ancestors 'self';" in (await client.get("/admin")).headers["content-security-policy"]
    assert "appsforoffice.microsoft.com" in (await client.get("/office-addin")).headers["content-security-policy"]


async def test_embedded_login_gets_partitioned_cookie(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, origin="https://app.kunde.dk"))
    r = await client.post("/api/auth/otp/start", json={"phone": "12345678"})
    r = await client.post("/api/auth/otp/verify", json={"phone": "12345678", "code": r.json()["dev_code"]},
                          headers={"x-gaer-embedded": "1"})
    cookie = r.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "partitioned" in cookie and "secure" in cookie


async def test_front_page_has_login_card_and_first_user_notice(client, owner_conn):
    body = (await client.get("/w/")).text
    assert 'class="login-card"' in body and 'href="/login"' in body and "bliver administrator" in body
    assert "first person to log in becomes" in (await client.get("/w/?lang=en")).text
    await otp_login(client, "12345678")
    body = (await client.get("/w/?lang=da")).text
    assert 'class="login-card"' in body and "bliver administrator" not in body
    assert 'class="login-card"' not in (await client.get("/w/om")).text  # only on the front page


async def test_front_page_placeholder_when_none_written(client, owner_conn):
    saved = owner_conn.execute("select id from web_pages where slug = '' and lang = 'da'").fetchone()[0]
    owner_conn.execute("update web_pages set published = false where id = %s", (saved,))
    try:
        r = await client.get("/w/?lang=da")
        assert r.status_code == 200 and "Her kommer jeres website" in r.text and 'class="login-card"' in r.text
    finally:
        owner_conn.execute("update web_pages set published = true where id = %s", (saved,))

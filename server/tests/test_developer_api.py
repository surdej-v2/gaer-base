"""Gær Developer, fase 6: API-studie — /gaer/v<major>/<slug> over the same operations (API-1 … API-3), keys with scopes,
expiry, IP range, rotation and revocation (API-6, API-7), levels and idempotency (API-4, API-8), OpenAPI 3.1 and clients
(API-5), majors side by side with Deprecation/Sunset (API-9), the console (API-10), rate limits and monitoring (API-11),
ETag/Cache-Control/CORS (API-12)."""

from app import jobs
from tests.helpers import otp_login, stepup
from tests.mcp_helpers import mcp_token
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def _call(client, auth, method, url, body=None):
    send = getattr(client, method)
    r = await (send(url, json=body) if body is not None else send(url))
    if r.status_code == 403 and r.headers.get("x-gaer-error") == "stepup_required":
        await stepup(client, auth)
        r = await (send(url, json=body) if body is not None else send(url))
    return r


async def _api(admin, ids, **extra):
    async def op(slug, definition):
        r = await admin.post(f"{DEV}/operations", json={"slug": slug, "name": slug, "definition": definition})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    soeg = await op("api_soeg", {"source": {"dataset": ids["lejemaal"]}, "fields": ["id", "ejendom_id", "aarlig_leje", "lejer_navn"],
                                 "params": [{"name": "ejendom", "field": "ejendom_id"}], "sort": "id", "limit": 5, "cache": {"ttl": 60},
                                 "examples": [{"args": {"ejendom": "E-1"}}]})
    hent = await op("api_hent", {"source": {"dataset": ids["lejemaal"]}, "fields": ["id", "ejendom_id"],
                                 "params": [{"name": "id", "field": "id", "type": "int", "required": True}]})
    definition = {"name": "Lejedata", "cors": ["https://erp.example.dk"], "limits": {"per_key": 50},
                  "operations": [{"method": "GET", "path": "/lejemaal", "operation": soeg, "level": "white"},
                                 {"method": "GET", "path": "/lejemaal/:id", "operation": hent, "level": "white"},
                                 {"method": "POST", "path": "/lejemaal/soeg", "operation": soeg, "level": "yellow"}], **extra}
    r = await admin.post(f"{DEV}/apis", json={"slug": "lejeapi", "name": "Lejedata", "definition": definition})
    assert r.status_code == 201, r.text
    return r.json()["id"], soeg, hent, definition


async def _key(admin, auth, scopes, **extra):
    r = await _call(admin, auth, "post", f"{DEV}/api-keys", {"name": "ERP", "scopes": scopes, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_api_keys_read_masked_etag_cors_and_levels(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, soeg, hent, _ = await _api(admin, ids)
    key = await _key(admin, auth, ["GET /lejemaal*"], api_id=api_id)
    assert key["secret"].startswith(key["keyId"] + ".")
    assert owner_conn.execute("select secret_hash from dev_api_keys").fetchone()[0] != key["secret"]
    h = {"Authorization": f"Bearer {key['secret']}", "Origin": "https://erp.example.dk"}
    r = await admin.get("/gaer/v1/lejeapi/lejemaal?ejendom=E-1", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 5 and body["hasMore"] is True and body["data"][0]["lejer_navn"] == "•••"
    assert r.headers["cache-control"].startswith("private") and r.headers["access-control-allow-origin"] == "https://erp.example.dk"
    tag = r.headers["etag"]
    again = await admin.get("/gaer/v1/lejeapi/lejemaal?ejendom=E-1", headers={**h, "If-None-Match": tag})
    assert again.status_code == 304
    r = await admin.get("/gaer/v1/lejeapi/lejemaal/3", headers=h)
    assert r.status_code == 200 and r.json()["data"] == [{"id": 3, "ejendom_id": "E-1"}]
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal")).status_code == 401
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal", headers={"Authorization": "Bearer gk_000000000000.forkert"})).status_code == 401
    assert (await admin.get("/gaer/v1/lejeapi/ukendt", headers=h)).status_code == 404
    assert (await admin.delete("/gaer/v1/lejeapi/lejemaal", headers=h)).status_code == 405
    pre = await admin.options("/gaer/v1/lejeapi/lejemaal", headers={"Origin": "https://erp.example.dk"})
    assert pre.status_code == 204 and "Authorization" in pre.headers["access-control-allow-headers"]
    # Yellow: a wildcard scope is not enough; the exact operation is, with an Idempotency-Key.
    r = await admin.post("/gaer/v1/lejeapi/lejemaal/soeg", headers=h, json={"ejendom": "E-2"})
    assert r.status_code == 403
    exact = await _key(admin, auth, ["POST /lejemaal/soeg"], api_id=api_id)
    hx = {"Authorization": f"Bearer {exact['secret']}"}
    assert (await admin.post("/gaer/v1/lejeapi/lejemaal/soeg", headers=hx, json={"ejendom": "E-2"})).status_code == 428
    r1 = await admin.post("/gaer/v1/lejeapi/lejemaal/soeg", headers={**hx, "Idempotency-Key": "abc"}, json={"ejendom": "E-2"})
    assert r1.status_code == 200 and r1.json()["count"] == 5
    r2 = await admin.post("/gaer/v1/lejeapi/lejemaal/soeg", headers={**hx, "Idempotency-Key": "abc"}, json={"ejendom": "E-1"})
    assert r2.headers.get("idempotent-replayed") == "true" and r2.json() == r1.json()
    # Logged without keys or answers; aggregated per minute.
    log = owner_conn.execute("select path, key_id, status from dev_api_log order by id").fetchall()
    assert ("/lejemaal", key["keyId"], 200) in log and all("abc" not in str(x) for x in log)


async def test_rate_limit_ip_rotation_revocation_and_client_credentials(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, *_ = await _api(admin, ids, limits={"per_key": 2})
    key = await _key(admin, auth, ["GET *"], api_id=api_id)
    h = {"Authorization": f"Bearer {key['secret']}"}
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal", headers=h)).status_code == 200
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal", headers=h)).status_code == 200
    r = await admin.get("/gaer/v1/lejeapi/lejemaal", headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # IP range: the test client calls from 203.0.113.7.
    ipkey = await _key(admin, auth, ["GET *"], ip_allow=["10.0.0.0/8"])
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers={"Authorization": f"Bearer {ipkey['secret']}"})).status_code == 401
    # Rotation with no overlap: the old key stops at once, the new one works.
    r = await _call(admin, auth, "post", f"{DEV}/api-keys/{key['id']}/rotate", {"overlap_days": 0})
    new = r.json()
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers=h)).status_code == 401
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers={"Authorization": f"Bearer {new['secret']}"})).status_code == 200
    r = await _call(admin, auth, "post", f"{DEV}/api-keys/{new['id']}/revoke")
    assert r.status_code == 200
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers={"Authorization": f"Bearer {new['secret']}"})).status_code == 401
    # OAuth 2.0 client credentials.
    cc = await _key(admin, auth, ["GET /lejemaal/:id"], kind="client_credentials", api_id=api_id)
    t = await admin.post("/gaer/oauth/token", data={"grant_type": "client_credentials", "client_id": cc["clientId"],
                                                    "client_secret": cc["secret"]})
    assert t.status_code == 200, t.text
    token = t.json()["access_token"]
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/2", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal", headers={"Authorization": f"Bearer {token}"})).status_code == 403
    bad = await admin.post("/gaer/oauth/token", data={"grant_type": "client_credentials", "client_id": cc["clientId"], "client_secret": "x"})
    assert bad.status_code == 401
    # Only Udvikler-admin (and Fuld) create keys.
    dev = make_client()
    from tests.helpers import full_user
    dev_auth = await full_user(dev, "20000040")
    owner_conn.execute("update users set role = 'admin', admin_roles = '{udvikler}', audience = 'internal' where phone = '+4520000040'")
    r = await _call(dev, dev_auth, "post", f"{DEV}/api-keys", {"name": "x", "scopes": ["*"]})
    assert r.status_code == 403


async def test_user_tokens_external_white_only(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    await _api(admin, ids)
    ext = make_client()
    await otp_login(ext, "20000041")
    token = (await mcp_token(ext, "mcp"))["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert (await ext.get("/gaer/v1/lejeapi/lejemaal/1", headers=h)).status_code == 200
    r = await ext.post("/gaer/v1/lejeapi/lejemaal/soeg", headers={**h, "Idempotency-Key": "k"}, json={})
    assert r.status_code == 403 and "hvidt" in r.json()["error"]


async def test_majors_side_by_side_openapi_console_and_monitoring(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, soeg, hent, definition = await _api(admin, ids)
    key = await _key(admin, auth, ["GET *"], api_id=api_id)
    h = {"Authorization": f"Bearer {key['secret']}"}
    spec = (await admin.get("/gaer/v1/lejeapi/openapi.json")).json()
    assert spec["openapi"] == "3.1.0" and "/lejemaal/{id}" in spec["paths"]
    props = spec["paths"]["/lejemaal"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["data"]["items"]["properties"]
    assert props["lejer_navn"]["x-gaer-masking"] == "mask" and props["lejer_navn"]["x-gaer-classification"] == "red"
    code = (await admin.get(f"{DEV}/apis/{api_id}/client?lang=python")).json()["code"]
    assert "class Client" in code and "/lejemaal" in code
    # A major (an operation removed): v2 next to v1; v1 answers with Deprecation and Sunset.
    v2 = {**definition, "operations": [o for o in definition["operations"] if o["path"] != "/lejemaal/soeg"]}
    r = await admin.put(f"{DEV}/apis/{api_id}", json={"definition": v2, "submit": True})
    assert r.json()["version"]["changeType"] == "major" and r.json()["version"]["version"] == "2.0.0"
    old = await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers=h)
    assert old.status_code == 200 and "deprecation" in old.headers and "sunset" in old.headers
    new = await admin.get("/gaer/v2/lejeapi/lejemaal/1", headers=h)
    assert new.status_code == 200 and "deprecation" not in new.headers
    assert (await admin.post("/gaer/v2/lejeapi/lejemaal/soeg", headers=h, json={})).status_code == 405   # only GET /lejemaal/:id left
    detail = (await admin.get(f"{DEV}/apis/{api_id}")).json()
    assert [m["major"] for m in detail["majors"]] == [2, 1] and detail["majors"][1]["sunsetAt"]
    owner_conn.execute("update dev_api_majors set sunset_at = now() - interval '1 minute' where major = 1")
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers=h)).status_code == 410
    # The console runs as a test account and shows curl, without any key.
    acc = (await admin.post("/api/impersonation/users", json={"display_name": "Ekstern", "role": "user"})).json()["id"]
    r = await admin.post(f"{DEV}/apis/{api_id}/console", json={"path": "/lejemaal", "query": {"ejendom": "E-1"}, "as_user_id": acc})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == 200 and c["body"]["count"] == 5 and c["curl"].startswith("curl -H") and "$NOEGLE" in c["curl"]
    await jobs.HANDLERS["developer.metrics"]({"minutes": 5}, {})
    owner_conn.execute("update dev_api_log set ts = ts - interval '1 minute'")
    await jobs.HANDLERS["developer.metrics"]({"minutes": 5}, {})
    mon = (await admin.get(f"{DEV}/apis/{api_id}/monitor")).json()
    assert mon["callsToday"] >= 2 and mon["perKey"][0]["key_id"] == key["keyId"]

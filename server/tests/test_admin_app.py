"""The standalone admin app: /admin/* gets web/dist/admin.html, everything else index.html."""

from dataclasses import replace

import httpx

from app import config
from app.main import create_app
from tests.helpers import full_user


async def test_admin_paths_serve_admin_html(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("APP")
    (tmp_path / "admin.html").write_text("ADMIN")
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr(config, "settings", replace(config.settings, static_dir=str(tmp_path)))
    app = create_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"x-gaer": "1"})

    for path in ("/admin", "/admin/", "/admin/users", "/admin/app-stores/tekster"):
        r = await client.get(path)
        assert r.status_code == 200 and r.text == "ADMIN", path
    for path in ("/klip", "/administration", "/m/klip"):
        assert (await client.get(path)).text == "APP", path
    assert (await client.get("/")).status_code == 302  # anonymous: public website

    await full_user(client, "11111111")
    assert (await client.get("/")).text == "APP"


async def test_analytics_has_user_and_login_numbers(make_client):
    admin = make_client()
    await full_user(admin, "11111111")  # one grace login, then a passkey
    other = make_client()
    r = await other.post("/api/auth/otp/start", json={"phone": "22222222"})
    await other.post("/api/auth/otp/verify", json={"phone": "22222222", "code": "000000" if r.json()["dev_code"] != "000000" else "111111"})

    data = (await admin.get("/api/admin/analytics?days=7")).json()
    assert data["users"] == {"total": 1, "new": 1, "active": 1, "with_passkey": 1, "must_passkey": 0}
    assert data["logins"]["otp"] == 1 and data["logins"]["passkey"] == 0
    assert data["logins"]["failed"] == 1 and data["logins"]["failed_24h"] == 1
    assert sum(d["logins"] for d in data["logins_per_day"]) == 1

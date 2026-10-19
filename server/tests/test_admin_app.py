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

"""Self-service shop: products, QR labels, checkout with MobilePay (console + mocked API), receipts."""

from app import config, payments
from tests.helpers import full_user, otp_login


async def _admin(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    return admin


async def _product(admin, **kw) -> dict:
    body = {"name": "Æbler, 1 kg", "description": "Fra haven", "price_ore": 2500, "stock": None, "active": True} | kw
    r = await admin.post("/api/admin/shop/products", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _checkout(client, code: str, quantity: int = 1):
    return await client.post("/api/public/shop/checkout", json={"code": code, "quantity": quantity})


async def test_only_admins_manage_products(make_client, owner_conn):
    admin = await _admin(make_client)
    user = make_client()
    await otp_login(user, "22222222")
    anonymous = make_client()

    p = await _product(admin)
    assert p["url"] == f"http://localhost:5173/w/butik/{p['code']}"
    for c in (user, anonymous):
        assert (await c.get("/api/admin/shop/products")).status_code in (401, 403)
        assert (await c.post("/api/admin/shop/products", json={"name": "x", "price_ore": 1})).status_code in (401, 403)

    r = await admin.put(f"/api/admin/shop/products/{p['id']}",
                        json={"name": "Æbler, 1 kg", "description": "Fra haven", "price_ore": 3000, "stock": 5, "active": True})
    assert r.status_code == 200 and r.json()["price_ore"] == 3000
    assert (await admin.delete(f"/api/admin/shop/products/{p['id']}")).status_code == 200

    rows = owner_conn.execute("""select action, before, after from audit_log where table_name = 'shop_products'
                                 order by id""").fetchall()
    assert [r[0] for r in rows] == ["create", "update", "delete"]
    assert rows[1][1] == {"price_ore": 2500, "stock": None} and rows[1][2] == {"price_ore": 3000, "stock": 5}


async def test_labels_have_qr_codes(make_client):
    admin = await _admin(make_client)
    p = await _product(admin)
    await _product(admin, name="Gammel vare", active=False)
    labels = (await admin.get("/api/admin/shop/labels")).json()
    assert len(labels) == 1 and labels[0]["id"] == p["id"]
    assert labels[0]["svg"].startswith("<svg") and "viewBox" in labels[0]["svg"]


async def test_buy_with_console_payment(make_client, owner_conn):
    admin = await _admin(make_client)
    p = await _product(admin, stock=3)
    buyer = make_client("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1")

    page = await buyer.get(f"/w/butik/{p['code']}")
    assert page.status_code == 200 and "Æbler, 1 kg" in page.text and "25,00 kr." in page.text
    assert "Betal med MobilePay" in page.text

    r = await _checkout(buyer, p["code"], 2)
    assert r.status_code == 200, r.text
    redirect, order_url = r.json()["redirect_url"], r.json()["order_url"]
    token = order_url.rsplit("/", 1)[1]
    assert redirect == f"/w/butik/testbetaling/{token}"

    pending = await buyer.get(order_url)
    assert "Venter på betaling" in pending.text and 'http-equiv="refresh"' in pending.text

    assert (await buyer.get(redirect)).status_code == 200
    r = await buyer.post(redirect, data={"answer": "approve"})
    assert r.status_code == 303 and r.headers["location"] == order_url

    receipt = await buyer.get(order_url)
    assert "Betalt" in receipt.text and "50,00 kr." in receipt.text and "#1" in receipt.text
    assert owner_conn.execute("select stock from shop_products").fetchone()[0] == 1

    # Paying again does nothing: the order is already paid and stock is not counted twice.
    await buyer.post(redirect, data={"answer": "approve"})
    assert owner_conn.execute("select stock from shop_products").fetchone()[0] == 1

    orders = (await admin.get("/api/admin/shop/orders?status=unfulfilled")).json()
    assert orders["total"] == 1 and orders["summary"]["today_ore"] == 5000 and orders["summary"]["unfulfilled"] == 1
    assert (await admin.post(f"/api/admin/shop/orders/{orders['items'][0]['id']}/fulfil")).status_code == 200
    assert (await admin.get("/api/admin/shop/orders?status=unfulfilled")).json()["total"] == 0
    assert "Udleveret" in (await buyer.get(order_url)).text

    rows = owner_conn.execute("""select action, actor_user_id, after from audit_log where table_name = 'shop_orders'
                                 order by id""").fetchall()
    assert [r[0] for r in rows] == ["create", "update", "update"]
    assert rows[0][1] is None and rows[0][2]["amount_ore"] == 5000
    assert rows[1][2] == {"status": "paid", "provider_state": "AUTHORIZED"}
    assert rows[2][1] is not None and "fulfilled_at" in rows[2][2]

    # Orders hold no personal data.
    cols = {r[0] for r in owner_conn.execute("""select column_name from information_schema.columns
                                                where table_name = 'shop_orders'""")}
    assert not cols & {"user_id", "phone", "ip"}


async def test_rejected_payment_is_cancelled(make_client, owner_conn):
    admin = await _admin(make_client)
    p = await _product(admin, stock=3)
    buyer = make_client()
    r = (await _checkout(buyer, p["code"])).json()
    await buyer.post(r["redirect_url"], data={"answer": "reject"})
    page = await buyer.get(r["order_url"])
    assert "ikke gennemført" in page.text and "Prøv igen" in page.text
    assert owner_conn.execute("select stock from shop_products").fetchone()[0] == 3
    order_id = owner_conn.execute("select id from shop_orders").fetchone()[0]
    assert (await admin.post(f"/api/admin/shop/orders/{order_id}/fulfil")).status_code == 409


async def test_sold_out_inactive_and_unknown(make_client):
    admin = await _admin(make_client)
    sold_out = await _product(admin, stock=0)
    few = await _product(admin, name="Pærer", stock=2)
    hidden = await _product(admin, name="Skjult", active=False)
    buyer = make_client()

    assert (await _checkout(buyer, sold_out["code"])).status_code == 409
    r = await _checkout(buyer, few["code"], 3)
    assert r.status_code == 409 and r.json()["detail"] == "Der er kun 2 tilbage"
    assert (await _checkout(buyer, hidden["code"])).status_code == 404
    assert (await buyer.get(f"/w/butik/{hidden['code']}")).status_code == 404
    assert "Udsolgt" in (await buyer.get(f"/w/butik/{sold_out['code']}")).text
    assert (await buyer.get("/w/butik/ordre/" + "0" * 32)).status_code == 404
    assert (await buyer.get("/w/butik/ordre/nope")).status_code == 404

    index = (await buyer.get("/w/butik")).text
    assert "Pærer" in index and "Skjult" not in index

    # Without the CSRF header the public checkout is refused like every other /api POST.
    r = await buyer.post("/api/public/shop/checkout", json={"code": few["code"]}, headers={"x-gaer": ""})
    assert r.status_code == 403


async def test_checkout_rate_limit(make_client, owner_conn):
    admin = await _admin(make_client)
    p = await _product(admin)
    buyer = make_client()
    for _ in range(30):
        assert (await _checkout(buyer, p["code"])).status_code == 200
    assert (await _checkout(buyer, p["code"])).status_code == 429


async def test_mobilepay_flow(make_client, owner_conn, monkeypatch):
    calls = []
    state = {"value": "CREATED"}

    async def create_payment(*, reference, amount_ore, description, return_url):
        calls.append(("create", reference, amount_ore, return_url))
        return f"https://pay.mobilepay.example/{reference}"

    async def get_state(reference):
        calls.append(("state", reference))
        return state["value"]

    async def capture(reference, amount_ore):
        calls.append(("capture", reference, amount_ore))

    monkeypatch.setattr(payments, "create_payment", create_payment)
    monkeypatch.setattr(payments, "get_state", get_state)
    monkeypatch.setattr(payments, "capture", capture)
    monkeypatch.setattr(config, "settings", config.Settings(payment_provider="mobilepay"))

    admin = await _admin(make_client)
    p = await _product(admin, price_ore=1995)
    buyer = make_client()
    r = (await _checkout(buyer, p["code"])).json()
    token = r["order_url"].rsplit("/", 1)[1]
    assert r["redirect_url"] == f"https://pay.mobilepay.example/{token}"
    assert calls[0] == ("create", token, 1995, f"http://localhost:5173/w/butik/ordre/{token}")

    # The console test page does not exist with a real provider.
    assert (await buyer.get(f"/w/butik/testbetaling/{token}")).status_code == 404

    assert "Venter på betaling" in (await buyer.get(r["order_url"])).text
    state["value"] = "AUTHORIZED"
    assert "Betalt" in (await buyer.get(r["order_url"])).text
    assert "Betalt" in (await buyer.get(r["order_url"])).text
    assert [c for c in calls if c[0] == "capture"] == [("capture", token, 1995)]  # captured exactly once

    # An admin can check an order whose buyer never came back.
    r2 = (await _checkout(buyer, p["code"])).json()
    state["value"] = "EXPIRED"
    order_id = owner_conn.execute("select id from shop_orders where token = %s",
                                  (r2["order_url"].rsplit("/", 1)[1],)).fetchone()[0]
    r = await admin.post(f"/api/admin/shop/orders/{order_id}/sync")
    assert r.json() == {"status": "cancelled", "provider_state": "EXPIRED"}


async def test_failed_start_cancels_order(make_client, owner_conn, monkeypatch):
    async def create_payment(**_):
        raise payments.PaymentError("down")

    monkeypatch.setattr(payments, "create_payment", create_payment)
    admin = await _admin(make_client)
    p = await _product(admin)
    r = await _checkout(make_client(), p["code"])
    assert r.status_code == 502
    assert owner_conn.execute("select status, provider_state from shop_orders").fetchone() == ("cancelled", "START_FAILED")


def test_console_refused_in_production(monkeypatch):
    monkeypatch.setattr(config, "settings", config.Settings(environment="production", payment_provider="console"))
    try:
        payments.provider()
    except payments.PaymentError:
        return
    raise AssertionError("console provider must be refused in production")

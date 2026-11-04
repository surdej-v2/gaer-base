"""Self-service shop: products with a printed QR code, paid with MobilePay.

Flow for a buyer (anonymous, no login):
  scan QR → /w/butik/<code> (product, quantity) → POST /api/public/shop/checkout → MobilePay
  → back to /w/butik/ordre/<token> (receipt: "Betalt", order number, what and how many).

Admins manage products, print QR labels and follow and fulfil orders in /admin/shop (/api/admin/shop).

Orders hold no personal data (no user, no phone number), so there is nothing to add to the GDPR export
or erasure. The buyer's IP is only in the audit log, which has its own retention.
"""

import os
import secrets
from html import escape

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import config, db, payments, settings_store
from app.logs import RequestMeta, audit, changed
from app.security import client_ip
from app.sessions import Principal, request_meta, require_admin

router = APIRouter(tags=["shop"])
admin_router = APIRouter(prefix="/api/admin/shop", tags=["shop"], dependencies=[Depends(require_admin)])

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web_static")
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: readable if someone types the address
CHECKOUTS_PER_IP_PER_10_MIN = 30


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def product_url(code: str) -> str:
    return f"{config.settings.origin.rstrip('/')}/w/butik/{code}"


def qr_svg(url: str) -> str:
    return segno.make(url, error="m", micro=False).svg_inline(scale=1, border=2, omitsize=True)


def kr(ore: int) -> str:
    """12345 → '123,45 kr.' (Danish format)."""
    whole = f"{ore // 100:,}".replace(",", ".")
    return f"{whole},{ore % 100:02d} kr."


# ── Order state ─────────────────────────────────────────────────────────────

async def sync_order(conn, order: dict, meta: RequestMeta, *, state: str | None = None) -> dict:
    """Brings a pending order up to date with the payment provider. The caller has locked the row
    (`for update`), so two requests cannot capture or count stock twice. Returns the current row.
    `state` is only passed by the console test page, which stands in for the provider."""
    if order["status"] != "pending":
        return order
    if state is None:
        if order["provider"] == "mobilepay":
            state = await payments.get_state(order["token"])
        else:
            state = order["provider_state"]  # console: only the test page changes it
    status = order["status"]
    if state == "AUTHORIZED":
        if order["provider"] == "mobilepay":
            await payments.capture(order["token"], order["amount_ore"])
        status = "paid"
    elif state in payments.FAILED_STATES:
        status = "cancelled"
    if status == order["status"] and state == order["provider_state"]:
        return order
    row = await db.fetch_one(conn, """
        update shop_orders set status = %s, provider_state = %s,
               paid_at = case when %s = 'paid' then now() else paid_at end
        where id = %s returning *""", (status, state, status, order["id"]))
    if status == "paid" and order["product_id"] is not None:
        await conn.execute("update shop_products set stock = greatest(stock - %s, 0), updated_at = now() "
                           "where id = %s and stock is not null", (order["quantity"], order["product_id"]))
    b, a = changed({"status": order["status"], "provider_state": order["provider_state"]},
                   {"status": status, "provider_state": state})
    await audit(conn, "update", actor_id=None, meta=meta, table="shop_orders", row_id=order["id"], before=b, after=a)
    return row


async def _locked_order(conn, token: str) -> dict | None:
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        return None
    return await db.fetch_one(conn, "select * from shop_orders where token = %s for update", (token,))


# ── Public API ──────────────────────────────────────────────────────────────

class CheckoutIn(BaseModel):
    code: str = Field(pattern="^[a-z0-9]{6,16}$")
    quantity: int = Field(default=1, ge=1, le=99)


@router.post("/api/public/shop/checkout")
async def checkout(body: CheckoutIn, request: Request):
    meta = request_meta(request)
    try:
        provider = payments.provider()
    except payments.PaymentError:
        raise HTTPException(503, "Betaling er ikke sat op endnu")
    token = secrets.token_hex(16)
    async with db.connection() as conn:
        recent = await db.fetch_one(conn, """
            select count(*) as n from audit_log
            where table_name = 'shop_orders' and action = 'create' and actor_user_id is null and ip = %s
              and ts > now() - interval '10 minutes'""", (client_ip(request),))
        if recent["n"] >= CHECKOUTS_PER_IP_PER_10_MIN:
            raise HTTPException(429, "For mange køb på kort tid. Prøv igen om lidt.")
        product = await db.fetch_one(conn, "select * from shop_products where code = %s and active", (body.code,))
        if product is None:
            raise HTTPException(404, "Varen findes ikke")
        if product["stock"] is not None and product["stock"] < body.quantity:
            raise HTTPException(409, "Udsolgt" if product["stock"] == 0 else f"Der er kun {product['stock']} tilbage")
        amount = product["price_ore"] * body.quantity
        order = await db.fetch_one(conn, """
            insert into shop_orders (token, product_id, product_name, quantity, unit_price_ore, amount_ore, provider)
            values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (token, product["id"], product["name"], body.quantity, product["price_ore"], amount, provider))
        await audit(conn, "create", actor_id=None, meta=meta, table="shop_orders", row_id=order["id"],
                    after={"product_id": product["id"], "quantity": body.quantity, "amount_ore": amount,
                           "provider": provider})
    # The order is committed before the buyer leaves, so the return page always finds it.
    order_path = f"/w/butik/ordre/{token}"
    try:
        redirect = await payments.create_payment(
            reference=token, amount_ore=amount, description=f"{product['name']} × {body.quantity}",
            return_url=f"{config.settings.origin.rstrip('/')}{order_path}")
    except payments.PaymentError:
        async with db.connection() as conn:
            await conn.execute("update shop_orders set status = 'cancelled', provider_state = 'START_FAILED' "
                               "where id = %s", (order["id"],))
            await audit(conn, "update", actor_id=None, meta=meta, table="shop_orders", row_id=order["id"],
                        before={"status": "pending"}, after={"status": "cancelled", "provider_state": "START_FAILED"})
        raise HTTPException(502, "Betalingen kunne ikke startes. Prøv igen.")
    return {"redirect_url": redirect, "order_url": order_path}


# ── Public pages (/w/butik) ─────────────────────────────────────────────────
# Registered before the /w catch-all in web.py. Server-rendered like the rest of /w.

def _shell(values: dict, title: str, body: str, *, script: bool = True, refresh: int | None = None,
           status: int = 200) -> HTMLResponse:
    name = escape(values["app_name"])
    logo = '<img src="/api/public/logo" alt="">' if values.get("logo_key") else '<span class="dot"></span>'
    meta_refresh = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    # No w.js here: its consent banner would cover the pay button, and the shop pages need no analytics.
    scripts = '<script src="/w/static/shop.js" defer></script>' if script else ""
    html = f"""<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">{meta_refresh}
<title>{escape(title)} · {name}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/w/static/w.css">
{scripts}
</head>
<body class="shop">
<header class="top"><div class="inner">
  <a class="brand" href="/w/">{logo}{name}</a>
  <nav><a href="/w/butik">Alle varer</a></nav>
</div></header>
<main class="content">{body}</main>
<footer><div class="inner"><span>© {name}</span><a href="/privatliv">Privatliv</a></div></footer>
</body>
</html>"""
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/w/static/shop.js", include_in_schema=False)
async def shop_js():
    return FileResponse(os.path.join(STATIC_DIR, "shop.js"), headers={"Cache-Control": "public, max-age=300"})


@router.get("/w/butik", response_class=HTMLResponse, include_in_schema=False)
async def shop_index():
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        products = await db.fetch_all(conn, "select code, name, price_ore, stock from shop_products "
                                            "where active order by name")
    items = "".join(
        f'<a class="shop-item" href="/w/butik/{p["code"]}"><span>{escape(p["name"])}</span>'
        f'<strong>{"Udsolgt" if p["stock"] == 0 else kr(p["price_ore"])}</strong></a>' for p in products)
    body = f'<h1>Butik</h1><div class="shop-list">{items or "<p>Der er ingen varer endnu.</p>"}</div>'
    return _shell(values, "Butik", body)


@router.get("/w/butik/ordre/{token}", response_class=HTMLResponse, include_in_schema=False)
async def receipt(token: str, request: Request):
    meta = request_meta(request)
    notice = ""
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        order = await _locked_order(conn, token)
        if order is None:
            return _shell(values, "Ordren findes ikke", "<h1>Ordren findes ikke</h1>", script=False, status=404)
        try:
            order = await sync_order(conn, order, meta)
        except payments.PaymentError:
            notice = '<p class="shop-note">Vi kan ikke nå MobilePay lige nu. Siden prøver igen af sig selv.</p>'
        code = None
        if order["product_id"]:
            p = await db.fetch_one(conn, "select code from shop_products where id = %s", (order["product_id"],))
            code = p and p["code"]
    lines = (f'<dl class="shop-lines"><dt>Vare</dt><dd>{escape(order["product_name"])} × {order["quantity"]}</dd>'
             f'<dt>Beløb</dt><dd>{kr(order["amount_ore"])}</dd></dl>')
    back = f'<a class="shop-button secondary" href="/w/butik/{code}">Prøv igen</a>' if code else ""
    if order["status"] == "paid":
        paid = order["paid_at"].astimezone().strftime("%d.%m.%Y kl. %H:%M")
        handed = '<p class="shop-pill">Udleveret</p>' if order["fulfilled_at"] else ""
        body = (f'<div class="shop-receipt paid"><div class="shop-mark">✓</div><h1>Betalt</h1>'
                f'<p class="shop-order">Ordre <strong>#{order["id"]}</strong></p>{lines}'
                f'<p class="shop-small">Betalt {paid}. Tag din vare. Vis denne side, hvis nogen spørger.</p>{handed}</div>')
        return _shell(values, f"Ordre #{order['id']} · Betalt", body, script=False)
    if order["status"] == "cancelled":
        body = (f'<div class="shop-receipt cancelled"><div class="shop-mark">✕</div><h1>Betalingen blev ikke gennemført</h1>'
                f'{lines}<p class="shop-small">Der er ikke trukket penge.</p>{back}</div>')
        return _shell(values, "Betaling afbrudt", body, script=False)
    body = (f'<div class="shop-receipt pending"><div class="shop-mark spin">◌</div><h1>Venter på betaling …</h1>'
            f'{lines}<p class="shop-small">Godkend betalingen i MobilePay. Siden opdaterer af sig selv.</p>{notice}</div>')
    return _shell(values, "Venter på betaling", body, script=False, refresh=3)


def _console_only():
    if config.settings.payment_provider != "console" or config.settings.is_production:
        raise HTTPException(404)


@router.get("/w/butik/testbetaling/{token}", response_class=HTMLResponse, include_in_schema=False)
async def test_payment(token: str):
    """Stands in for MobilePay when PAYMENT_PROVIDER=console. Never available in production."""
    _console_only()
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        order = await _locked_order(conn, token)
    if order is None or order["provider"] != "console":
        raise HTTPException(404)
    body = (f'<div class="shop-receipt pending"><p class="shop-pill">Testbetaling · ingen rigtige penge</p>'
            f'<h1>{kr(order["amount_ore"])}</h1><p>{escape(order["product_name"])} × {order["quantity"]}</p>'
            f'<form method="post" class="shop-actions">'
            f'<button class="shop-button" name="answer" value="approve">Godkend</button>'
            f'<button class="shop-button secondary" name="answer" value="reject">Afvis</button></form></div>')
    return _shell(values, "Testbetaling", body, script=False)


@router.post("/w/butik/testbetaling/{token}", include_in_schema=False)
async def test_payment_answer(token: str, request: Request, answer: str = Form(pattern="^(approve|reject)$")):
    _console_only()
    meta = request_meta(request)
    async with db.connection() as conn:
        order = await _locked_order(conn, token)
        if order is None or order["provider"] != "console":
            raise HTTPException(404)
        await sync_order(conn, order, meta, state="AUTHORIZED" if answer == "approve" else "ABORTED")
    return RedirectResponse(f"/w/butik/ordre/{token}", status_code=303)


@router.get("/w/butik/{code}", response_class=HTMLResponse, include_in_schema=False)
async def product_page(code: str):
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        product = await db.fetch_one(conn, "select * from shop_products where code = %s and active", (code,))
    if product is None:
        return _shell(values, "Varen findes ikke",
                      '<h1>Varen findes ikke</h1><p>Koden er ikke i brug længere. <a href="/w/butik">Se alle varer</a>.</p>',
                      status=404)
    stock = product["stock"]
    desc = f'<p class="shop-desc">{escape(product["description"])}</p>' if product["description"] else ""
    if stock == 0:
        form = '<p class="shop-pill">Udsolgt</p>'
    else:
        top = min(stock if stock is not None else 10, 10)
        options = "".join(f'<option value="{n}">{n}</option>' for n in range(1, top + 1))
        left = f'<p class="shop-small">{stock} på lager</p>' if stock is not None and stock <= 10 else ""
        form = f"""<form class="shop-buy" data-checkout data-code="{product['code']}" data-price="{product['price_ore']}">
  <label>Antal <select name="quantity">{options}</select></label>
  <p class="shop-total">I alt <strong data-total>{kr(product['price_ore'])}</strong></p>
  <button class="shop-button mobilepay" type="submit">Betal med MobilePay</button>
  <p class="shop-error" data-error hidden></p>
  {left}
</form>"""
    body = (f'<div class="shop-product"><h1>{escape(product["name"])}</h1>{desc}'
            f'<p class="shop-price">{kr(product["price_ore"])}</p>{form}</div>')
    return _shell(values, product["name"], body)


# ── Admin API (/admin/shop) ─────────────────────────────────────────────────

class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    price_ore: int = Field(gt=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)
    active: bool = True


PRODUCT_FIELDS = ("name", "description", "price_ore", "stock", "active")


def _with_url(p: dict) -> dict:
    return {**p, "url": product_url(p["code"])}


@admin_router.get("/products")
async def list_products():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select p.*, coalesce(s.sold, 0) as sold, coalesce(s.revenue_ore, 0) as revenue_ore
            from shop_products p
            left join (select product_id, sum(quantity) as sold, sum(amount_ore) as revenue_ore
                       from shop_orders where status = 'paid' group by product_id) s on s.product_id = p.id
            order by p.active desc, p.name""")
    return [_with_url(r) for r in rows]


@admin_router.post("/products")
async def create_product(body: ProductIn, admin: Principal = Depends(require_admin)):
    data = body.model_dump()
    data["name"] = data["name"].strip()
    async with db.connection() as conn:
        for _ in range(5):  # codes are random; retry the rare collision
            code = new_code()
            if not await db.fetch_one(conn, "select 1 as x from shop_products where code = %s", (code,)):
                break
        row = await db.fetch_one(conn, """
            insert into shop_products (code, name, description, price_ore, stock, active, created_by)
            values (%s, %s, %s, %s, %s, %s, %s) returning *""",
            (code, data["name"], data["description"], data["price_ore"], data["stock"], data["active"], admin.user_id))
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="shop_products", row_id=row["id"],
                    after={k: row[k] for k in ("code", *PRODUCT_FIELDS)})
    return _with_url(row)


@admin_router.put("/products/{product_id}")
async def update_product(product_id: int, body: ProductIn, admin: Principal = Depends(require_admin)):
    data = body.model_dump()
    data["name"] = data["name"].strip()
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select * from shop_products where id = %s for update", (product_id,))
        if before is None:
            raise HTTPException(404, "Varen findes ikke")
        row = await db.fetch_one(conn, """
            update shop_products set name = %s, description = %s, price_ore = %s, stock = %s, active = %s,
                   updated_at = now() where id = %s returning *""",
            (data["name"], data["description"], data["price_ore"], data["stock"], data["active"], product_id))
        b, a = changed({k: before[k] for k in PRODUCT_FIELDS}, data)
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="shop_products",
                        row_id=product_id, before=b, after=a)
    return _with_url(row)


@admin_router.delete("/products/{product_id}")
async def delete_product(product_id: int, admin: Principal = Depends(require_admin)):
    """Orders keep their snapshot (name, price); their product_id becomes null."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from shop_products where id = %s returning code, name, price_ore",
                                 (product_id,))
        if row is None:
            raise HTTPException(404, "Varen findes ikke")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="shop_products",
                    row_id=product_id, before=row)
    return {"ok": True}


@admin_router.get("/labels")
async def labels(ids: str | None = None):
    """QR labels to print: every active product, or the given ids (comma separated)."""
    wanted = [int(x) for x in (ids or "").split(",") if x.strip().isdigit()][:500]
    async with db.connection() as conn:
        if wanted:
            rows = await db.fetch_all(conn, "select id, code, name, price_ore from shop_products where id = any(%s) "
                                            "order by name", (wanted,))
        else:
            rows = await db.fetch_all(conn, "select id, code, name, price_ore from shop_products where active "
                                            "order by name")
    return [{**r, "url": product_url(r["code"]), "svg": qr_svg(product_url(r["code"]))} for r in rows]


@admin_router.get("/orders")
async def list_orders(status: str | None = None, limit: int = 100, offset: int = 0):
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    where, params = "", []
    if status == "unfulfilled":
        where = "where status = 'paid' and fulfilled_at is null"
    elif status in ("pending", "paid", "cancelled"):
        where, params = "where status = %s", [status]
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""
            select id, product_id, product_name, quantity, unit_price_ore, amount_ore, currency, provider, status,
                   provider_state, created_at, paid_at, fulfilled_at, fulfilled_by
            from shop_orders {where} order by created_at desc limit %s offset %s""", params + [limit, offset])
        total = await db.fetch_one(conn, f"select count(*) as n from shop_orders {where}", params)
        summary = await db.fetch_one(conn, """
            select coalesce(sum(amount_ore) filter (where paid_at >= date_trunc('day', now())), 0) as today_ore,
                   count(*) filter (where paid_at >= date_trunc('day', now())) as today_orders,
                   coalesce(sum(amount_ore) filter (where paid_at >= now() - interval '30 days'), 0) as month_ore,
                   count(*) filter (where status = 'paid' and fulfilled_at is null) as unfulfilled
            from shop_orders where status = 'paid'""")
    return {"items": items, "total": total["n"], "summary": summary}


@admin_router.post("/orders/{order_id}/fulfil")
async def fulfil_order(order_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        order = await db.fetch_one(conn, "select status, fulfilled_at from shop_orders where id = %s for update",
                                   (order_id,))
        if order is None:
            raise HTTPException(404, "Ordren findes ikke")
        if order["status"] != "paid":
            raise HTTPException(409, "Kun betalte ordrer kan udleveres")
        if order["fulfilled_at"] is None:
            row = await db.fetch_one(conn, "update shop_orders set fulfilled_at = now(), fulfilled_by = %s "
                                           "where id = %s returning fulfilled_at", (admin.user_id, order_id))
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="shop_orders", row_id=order_id,
                        before={"fulfilled_at": None}, after={"fulfilled_at": row["fulfilled_at"]})
    return {"ok": True}


@admin_router.post("/orders/{order_id}/sync")
async def sync_order_now(order_id: int, admin: Principal = Depends(require_admin)):
    """Checks a pending order with MobilePay, e.g. when the buyer closed the browser after paying."""
    async with db.connection() as conn:
        order = await db.fetch_one(conn, "select * from shop_orders where id = %s for update", (order_id,))
        if order is None:
            raise HTTPException(404, "Ordren findes ikke")
        try:
            order = await sync_order(conn, order, admin.meta)
        except payments.PaymentError:
            raise HTTPException(502, "MobilePay svarer ikke. Prøv igen om lidt.")
    return {"status": order["status"], "provider_state": order["provider_state"]}

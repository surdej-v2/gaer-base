"""/admin/app-stores: graphics, texts and links for App Store and Google Play, after happy-groups
modules/member-brand-portal (StoreStudio) without white-label: one app per instance.

    GET /api/admin/app-store   stored edits + defaults derived from Indstillinger and ORIGIN
    PUT /api/admin/app-store   merge a partial update; null removes an edited value

The Happy AI side panel guides (surdej-v1, /extension/tools/app-stores) send what they collected:

    {"accounts": {"apple":  {"team_id": "A1B2C3D4E5", "issuer_id": "<uuid>", "key_id": "2X9R4HXF34"},
                  "google": {"developer_id": "1234567890123456789", "service_account": "…@….iam.gserviceaccount.com"},
                  "expo":   {"owner": "my-gaer", "project_id": "<uuid>"}}}

Private keys (.p8, service account JSON) and the Expo robot token never come here.
"""

import re
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app import config, db, settings_store
from app.logs import audit
from app.sessions import Principal, require_admin

router = APIRouter(prefix="/api/admin/app-store", tags=["admin"], dependencies=[Depends(require_admin)])

UUID = r"^[0-9a-fA-F-]{36}$"
HEX = r"^#[0-9a-fA-F]{6}$"
TEXT_KEYS = ("appName", "subtitle", "promo", "description", "keywords", "whatsNew", "shortDesc", "releaseNotes",
             "cap1", "cap2", "cap3", "cap4")
LINK_KEYS = ("support", "marketing", "privacy", "deletion", "email", "reviewLogin", "ascAppId", "playAppId", "easProjectId",
             "copyright")
MANUAL_KEYS = {"apple": ("privacy", "age", "price", "testflight"),
               "google": ("datasafety", "rating", "audience", "assetlinks", "internal")}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CfgIn(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    bundleId: str | None = Field(default=None, pattern=r"^[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z0-9_-]+)+$", max_length=155)
    primary: str | None = Field(default=None, pattern=HEX)
    primarySoft: str | None = Field(default=None, pattern=HEX)
    iconBg: str | None = Field(default=None, pattern=HEX)
    splashBg: str | None = Field(default=None, pattern=HEX)


class AppleIn(Strict):
    team_id: str | None = Field(default=None, pattern=r"^[A-Z0-9]{10}$")
    issuer_id: str | None = Field(default=None, pattern=UUID)
    key_id: str | None = Field(default=None, pattern=r"^[A-Z0-9]{10}$")


class GoogleIn(Strict):
    developer_id: str | None = Field(default=None, pattern=r"^\d{10,20}$")
    service_account: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com$", max_length=200)


class ExpoIn(Strict):
    owner: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,}$", max_length=60)
    project_id: str | None = Field(default=None, pattern=UUID)


class AccountsIn(Strict):
    apple: AppleIn | None = None
    google: GoogleIn | None = None
    expo: ExpoIn | None = None


class AppStoreIn(Strict):
    cfg: CfgIn | None = None
    texts: dict[str, dict[str, str | None]] | None = None      # {"da": {"subtitle": "…"}}; null resets a field
    links: dict[str, str | None] | None = None
    manual: dict[str, dict[str, bool]] | None = None            # {"apple": {"privacy": true}}
    accounts: AccountsIn | None = None


def defaults(values: dict) -> dict:
    """What the app gets until an admin edits it: name from Indstillinger, ids from the instance's domain."""
    host = urlparse(config.settings.origin).hostname or "localhost"
    labels = [re.sub(r"[^a-z0-9]", "", p.lower()) for p in host.split(".")]
    labels = [p for p in labels if p] or ["app"]
    slug = labels[0] if len(labels) == 1 else labels[-2]
    origin = f"https://{host}"
    name = (values.get("app_name") or "Tørgær")[:30]
    return {
        "host": host,
        "cfg": {"name": name, "bundleId": ".".join([*reversed(labels), "app"]), "primary": "#2563eb", "primarySoft": "#dbeafe",
                "iconBg": "#ffffff", "splashBg": "#f8fafc"},
        "links": {"support": f"{origin}/w/support", "marketing": f"{origin}/w/", "privacy": f"{origin}/privatliv",
                  "deletion": f"{origin}/konto#slet", "email": values.get("gdpr_email") or "", "reviewLogin": "",
                  "ascAppId": "", "playAppId": "", "easProjectId": "", "copyright": f"{date.today().year} {name}"},
        "ids": {"scheme": f"gaer-{slug}", "sku": f"gaer-{slug}", "rp_id": config.settings.rp_id},
    }


def _merge(data: dict, body: AppStoreIn) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()}
    if body.cfg:
        out["cfg"] = {**out.get("cfg", {}), **body.cfg.model_dump(exclude_none=True)}
    if body.texts:
        texts = {k: dict(v) for k, v in out.get("texts", {}).items()}
        for loc, fields in body.texts.items():
            if loc not in ("da", "en"):
                continue
            cur = texts.setdefault(loc, {})
            for k, v in fields.items():
                if k not in TEXT_KEYS:
                    continue
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v[:4000]
        out["texts"] = texts
    if body.links:
        links = dict(out.get("links", {}))
        for k, v in body.links.items():
            if k not in LINK_KEYS:
                continue
            if v is None:
                links.pop(k, None)
            else:
                links[k] = v.strip()[:500]
        out["links"] = links
    if body.manual:
        manual = {k: dict(v) for k, v in out.get("manual", {}).items()}
        for store, steps in body.manual.items():
            allowed = MANUAL_KEYS.get(store, ())
            manual.setdefault(store, {}).update({k: v for k, v in steps.items() if k in allowed})
        out["manual"] = manual
    if body.accounts:
        accounts = {k: dict(v) for k, v in out.get("accounts", {}).items()}
        for part, value in body.accounts.model_dump(exclude_none=True).items():
            accounts[part] = {**accounts.get(part, {}), **value}
        out["accounts"] = accounts
    return out


@router.get("")
async def get_app_store():
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select data, updated_at from app_store where id = 1")
        values = await settings_store.get_all(conn)
    return {"data": row["data"] if row else {}, "updated_at": row["updated_at"] if row else None,
            "defaults": defaults(values), "logo_url": "/api/public/logo" if values["logo_key"] else None}


@router.put("")
async def put_app_store(body: AppStoreIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select data from app_store where id = 1 for update")
        before = row["data"] if row else {}
        after = _merge(before, body)
        await conn.execute("""
            insert into app_store (id, data, updated_by) values (1, %s, %s)
            on conflict (id) do update set data = excluded.data, updated_by = excluded.updated_by, updated_at = now()""",
            (Jsonb(after), admin.user_id))
        keys = [k for k in after if before.get(k) != after.get(k)]
        if keys:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="app_store", row_id=1,
                        before={k: before.get(k) for k in keys}, after={k: after[k] for k in keys})
    return {"data": after}

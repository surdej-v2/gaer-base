"""/api/admin/config: Konfiguration — settings stored in Postgres instead of the environment (app/config_store.py).

Reading is open to the admin roles of each group (app/config_registry.GROUPS); secrets are only "set / not set".
Every change needs a fresh second factor and is audited (config.changed) with the old and new value, for secrets
only that they changed.
"""

import html

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import config_store, db, mail, settings_store
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/config", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("")
async def overview(admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        return await config_store.describe(conn, admin.admin_roles)


class GroupIn(BaseModel):
    values: dict[str, object] = Field(default_factory=dict)          # key → value, or null: back to the environment
    secrets: dict[str, str | None] = Field(default_factory=dict)     # key → new value, or null/"": remove


@router.put("/{group}")
async def save(group: str, body: GroupIn, admin: Principal = Depends(require_admin_stepup)):
    if len(body.values) + len(body.secrets) > 60:
        raise HTTPException(422, "For mange felter på én gang")
    try:
        async with db.connection() as conn:
            result = await config_store.save(conn, group, body.values, body.secrets, roles=admin.admin_roles,
                                             actor_id=admin.user_id, meta=admin.meta)
    except config_store.Refused as e:
        raise HTTPException(e.status, e.detail)
    await config_store.refresh(force=True)
    return result


@router.post("/import")
async def import_env(admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        result = await config_store.import_env(conn, roles=admin.admin_roles, actor_id=admin.user_id, meta=admin.meta)
    await config_store.refresh(force=True)
    return result


@router.post("/test-mail")
async def test_mail(admin: Principal = Depends(require_admin_stepup)):
    """Sends a mail to the admin's own address with the configuration as it is now."""
    if not admin.email:
        raise HTTPException(422, "Din konto har ingen e-mailadresse")
    if missing := mail.missing_config():
        raise HTTPException(422, "E-mail mangler: " + ", ".join(missing))
    async with db.connection() as conn:
        app_name = (await settings_store.get(conn, "app_name")) or "Gær"
    subject = f"Testmail fra {app_name}"
    text = (f"Denne mail er sendt fra /admin/config i {app_name} for at prøve opsætningen af e-mail.\n\n"
            "Den er kun sendt til dig.")
    try:
        await mail.send_mail(admin.email, subject, text, f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>", app_name)
    except mail.MailError as e:
        raise HTTPException(502, f"Udbyderen afviste mailen: {e}")
    return {"sent_to": admin.email}

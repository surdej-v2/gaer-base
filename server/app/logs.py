"""Auth log and audit log. Both are append-only: the app user may only INSERT.

Write to them inside the same transaction as the change they describe, so a change
is never committed without its log line (see the gaer-audit skill).
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from psycopg.types.json import Jsonb

from app.security import mask_phone, phone_hash, short_device


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str = ""
    embedded: bool = False  # the app runs in an iframe (browser extension, Office add-in)
    impersonator_id: int | None = None  # the admin behind a request made as an impersonated test account


def _jsonable(value):
    if value is None:
        return None
    return json.loads(json.dumps(value, default=_default))


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (UUID, Decimal)):
        return str(o)
    if isinstance(o, (bytes, memoryview)):
        return "<binary>"
    raise TypeError(f"not JSON serializable: {type(o)}")


async def auth_event(conn, event: str, *, ok: bool, meta: RequestMeta, user_id: int | None = None,
                     phone: str | None = None, **detail) -> None:
    if meta.impersonator_id is not None:
        detail.setdefault("impersonator_id", meta.impersonator_id)
    await conn.execute(
        """insert into auth_log (event, result, user_id, phone_masked, phone_hash, ip, device, detail)
           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (event, "ok" if ok else "fail", user_id, mask_phone(phone), phone_hash(phone), meta.ip,
         short_device(meta.user_agent) if meta.user_agent else None, Jsonb(_jsonable(detail))),
    )


async def audit(conn, action: str, *, actor_id: int | None, meta: RequestMeta, table: str | None = None,
                row_id=None, before: dict | None = None, after: dict | None = None, **detail) -> None:
    # While impersonating, the admin is the actor; the test account is recorded in the detail.
    if meta.impersonator_id is not None and actor_id != meta.impersonator_id:
        detail.setdefault("as_user_id", actor_id)
        actor_id = meta.impersonator_id
    await conn.execute(
        """insert into audit_log (actor_user_id, action, table_name, row_id, before, after, ip, detail)
           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (actor_id, action, table, None if row_id is None else str(row_id),
         Jsonb(_jsonable(before)) if before is not None else None,
         Jsonb(_jsonable(after)) if after is not None else None,
         meta.ip, Jsonb(_jsonable(detail))),
    )


def changed(before: dict, after: dict) -> tuple[dict, dict]:
    """Narrows a before/after pair to the fields that actually changed."""
    keys = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}

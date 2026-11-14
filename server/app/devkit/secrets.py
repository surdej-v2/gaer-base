"""The one internal interface to secrets for Gær Developer (F-12, afsnit 0).

Every studio asks this module for the values a connection or a live source needs, and for their status. Today it is
backed by the existing secret manager (app/secret_store.py, /admin/secrets) through app/source_secrets.py: a
definition names a secret as "gruppe/navn[@miljø]" (or an environment variable DATA_SECRET_…), never a value, and the
connection may use the value only while it holds an approved grant "Brug i kilde". When the Secrets studio is built it
replaces the body of these functions; nothing else changes.
"""

from app import source_secrets
from app.logs import RequestMeta

REF_HELP = "Navngiv hemmeligheden som gruppe/navn fra Hemmeligheder (eller DATA_SECRET_… som miljøvariabel)"


def spec_for(key: str, refs: list[tuple[str, str]]) -> dict:
    """The minimal source description source_secrets understands: the key and [{ref, used_as}]."""
    return {"key": key, "secrets": [{"ref": r, "used_as": used_as} for r, used_as in refs if r]}


async def values(conn, *, key: str, source_id: int | None, refs: list[tuple[str, str]], tester: dict | None = None,
                 meta: RequestMeta | None = None) -> tuple[dict[str, str], list[dict]]:
    """Values by reference for one call, and a status per secret. `tester`: the user row of a developer running a
    test, whose own access may be used (audited on the secret)."""
    return await source_secrets.resolve(conn, spec_for(key, refs), source_id=source_id, tester=tester, meta=meta)


async def status(conn, *, key: str, source_id: int | None, refs: list[tuple[str, str]]) -> list[dict]:
    """Only the status (nothing is decrypted or audited): for lists."""
    _, st = await source_secrets.resolve(conn, spec_for(key, refs), source_id=source_id, values_wanted=False)
    return st


async def record_use(conn, source_id: int, key: str, refs: list[tuple[str, str]]) -> None:
    """Which secrets a saved connection names (shown on the secret's page as "Bruges af")."""
    await source_secrets.sync(conn, source_id, spec_for(key, refs))


def is_ref(value) -> bool:
    from app.data_sources import SECRET_NAME_RE, SECRET_REF_RE
    return isinstance(value, str) and bool(SECRET_REF_RE.match(value) or SECRET_NAME_RE.match(value))

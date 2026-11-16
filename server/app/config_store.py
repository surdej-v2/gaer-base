"""Konfiguration (/admin/config): settings stored in Postgres instead of the environment.

config_values holds plain values, config_secrets encrypted keys and tokens (app.secrets_box, purpose "config:<key>").
Each process keeps the result in app.config (apply_overrides) and reads the tables again at most every CACHE_SECONDS,
like the kill switches: the guard middleware and the job worker call refresh(), so a change reaches every container
within a couple of seconds, and the process that made it sees it at once.

The order is database → environment → the default in app/config.py, so a fork that sets MAIL_* in its environment
keeps working, and a new instance can send its first login code before anyone has opened /admin.

A secret given for an endpoint (AI_BASE_URL, OVH_ENDPOINT, LETTERMINT_API_URL) is only used against that host: the
stored one against the host it was saved for (bound_host), the environment's against the environment's endpoint. So
changing an endpoint in /admin can never send a stored key to a new server; the key must be entered again.
"""

import asyncio
import dataclasses
import logging
import os
import time

from psycopg.types.json import Jsonb

from app import config, db, secrets_box, webhooks
from app import config_registry as reg
from app.logs import RequestMeta, audit

log = logging.getLogger("gaer.config")

CACHE_SECONDS = 2.0
_loaded_at = 0.0
_lock = asyncio.Lock()


def _purpose(key: str) -> str:
    return f"config:{key}"


async def _rows(conn) -> tuple[dict, dict]:
    values = {r["key"]: r for r in await db.fetch_all(
        conn, "select key, value, updated_at, updated_by from config_values")}
    secrets = {r["key"]: r for r in await db.fetch_all(
        conn, "select key, value_enc, bound_host, set_at, set_by from config_secrets")}
    return values, secrets


def _plain(values: dict) -> dict:
    out = {}
    for key, row in values.items():
        k = reg.KEYS.get(key)
        if k is None or k.secret:
            continue
        try:
            out[key] = reg.coerce(key, row["value"])
        except reg.Invalid as e:  # a value that no longer passes (bounds tightened in code): the environment wins
            log.warning("config %s ignored: %s", key, e)
    return out


def _secret_state(key: str, row: dict | None, effective, decrypt: bool) -> tuple[str, str | None, str | None]:
    """(source, value, why it is not used). source: db · env · none."""
    k = reg.KEYS[key]
    env = config.env_settings
    if row is not None:
        if k.endpoint and row["bound_host"] != reg.host(getattr(effective, k.endpoint)):
            return "db", None, f"gemt til {row['bound_host'] or 'en anden adresse'}, ikke den nuværende"
        if not decrypt:
            return "db", "", None
        try:
            return "db", secrets_box.decrypt(row["value_enc"], _purpose(key)), None
        except Exception:  # noqa: BLE001 — a key removed from SECRET_BOX_KEYS: treat as not set
            log.warning("config secret %s cannot be decrypted with SECRET_BOX_KEYS", key)
            return "db", None, "kan ikke dekrypteres med SECRET_BOX_KEYS"
    if getattr(env, key):
        if k.endpoint and reg.host(getattr(effective, k.endpoint)) != reg.host(getattr(env, k.endpoint)):
            return "env", None, "miljøets værdi gælder kun for miljøets adresse"
        return "env", getattr(env, key), None
    return "none", None, None


def compute(values: dict, secrets: dict) -> dict:
    """The overrides for app.config: plain values, plus each secret that is stored or that must be blanked out."""
    plain = _plain(values)
    effective = dataclasses.replace(config.env_settings, **plain)
    out = dict(plain)
    for key in reg.secret_keys():
        source, value, _ = _secret_state(key, secrets.get(key), effective, decrypt=True)
        if source == "db" or (source == "env" and value is None):
            out[key] = value
    return out


def invalidate() -> None:
    global _loaded_at
    _loaded_at = 0.0


async def refresh(force: bool = False) -> None:
    global _loaded_at
    if not force and time.monotonic() - _loaded_at < CACHE_SECONDS:
        return
    async with _lock:
        if not force and time.monotonic() - _loaded_at < CACHE_SECONDS:
            return
        try:
            async with db.connection() as conn:
                values, secrets = await _rows(conn)
        except Exception:
            # Keep the last known configuration: a database hiccup must not turn into a 500 on every request.
            log.warning("configuration could not be read, keeping the cached values", exc_info=True)
            _loaded_at = time.monotonic()
            return
        if config.apply_overrides(compute(values, secrets)):
            log.info("configuration from /admin/config applied (%d values)", len(config.overrides()))
        _loaded_at = time.monotonic()


# ── /admin/config ──────────────────────────────────────────────────────────────────────────────────────────

def _env_is_set(key: str) -> bool:
    return os.environ.get(reg.env_name(key)) not in (None, "")


def _jsonable(value):
    return list(value) if isinstance(value, tuple) else value


async def describe(conn, roles) -> dict:
    values, secrets = await _rows(conn)
    plain = _plain(values)
    effective = dataclasses.replace(config.env_settings, **plain)
    groups = []
    for gkey, group in reg.GROUPS.items():
        if not reg.may_use(gkey, roles):
            continue
        items = []
        for key in reg.keys_in(gkey):
            k = reg.KEYS[key]
            item = {"key": key, "env": reg.env_name(key), "label": k.label, "help": k.help, "kind": k.kind,
                    "options": list(k.options), "min": k.min, "max": k.max, "max_len": k.max_len,
                    "allow_empty": k.allow_empty, "secret": k.secret, "endpoint": k.endpoint, "provider": k.provider,
                    "restart": k.restart,
                    "blocked": [o for o in k.not_in_production] if config.settings.is_production else []}
            if k.secret:
                row = secrets.get(key)
                source, value, why = _secret_state(key, row, effective, decrypt=False)
                item |= {"source": source, "set": source != "none" and why is None, "ignored": why,
                         "set_at": row["set_at"] if row else None}
            else:
                row = values.get(key)
                item |= {"value": _jsonable(getattr(effective, key)),
                         "source": "db" if key in plain else ("env" if _env_is_set(key) else "default"),
                         "fallback": _jsonable(getattr(config.env_settings, key)),
                         "updated_at": row["updated_at"] if row else None}
            items.append(item)
        groups.append({"key": gkey, "label": group.label, "description": group.description, "items": items})
    env_only = [{"env": reg.env_name(f), "category": cat, "reason": why, "set": _env_is_set(f)}
                for f, (cat, why) in reg.ENV_ONLY.items()]
    importable = sum(1 for key in reg.KEYS for g in [reg.KEYS[key].group]
                     if reg.may_use(g, roles) and _env_is_set(key) and key not in values and key not in secrets)
    return {"groups": groups, "env_only": env_only, "importable": importable,
            "production": config.settings.is_production}


class Refused(Exception):
    def __init__(self, detail: str, status: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status = status


async def save(conn, group: str, values: dict, secrets: dict, *, roles, actor_id: int | None,
               meta: RequestMeta) -> dict:
    """Saves one group: values {key: value | None (back to the environment)}, secrets {key: str | None (remove)}.
    Audited and announced (webhook config.changed) in the caller's transaction. Returns what changed."""
    if group not in reg.GROUPS:
        raise Refused("Ukendt afsnit", 404)
    if not reg.may_use(group, roles):
        raise Refused("Dine admin-roller giver ikke adgang til dette afsnit", 403)
    for key in [*values, *secrets]:
        k = reg.KEYS.get(key)
        if k is None or k.group != group:
            raise Refused(f"{key} hører ikke til afsnittet")
        if (key in values) == k.secret:
            raise Refused(f"{k.label}: {'en hemmelighed gemmes for sig' if k.secret else 'er ikke en hemmelighed'}")
    production = config.settings.is_production
    rows, stored = await _rows(conn)
    before_plain = _plain(rows)
    before_eff = dataclasses.replace(config.env_settings, **before_plain)
    clean: dict = {}
    for key, value in values.items():
        try:
            clean[key] = None if value is None else reg.coerce(key, value, production=production)
        except reg.Invalid as e:
            raise Refused(str(e))
    after_plain = {k: v for k, v in before_plain.items() if k not in clean}
    after_plain.update({k: v for k, v in clean.items() if v is not None})
    after_eff = dataclasses.replace(config.env_settings, **after_plain)

    # The environment's value must also pass in production: "console" there falls back to … the environment.
    for key, value in clean.items():
        if value is None and production and getattr(after_eff, key) in reg.KEYS[key].not_in_production:
            raise Refused(f"{reg.KEYS[key].label}: miljøets værdi kan ikke bruges i produktion")

    changed_before, changed_after = {}, {}
    for key, value in clean.items():
        old = getattr(before_eff, key)
        if value is None:
            await conn.execute("delete from config_values where key = %s", (key,))
        else:
            await conn.execute(
                """insert into config_values (key, value, updated_at, updated_by) values (%s, %s, now(), %s)
                   on conflict (key) do update set value = excluded.value, updated_at = now(),
                                                   updated_by = excluded.updated_by""",
                (key, Jsonb(reg.to_json(key, value)), actor_id))
        new = getattr(after_eff, key)
        if old != new or (value is None) != (key not in before_plain):
            changed_before[key], changed_after[key] = _jsonable(old), _jsonable(new)

    # A stored secret bound to an endpoint whose host changed is removed: it must be entered again for the new host.
    cleared = []
    for key in reg.keys_in(group):
        k = reg.KEYS[key]
        if k.secret and k.endpoint and key in stored and key not in secrets:
            if stored[key]["bound_host"] != reg.host(getattr(after_eff, k.endpoint)):
                await conn.execute("delete from config_secrets where key = %s", (key,))
                cleared.append(key)

    secrets_set, secrets_removed = [], []
    for key, value in secrets.items():
        k = reg.KEYS[key]
        if value is None or value == "":
            if key in stored:
                await conn.execute("delete from config_secrets where key = %s", (key,))
                secrets_removed.append(key)
            continue
        try:
            value = reg.coerce(key, value)
        except reg.Invalid as e:
            raise Refused(str(e))
        bound = reg.host(getattr(after_eff, k.endpoint)) if k.endpoint else None
        await conn.execute(
            """insert into config_secrets (key, value_enc, bound_host, set_at, set_by) values (%s, %s, %s, now(), %s)
               on conflict (key) do update set value_enc = excluded.value_enc, bound_host = excluded.bound_host,
                                               set_at = now(), set_by = excluded.set_by""",
            (key, secrets_box.encrypt(value, _purpose(key)), bound, actor_id))
        secrets_set.append(key)

    result = {"changed": sorted(changed_after), "secrets_set": secrets_set, "secrets_removed": secrets_removed,
              "secrets_cleared": cleared,
              "restart": sorted(k for k in changed_after if reg.KEYS[k].restart)}
    if changed_after or secrets_set or secrets_removed or cleared:
        # Secrets are never in the audit log: only their names.
        await audit(conn, "config.changed", actor_id=actor_id, meta=meta, table="config_values", row_id=group,
                    before=changed_before, after=changed_after,
                    **{k: v for k, v in result.items() if v and k != "changed"})
        await webhooks.emit(conn, "config.changed", {"group": group, "keys": sorted(
            {*changed_after, *secrets_set, *secrets_removed, *cleared})})
    return result


async def import_env(conn, *, roles, actor_id: int | None, meta: RequestMeta) -> dict:
    """Copies every value the environment sets (and the database does not) into the database, for the groups the
    admin may change. A value that would not pass the bounds here stays in the environment."""
    rows, stored = await _rows(conn)
    copied, skipped = [], []
    production = config.settings.is_production
    for key, k in reg.KEYS.items():
        if not reg.may_use(k.group, roles) or not _env_is_set(key) or key in rows or key in stored:
            continue
        value = getattr(config.env_settings, key)
        try:
            value = reg.coerce(key, _jsonable(value) if not isinstance(value, tuple) else ",".join(value),
                               production=production)
        except reg.Invalid as e:
            skipped.append({"key": key, "env": reg.env_name(key), "reason": str(e)})
            continue
        if k.secret:
            bound = reg.host(getattr(config.env_settings, k.endpoint)) if k.endpoint else None
            await conn.execute(
                "insert into config_secrets (key, value_enc, bound_host, set_by) values (%s, %s, %s, %s)",
                (key, secrets_box.encrypt(value, _purpose(key)), bound, actor_id))
        else:
            await conn.execute("insert into config_values (key, value, updated_by) values (%s, %s, %s)",
                               (key, Jsonb(reg.to_json(key, value)), actor_id))
        copied.append(key)
    if copied:
        await audit(conn, "config.imported", actor_id=actor_id, meta=meta, table="config_values",
                    keys=copied, skipped=[s["key"] for s in skipped])
        await webhooks.emit(conn, "config.changed", {"group": "import", "keys": copied})
    return {"copied": copied, "skipped": skipped}

"""Datalager: NoSQL collections and KV namespaces with private/shared access, versioning and RACI approval.

Rules (design: the canvas "NoSQL og KV-lager"; tables in migrations/0042_datastore.sql):

- A container is a collection (`nosql`: JSON documents, filter by containment) or a namespace (`kv`: key → JSON
  value, optional expiry). It is owned by a group (app/groups.py) with its own RACI — exactly one A, at least one R,
  A/R/C must be admins whose roles open /admin/data/store (`eligible`), I may be anyone — or by one user (personal,
  addressed as "~<slug>"; the owner is everything).
- Access (`level_of`): `manage` = A, the personal owner, or an admin with Fuld adgang (group containers only; a
  personal container is never opened by an admin) · `write` = R, and members of a
  group the container is shared with at level write · `read` = C, I, members of the owning group, members of groups it
  is shared with. Shares count only while the container is `shared`. A private item is seen only with write or more.
- Versioning: `none` (only the latest value), `revisions` (each write is a revision that can be restored) and `semver`:
  the items are the working copy; `propose_release` snapshots it as the next minor or major, the server decides whether
  the changes break readers (`analyse`: a removed key, a changed JSON type, a schema that removes or retypes a property
  or adds a required one) and then it must be a major. Approval follows the container's rule (minor_rule / major_rule)
  among the RACI, never by the proposer; when nobody else could approve, one other eligible admin may (`fallback`).
  One rejection closes it; unanswered it expires after RELEASE_DAYS. Personal containers release at once.
- Readers of a semver container get the current release, or pin a major ("kunder@3" → newest approved 3.x). When a
  new major is approved, older majors stay readable for sunset_days, then answer 410.
- Every change is audited in the same transaction (table "store_containers", row = the container). Values are never in
  the audit log; revisions hold the history. Webhooks carry ids only.
"""

import html
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import admin_roles, config, db, groups, jobs, mail, outbound_mail, settings_store, webhooks
from app.logs import RequestMeta, audit

SECTION_API = "/api/admin/store"
TABLE = "store_containers"
RACI = ("R", "A", "C", "I")
KINDS = {"nosql": "NoSQL-samling", "kv": "KV-navnerum"}
VERSIONING = {"none": "Ingen", "revisions": "Løbende revisioner", "semver": "Major/minor"}
MINOR_RULES = {"auto": "Automatisk, når en R udgiver", "one_r": "Én anden R eller A", "a": "A"}
MAJOR_RULES = {"a_and_all_c": "A og alle C", "a_and_one_c": "A og én C", "a_only": "Kun A"}
LEVELS = ("read", "write", "manage")
SHARE_LEVELS = {"read": "Læse", "write": "Skrive"}
RELEASE_DAYS = 14
MAX_ITEMS = 50_000
MAX_VALUE_BYTES = 256 * 1024
MAX_SCHEMA_BYTES = 64 * 1024
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

CONTAINER_COLUMNS = """c.id, c.kind, c.slug, c.name, c.description, c.group_id, g.name as group_name, c.owner_id,
                       c.visibility, c.versioning, c.minor_rule, c.major_rule, c.sunset_days, c.json_schema,
                       c.default_ttl_seconds, c.current_major, c.current_minor, c.current_release_id, c.created_by,
                       c.created_at, c.updated_at, c.archived_at"""
RELEASE_COLUMNS = """r.id, r.container_id, r.major, r.minor, r.kind, r.status, r.approval_rule, r.fallback, r.note,
                     r.breaking, r.changes, r.item_count, r.proposed_by, r.created_at, r.expires_at, r.decided_at,
                     r.sunset_at, r.items_purged_at"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def version_label(major: int | None, minor: int | None) -> str | None:
    return None if major is None else f"{major}.{minor}"


# ── People ─────────────────────────────────────────────────────────────────

def eligible(user: dict | None) -> bool:
    """May this user act in /admin/data/store (be A, R or C, approve a release)?"""
    if not user or user.get("role") != "admin" or user.get("blocked_at") or user.get("impersonation_only"):
        return False
    roles = list(user.get("admin_roles") or []) + ([admin_roles.FULL] if user.get("super_admin") else [])
    return admin_roles.allows(roles, "GET", SECTION_API)


def is_full(user: dict | None) -> bool:
    if not user or user.get("role") != "admin" or user.get("blocked_at") or user.get("impersonation_only"):
        return False
    return bool(user.get("super_admin")) or admin_roles.FULL in (user.get("admin_roles") or [])


async def users_by_id(conn, ids) -> dict[int, dict]:
    ids = sorted({i for i in ids if i is not None})
    if not ids:
        return {}
    rows = await db.fetch_all(conn, """select id, email, display_name, role, admin_roles, super_admin, blocked_at,
                                              impersonation_only from users where id = any(%s)""", (ids,))
    return {r["id"]: r for r in rows}


async def user_row(conn, user_id: int) -> dict:
    return (await users_by_id(conn, [user_id])).get(user_id) or {"id": user_id}


def person(u: dict | None) -> dict | None:
    if u is None:
        return None
    return {"id": u["id"], "name": u.get("display_name") or u.get("email") or f"#{u['id']}", "email": u.get("email"),
            "eligible": eligible(u)}


async def eligible_admins(conn) -> list[dict]:
    rows = await db.fetch_all(conn, """select id, email, display_name, role, admin_roles, super_admin, blocked_at,
                                              impersonation_only from users
                                       where role = 'admin' and blocked_at is null and not impersonation_only
                                       order by lower(coalesce(display_name, email, '')), id""")
    return [r for r in rows if eligible(r)]


# ── Containers ─────────────────────────────────────────────────────────────

async def get(conn, container_id: int, *, lock: bool = False) -> dict:
    row = await db.fetch_one(conn, f"""select {CONTAINER_COLUMNS} from store_containers c
                                       left join groups g on g.id = c.group_id
                                       where c.id = %s {'for update of c' if lock else ''}""", (container_id,))
    if row is None:
        raise HTTPException(404, "Samlingen findes ikke")
    return row


async def resolve(conn, ref: str, user_id: int) -> dict:
    """A container by id, group slug, or "~slug" for the caller's own. Unknown and unreadable look the same (404)."""
    if ref.isdigit():
        row = await db.fetch_one(conn, "select id from store_containers where id = %s", (int(ref),))
    elif ref.startswith("~"):
        row = await db.fetch_one(conn, """select id from store_containers where owner_id = %s and slug = %s
                                          and archived_at is null""", (user_id, ref[1:]))
    else:
        row = await db.fetch_one(conn, """select id from store_containers where group_id is not null and slug = %s
                                          and archived_at is null""", (ref,))
    if row is None:
        raise HTTPException(404, "Samlingen findes ikke")
    return await get(conn, row["id"])


async def raci_map(conn, container_id: int) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in await db.fetch_all(conn, "select user_id, role from store_raci where container_id = %s", (container_id,)):
        out.setdefault(r["user_id"], []).append(r["role"])
    return {k: sorted(v, key=RACI.index) for k, v in out.items()}


async def roles_of(conn, container_id: int, user_id: int) -> set[str]:
    rows = await db.fetch_all(conn, "select role from store_raci where container_id = %s and user_id = %s",
                              (container_id, user_id))
    return {r["role"] for r in rows}


async def shares(conn, container_id: int) -> list[dict]:
    return await db.fetch_all(conn, """select s.group_id, g.name as group_name, s.level, s.added_at
                                       from store_shares s join groups g on g.id = s.group_id
                                       where s.container_id = %s order by lower(g.name)""", (container_id,))


async def level_of(conn, c: dict, user: dict) -> str | None:
    """None, 'read', 'write' or 'manage' — see the module docstring."""
    uid = user["id"]
    if c["owner_id"] is not None and c["owner_id"] == uid:
        return "manage"
    if c["group_id"] is not None and is_full(user):
        return "manage"  # break-glass for group containers; a personal one stays its owner's
    best: str | None = None

    def lift(level: str):
        nonlocal best
        if best is None or LEVELS.index(level) > LEVELS.index(best):
            best = level

    if c["group_id"] is not None:
        roles = await roles_of(conn, c["id"], uid)
        if "A" in roles:
            return "manage"
        if "R" in roles:
            lift("write")
        if roles & {"C", "I"} or await groups.is_member(conn, c["group_id"], uid):
            lift("read")
    if c["visibility"] == "shared":
        for s in await db.fetch_all(conn, """select s.level from store_shares s
                                             join group_members m on m.group_id = s.group_id and m.user_id = %s
                                             where s.container_id = %s""", (uid, c["id"])):
            lift(s["level"])
    return best


async def require(conn, c: dict, user_id: int, need: str) -> str:
    level = await level_of(conn, c, await user_row(conn, user_id))
    if level is None:
        raise HTTPException(404, "Samlingen findes ikke")
    if LEVELS.index(level) < LEVELS.index(need):
        raise HTTPException(403, {"write": "Du kan kun læse i denne samling",
                                  "manage": "Kun den ansvarlige (A) kan ændre det"}[need])
    return level


def check_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "Navnet i adressen må kun have små bogstaver, tal og bindestreg")
    return slug


async def validate_raci(conn, assignments: dict[int, list[str]]) -> dict[int, list[str]]:
    """Exactly one A, at least one R, A/R/C eligible, I alone (as in the secret manager)."""
    clean: dict[int, list[str]] = {}
    for uid, roles in assignments.items():
        roles = sorted({r for r in roles if r in RACI}, key=RACI.index)
        if not roles:
            continue
        if "I" in roles and len(roles) > 1:
            roles = [r for r in roles if r != "I"]
        clean[int(uid)] = roles
    users = await users_by_id(conn, clean)
    if any(u not in users for u in clean):
        raise HTTPException(400, "En person i RACI findes ikke")
    if len([u for u, rs in clean.items() if "A" in rs]) != 1:
        raise HTTPException(400, "Der skal være præcis én Accountable (A)")
    if not any("R" in rs for rs in clean.values()):
        raise HTTPException(400, "Der skal være mindst én Responsible (R)")
    not_ok = [person(users[u])["name"] for u, rs in clean.items() if set(rs) & {"A", "R", "C"} and not eligible(users[u])]
    if not_ok:
        raise HTTPException(400, "A, R og C skal være administratorer med adgang til Datalager: " + ", ".join(not_ok))
    return clean


async def write_raci(conn, container_id: int, assignments: dict[int, list[str]]) -> None:
    await conn.execute("delete from store_raci where container_id = %s", (container_id,))
    for uid, roles in assignments.items():
        for role in roles:
            await conn.execute("insert into store_raci (container_id, user_id, role) values (%s, %s, %s)",
                               (container_id, uid, role))


async def default_raci(conn, group_id: int, creator_id: int) -> dict[int, list[str]]:
    out = {creator_id: ["R", "A"]}
    for uid in await groups.member_ids(conn, group_id):
        out.setdefault(uid, ["I"])
    return out


async def write_shares(conn, container_id: int, items: list[dict], actor_id: int) -> None:
    seen = set()
    for s in items:
        if s["group_id"] in seen:
            raise HTTPException(400, "En gruppe står to gange")
        seen.add(s["group_id"])
        await groups.get(conn, s["group_id"])
    await conn.execute("delete from store_shares where container_id = %s", (container_id,))
    for s in items:
        await conn.execute("""insert into store_shares (container_id, group_id, level, added_by) values (%s, %s, %s, %s)""",
                           (container_id, s["group_id"], s["level"], actor_id))


async def create(conn, *, kind: str, slug: str, name: str, description: str, group_id: int | None, owner_id: int | None,
                 visibility: str, versioning: str, minor_rule: str, major_rule: str, sunset_days: int,
                 json_schema: dict | None, default_ttl_seconds: int | None, actor_id: int,
                 raci: dict[int, list[str]] | None, meta: RequestMeta) -> dict:
    slug = check_slug(slug)
    if kind == "kv" and json_schema is not None:
        raise HTTPException(400, "Kun en NoSQL-samling kan have et skema")
    if kind == "nosql" and default_ttl_seconds is not None:
        raise HTTPException(400, "Kun nøgler i et KV-navnerum kan udløbe")
    if json_schema is not None:
        check_schema(json_schema)
    if group_id is not None:
        await groups.get(conn, group_id)
        clash = await db.fetch_one(conn, """select 1 from store_containers where group_id is not null and slug = %s
                                            and archived_at is null""", (slug,))
    else:
        clash = await db.fetch_one(conn, """select 1 from store_containers where owner_id = %s and slug = %s
                                            and archived_at is null""", (owner_id, slug))
    if clash:
        raise HTTPException(409, "Der findes allerede en samling med det navn")
    row = await db.fetch_one(conn, """
        insert into store_containers (kind, slug, name, description, group_id, owner_id, visibility, versioning,
                                      minor_rule, major_rule, sunset_days, json_schema, default_ttl_seconds, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (kind, slug, name.strip(), description.strip(), group_id, owner_id, visibility, versioning, minor_rule,
         major_rule, sunset_days, Jsonb(json_schema) if json_schema is not None else None, default_ttl_seconds, actor_id))
    if group_id is not None:
        assignments = await validate_raci(conn, raci if raci is not None else await default_raci(conn, group_id, actor_id))
        await write_raci(conn, row["id"], assignments)
    c = await get(conn, row["id"])
    await audit(conn, "create", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"],
                after={k: c[k] for k in ("kind", "slug", "name", "group_id", "owner_id", "visibility", "versioning",
                                         "minor_rule", "major_rule")})
    await webhooks.emit(conn, "store.created", {"container_id": c["id"], "group_id": c["group_id"], "kind": kind})
    return c


SETTINGS = ("name", "description", "visibility", "versioning", "minor_rule", "major_rule", "sunset_days", "json_schema",
            "default_ttl_seconds")


async def update_settings(conn, c: dict, changes: dict, *, actor_id: int, meta: RequestMeta) -> dict:
    """Name, description, visibility, versioning and its rules, the schema (nosql), the default expiry (kv)."""
    if c["archived_at"] is not None:
        raise HTTPException(409, "Samlingen er arkiveret")
    changes = {k: v for k, v in changes.items() if k in SETTINGS}
    if "json_schema" in changes:
        if c["kind"] != "nosql" and changes["json_schema"] is not None:
            raise HTTPException(400, "Kun en NoSQL-samling kan have et skema")
        if changes["json_schema"] is not None:
            check_schema(changes["json_schema"])
    if changes.get("default_ttl_seconds") is not None and c["kind"] != "kv":
        raise HTTPException(400, "Kun nøgler i et KV-navnerum kan udløbe")
    if changes.get("versioning") and changes["versioning"] != "semver" and c["versioning"] == "semver":
        if await db.fetch_one(conn, "select 1 from store_releases where container_id = %s and status = 'pending'", (c["id"],)):
            raise HTTPException(409, "Afgør eller annullér den ventende version først")
    before = {k: c[k] for k in changes}
    if not changes:
        return c
    sets = ", ".join(f"{k} = %s" for k in changes)
    values = [Jsonb(v) if k == "json_schema" and v is not None else v for k, v in changes.items()]
    await conn.execute(f"update store_containers set {sets}, updated_at = now() where id = %s", (*values, c["id"]))
    after = await get(conn, c["id"])
    b, a = {k: before[k] for k in changes if before[k] != after[k]}, {k: after[k] for k in changes if before[k] != after[k]}
    if a:
        await audit(conn, "update", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"], before=b, after=a)
    return after


async def archive(conn, c: dict, *, actor_id: int, meta: RequestMeta, reason: str | None) -> None:
    if c["archived_at"] is not None:
        raise HTTPException(409, "Samlingen er allerede arkiveret")
    await conn.execute("""update store_releases set status = 'cancelled', decided_at = now()
                          where container_id = %s and status = 'pending'""", (c["id"],))
    await conn.execute("update store_containers set archived_at = now(), updated_at = now() where id = %s", (c["id"],))
    await audit(conn, "store.archived", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"], reason=reason)
    await webhooks.emit(conn, "store.archived", {"container_id": c["id"]})


# ── JSON Schema (a subset: type, properties, required, items, enum) ────────

SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def check_schema(schema, path: str = "") -> None:
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        raise HTTPException(400, "Skemaet er for stort")
    if not isinstance(schema, dict):
        raise HTTPException(400, f"Skemaet{path} skal være et objekt")
    t = schema.get("type")
    for x in ([t] if isinstance(t, str) else t or []):
        if x not in SCHEMA_TYPES:
            raise HTTPException(400, f"Ukendt type {x!r} i skemaet{path}")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        raise HTTPException(400, f"properties{path} skal være et objekt")
    for k, v in props.items():
        check_schema(v, f"{path}.{k}")
    req = schema.get("required", [])
    if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
        raise HTTPException(400, f"required{path} skal være en liste af feltnavne")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise HTTPException(400, f"enum{path} skal være en liste")


def json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def validate(value, schema: dict | None, path: str = "") -> list[str]:
    """Errors (Danish) for a value against the schema subset; empty when it fits."""
    if not schema:
        return []
    errors: list[str] = []
    t = schema.get("type")
    allowed = [t] if isinstance(t, str) else (t or [])
    actual = json_type(value)
    if allowed and not (actual in allowed or (actual == "integer" and "number" in allowed)):
        return [f"{path or 'dokumentet'} skal være {' eller '.join(allowed)}, ikke {actual}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path or 'værdien'} skal være en af {schema['enum']}")
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                errors.append(f"{path + '.' if path else ''}{r} mangler")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errors += validate(value[k], sub, f"{path + '.' if path else ''}{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value[:1000]):
            errors += validate(v, schema["items"], f"{path}[{i}]")
    return errors[:20]


def schema_breaks(old: dict | None, new: dict | None, path: str = "") -> list[str]:
    """Why readers of `old` would break on `new`: a property removed or retyped, a property made required."""
    if not old or not new:
        if not old and new and new.get("required"):
            return [f"Nyt skema kræver {', '.join(new['required'])}"]
        return []
    out = []

    def types(s):
        t = s.get("type")
        return set([t] if isinstance(t, str) else (t or []))

    if types(old) and types(new) and not types(new) >= types(old):
        out.append(f"{path or 'dokumentet'}: type {'/'.join(sorted(types(old)))} → {'/'.join(sorted(types(new)))}")
    old_p, new_p = old.get("properties", {}), new.get("properties", {})
    for k in old_p:
        if k not in new_p:
            out.append(f"Feltet {path + '.' if path else ''}{k} er fjernet")
        else:
            out += schema_breaks(old_p[k], new_p[k], f"{path + '.' if path else ''}{k}")
    for r in set(new.get("required", [])) - set(old.get("required", [])):
        out.append(f"Feltet {path + '.' if path else ''}{r} er nu påkrævet")
    if "items" in old and "items" in new:
        out += schema_breaks(old["items"], new["items"], f"{path}[]")
    return out


# ── Items ──────────────────────────────────────────────────────────────────

def check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise HTTPException(400, "En nøgle må kun have bogstaver, tal, punktum, kolon, bindestreg og understreg (højst 200)")
    return key


def check_value(value) -> None:
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_VALUE_BYTES:
        raise HTTPException(413, "Værdien er større end 256 KB")


def _versioned(c: dict) -> bool:
    return c["versioning"] in ("revisions", "semver")


def item_out(row: dict) -> dict:
    return {k: row[k] for k in ("key", "value", "private", "rev", "expires_at", "updated_at") if k in row}


async def write_item(conn, c: dict, key: str, value, *, private: bool | None, ttl_seconds: int | None, actor_id: int,
                     meta: RequestMeta) -> dict:
    """Creates or replaces one item in the working copy (a new revision when versioned)."""
    if c["archived_at"] is not None:
        raise HTTPException(409, "Samlingen er arkiveret")
    check_key(key)
    check_value(value)
    if c["kind"] == "nosql":
        if not isinstance(value, dict):
            raise HTTPException(400, "Et dokument skal være et JSON-objekt")
        errors = validate(value, c["json_schema"])
        if errors:
            raise HTTPException(422, "Dokumentet passer ikke til skemaet: " + "; ".join(errors))
    if ttl_seconds is not None and c["kind"] != "kv":
        raise HTTPException(400, "Kun nøgler i et KV-navnerum kan udløbe")
    ttl = ttl_seconds if ttl_seconds is not None else c["default_ttl_seconds"]
    expires = _now() + timedelta(seconds=ttl) if ttl else None
    old = await db.fetch_one(conn, "select * from store_items where container_id = %s and key = %s for update",
                             (c["id"], key))
    if old is None:
        n = await db.fetch_one(conn, "select count(*) as n from store_items where container_id = %s", (c["id"],))
        if n["n"] >= MAX_ITEMS:
            raise HTTPException(409, f"Samlingen har allerede {MAX_ITEMS} elementer")
        last = await db.fetch_one(conn, """select max(rev) as rev from store_revisions
                                           where container_id = %s and item_key = %s""", (c["id"], key))
        rev = (last["rev"] or 0) + 1
        row = await db.fetch_one(conn, """
            insert into store_items (container_id, key, value, private, rev, expires_at, created_by, updated_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s) returning *""",
            (c["id"], key, Jsonb(value), bool(private), rev, expires, actor_id, actor_id))
    else:
        rev = old["rev"] + 1
        row = await db.fetch_one(conn, """
            update store_items set value = %s, private = %s, rev = %s, expires_at = %s, updated_by = %s,
                   updated_at = now() where id = %s returning *""",
            (Jsonb(value), old["private"] if private is None else private, rev, expires, actor_id, old["id"]))
    if _versioned(c):
        await conn.execute("""update store_revisions set superseded_at = now() where container_id = %s
                              and item_key = %s and superseded_at is null""", (c["id"], key))
        await conn.execute("""insert into store_revisions (container_id, item_key, rev, value, private, changed_by)
                              values (%s, %s, %s, %s, %s, %s)""", (c["id"], key, rev, Jsonb(value), row["private"], actor_id))
    await conn.execute("update store_containers set updated_at = now() where id = %s", (c["id"],))
    await audit(conn, "store.item_written", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"], key=key, rev=rev,
                created=old is None, private=row["private"])
    return row


async def delete_item(conn, c: dict, key: str, *, actor_id: int, meta: RequestMeta) -> None:
    if c["archived_at"] is not None:
        raise HTTPException(409, "Samlingen er arkiveret")
    old = await db.fetch_one(conn, "delete from store_items where container_id = %s and key = %s returning *",
                             (c["id"], key))
    if old is None:
        raise HTTPException(404, "Nøglen findes ikke")
    if _versioned(c):
        await conn.execute("""update store_revisions set superseded_at = now() where container_id = %s
                              and item_key = %s and superseded_at is null""", (c["id"], key))
        await conn.execute("""insert into store_revisions (container_id, item_key, rev, value, private, changed_by)
                              values (%s, %s, %s, null, %s, %s)""", (c["id"], key, old["rev"] + 1, old["private"], actor_id))
    await conn.execute("update store_containers set updated_at = now() where id = %s", (c["id"],))
    await audit(conn, "store.item_deleted", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"], key=key)


async def restore_revision(conn, c: dict, key: str, rev: int, *, actor_id: int, meta: RequestMeta) -> dict:
    if not _versioned(c):
        raise HTTPException(400, "Samlingen gemmer ikke revisioner")
    r = await db.fetch_one(conn, """select value, private from store_revisions where container_id = %s and item_key = %s
                                    and rev = %s""", (c["id"], key, rev))
    if r is None:
        raise HTTPException(404, "Revisionen findes ikke")
    if r["value"] is None:
        await delete_item(conn, c, key, actor_id=actor_id, meta=meta)
        return {"key": key, "deleted": True}
    row = await write_item(conn, c, key, r["value"], private=r["private"], ttl_seconds=None, actor_id=actor_id, meta=meta)
    await audit(conn, "store.item_restored", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"], key=key,
                from_rev=rev, rev=row["rev"])
    return item_out(row)


def _filter_sql(filter_: dict | None, prefix: str | None, private_ok: bool, live: bool) -> tuple[str, list]:
    where, params = [], []
    if filter_:
        where.append("value @> %s")
        params.append(Jsonb(filter_))
    if prefix:
        where.append("key like %s")
        params.append(prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if not private_ok:
        where.append("not private")
    if live:
        where.append("(expires_at is null or expires_at > now())")
    return ("".join(f" and {w}" for w in where), params)


async def list_items(conn, c: dict, *, level: str, version: str | None, filter_: dict | None = None,
                     prefix: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """Items of the working copy (`version` 'draft', or always outside semver) or of a release."""
    private_ok = LEVELS.index(level) >= LEVELS.index("write")
    release = await release_for(conn, c, version, level)
    if release is None:
        extra, params = _filter_sql(filter_, prefix, private_ok, live=True)
        base = f"from store_items where container_id = %s{extra}"
        args = [c["id"], *params]
        total = (await db.fetch_one(conn, f"select count(*) as n {base}", args))["n"]
        rows = await db.fetch_all(conn, f"""select key, value, private, rev, expires_at, updated_at {base}
                                           order by key limit %s offset %s""", [*args, limit, offset])
        return {"version": "draft" if c["versioning"] == "semver" else None, "total": total,
                "items": [item_out(r) for r in rows]}
    extra, params = _filter_sql(filter_, prefix, private_ok, live=False)
    base = f"from store_release_items where release_id = %s{extra}"
    args = [release["id"], *params]
    total = (await db.fetch_one(conn, f"select count(*) as n {base}", args))["n"]
    rows = await db.fetch_all(conn, f"select key, value, private {base} order by key limit %s offset %s",
                              [*args, limit, offset])
    return {"version": version_label(release["major"], release["minor"]), "total": total, "items": [dict(r) for r in rows]}


async def get_item(conn, c: dict, key: str, *, level: str, version: str | None) -> dict:
    private_ok = LEVELS.index(level) >= LEVELS.index("write")
    release = await release_for(conn, c, version, level)
    if release is None:
        row = await db.fetch_one(conn, """select key, value, private, rev, expires_at, updated_at from store_items
                                          where container_id = %s and key = %s
                                          and (expires_at is null or expires_at > now())""", (c["id"], key))
        out = item_out(row) if row else None
        label = "draft" if c["versioning"] == "semver" else None
    else:
        row = await db.fetch_one(conn, "select key, value, private from store_release_items where release_id = %s and key = %s",
                                 (release["id"], key))
        out = dict(row) if row else None
        label = version_label(release["major"], release["minor"])
    if out is None or (out["private"] and not private_ok):
        raise HTTPException(404, "Nøglen findes ikke")
    return {**out, "version": label}


async def revisions(conn, c: dict, key: str, limit: int = 50) -> list[dict]:
    rows = await db.fetch_all(conn, """select rev, value, private, changed_by, changed_at from store_revisions
                                       where container_id = %s and item_key = %s order by rev desc limit %s""",
                              (c["id"], key, limit))
    users = await users_by_id(conn, [r["changed_by"] for r in rows])
    return [{**r, "deleted": r["value"] is None, "changed_by": person(users.get(r["changed_by"]))} for r in rows]


# ── Releases ───────────────────────────────────────────────────────────────

def parse_version(version: str | None) -> tuple[int, int | None] | None:
    if version in (None, "", "current", "draft"):
        return None
    m = re.fullmatch(r"(\d{1,6})(?:\.(\d{1,6}))?", version)
    if not m:
        raise HTTPException(400, "Version skal være draft, current, et major-nummer (3) eller major.minor (3.2)")
    return int(m.group(1)), (int(m.group(2)) if m.group(2) is not None else None)


async def release_for(conn, c: dict, version: str | None, level: str) -> dict | None:
    """The release a read should use, or None for the working copy.

    Outside semver: always the working copy. In semver: 'draft' (writers only) is the working copy; no version (or
    'current') is the current release for readers and the working copy for writers until a first release exists."""
    if c["versioning"] != "semver":
        if version not in (None, "", "current", "draft"):
            raise HTTPException(400, "Samlingen har ikke major/minor-versioner")
        return None
    writer = LEVELS.index(level) >= LEVELS.index("write")
    if version == "draft":
        if not writer:
            raise HTTPException(403, "Kun dem, der kan skrive, kan læse kladden")
        return None
    parsed = parse_version(version)
    if parsed is None:
        if c["current_release_id"] is None:
            if writer:
                return None
            raise HTTPException(404, "Der er ingen udgivet version endnu")
        return await db.fetch_one(conn, f"select {RELEASE_COLUMNS} from store_releases r where r.id = %s",
                                  (c["current_release_id"],))
    major, minor = parsed
    sql = f"""select {RELEASE_COLUMNS} from store_releases r where r.container_id = %s and r.status = 'approved'
              and r.major = %s {'and r.minor = %s' if minor is not None else ''} order by r.minor desc limit 1"""
    rel = await db.fetch_one(conn, sql, (c["id"], major, minor) if minor is not None else (c["id"], major))
    if rel is None:
        raise HTTPException(404, f"Version {version} findes ikke")
    if rel["items_purged_at"] is not None or (rel["sunset_at"] is not None and rel["sunset_at"] <= _now()):
        raise HTTPException(410, f"Version {major} er udfaset. Brug version {c['current_major']}.")
    return rel


async def _snapshot(conn, c: dict) -> dict[str, dict]:
    rows = await db.fetch_all(conn, """select key, value, private from store_items where container_id = %s
                                       and (expires_at is null or expires_at > now())""", (c["id"],))
    return {r["key"]: r for r in rows}


async def _current_items(conn, c: dict) -> dict[str, dict]:
    if c["current_release_id"] is None:
        return {}
    rows = await db.fetch_all(conn, "select key, value, private from store_release_items where release_id = %s",
                              (c["current_release_id"],))
    return {r["key"]: r for r in rows}


async def analyse(conn, c: dict) -> dict:
    """What the next release would contain against the current one, and why it would have to be a major."""
    new, old = await _snapshot(conn, c), await _current_items(conn, c)
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and (new[k]["value"] != old[k]["value"] or new[k]["private"] != old[k]["private"]))
    breaking: list[str] = []
    old_schema = None
    if c["current_release_id"] is not None:
        rel = await db.fetch_one(conn, "select json_schema from store_releases where id = %s", (c["current_release_id"],))
        old_schema = rel["json_schema"] if rel else None
    if c["kind"] == "kv":
        breaking += [f"Nøglen {k} er fjernet" for k in removed[:20]]
        for k in changed:
            a, b = json_type(old[k]["value"]), json_type(new[k]["value"])
            if a != b and not {a, b} <= {"integer", "number"}:
                breaking.append(f"{k}: type {a} → {b}")
            elif new[k]["private"] and not old[k]["private"]:
                breaking.append(f"{k} er gjort privat")
    elif c["current_release_id"] is not None:
        breaking += schema_breaks(old_schema, c["json_schema"])
    first = c["current_major"] is None
    return {
        "first": first,
        "next_minor": None if first else f"{c['current_major']}.{c['current_minor'] + 1}",
        "next_major": "1.0" if first else f"{c['current_major'] + 1}.0",
        "breaking": breaking[:50],
        "changes": {"added": added[:50], "changed": changed[:50], "removed": removed[:50],
                    "counts": {"added": len(added), "changed": len(changed), "removed": len(removed)},
                    "schema_changed": (old_schema or None) != (c["json_schema"] or None)},
        "item_count": len(new),
    }


def approvers_for(c: dict, kind: str, raci: dict[int, list[str]], proposer: int,
                  users: dict[int, dict]) -> tuple[str, dict[int, list[str]]]:
    """The rule that applies and who must answer (never the proposer, only eligible people)."""
    rule = c["minor_rule"] if kind == "minor" else c["major_rule"]
    want = {"auto": set(), "one_r": {"R", "A"}, "a": {"A"}, "a_and_all_c": {"A", "C"}, "a_and_one_c": {"A", "C"},
            "a_only": {"A"}}[rule]
    out = {}
    for uid, roles in raci.items():
        hit = [r for r in roles if r in want]
        if hit and uid != proposer and eligible(users.get(uid)):
            out[uid] = hit
    return rule, out


def outcome(rule: str, fallback: bool, rows: list[dict]) -> str:
    if any(r["decision"] == "rejected" for r in rows):
        return "rejected"
    ok = [r for r in rows if r["decision"] == "approved"]
    if fallback or rule == "one_r":
        return "approved" if ok else "pending"
    a_rows = [r for r in rows if "A" in r["roles"]]
    c_rows = [r for r in rows if "C" in r["roles"] and "A" not in r["roles"]]
    a_ok = all(r["decision"] == "approved" for r in a_rows)
    if rule in ("a", "a_only"):
        return "approved" if a_ok else "pending"
    if rule == "a_and_all_c":
        return "approved" if all(r["decision"] == "approved" for r in rows) else "pending"
    if rule == "a_and_one_c":
        return "approved" if a_ok and (not c_rows or any(r["decision"] == "approved" for r in c_rows)) else "pending"
    return "pending"


async def get_release(conn, release_id: int, *, lock: bool = False) -> dict:
    row = await db.fetch_one(conn, f"select {RELEASE_COLUMNS} from store_releases r where r.id = %s"
                                   + (" for update" if lock else ""), (release_id,))
    if row is None:
        raise HTTPException(404, "Versionen findes ikke")
    return row


async def release_approvals(conn, release_id: int) -> list[dict]:
    return await db.fetch_all(conn, """select approver_id, roles, decision, note, decided_at from store_release_approvals
                                       where release_id = %s order by approver_id""", (release_id,))


async def propose_release(conn, c: dict, *, kind: str, note: str, actor_id: int, meta: RequestMeta) -> dict:
    """Snapshots the working copy as the next version and asks the approvers (or approves it at once)."""
    if c["versioning"] != "semver":
        raise HTTPException(400, "Slå major/minor-versionering til først")
    if c["archived_at"] is not None:
        raise HTTPException(409, "Samlingen er arkiveret")
    if await db.fetch_one(conn, "select 1 from store_releases where container_id = %s and status = 'pending'", (c["id"],)):
        raise HTTPException(409, "Der venter allerede en version på godkendelse")
    a = await analyse(conn, c)
    if a["first"]:
        kind, major, minor = "major", 1, 0
    else:
        if kind == "auto":
            kind = "major" if a["breaking"] else "minor"
        if kind == "minor" and a["breaking"]:
            raise HTTPException(409, "Ændringerne bryder læsere og skal udgives som en major-version: " + "; ".join(a["breaking"][:5]))
        counts = a["changes"]["counts"]
        if kind == "minor" and not (sum(counts.values()) or a["changes"]["schema_changed"]):
            raise HTTPException(409, "Der er ingen ændringer siden den gældende version")
        major, minor = (c["current_major"] + 1, 0) if kind == "major" else (c["current_major"], c["current_minor"] + 1)
    personal = c["owner_id"] is not None
    raci = {} if personal else await raci_map(conn, c["id"])
    users = await users_by_id(conn, list(raci))
    rule, approvers = approvers_for(c, kind, raci, actor_id, users)
    if not personal and kind == "minor" and rule == "auto" and not ({"R", "A"} & set(raci.get(actor_id, []))):
        rule, approvers = "one_r", approvers_for({**c, "minor_rule": "one_r"}, kind, raci, actor_id, users)[1]
    auto = personal or rule == "auto"
    fallback = not auto and not approvers
    rel = await db.fetch_one(conn, f"""
        insert into store_releases (container_id, major, minor, kind, approval_rule, fallback, note, breaking, changes,
                                    json_schema, item_count, proposed_by, expires_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(days => %s))
        returning id""",
        (c["id"], major, minor, kind, "personal" if personal else rule, fallback, note.strip(), Jsonb(a["breaking"]),
         Jsonb(a["changes"]), Jsonb(c["json_schema"]) if c["json_schema"] is not None else None, a["item_count"],
         actor_id, RELEASE_DAYS))
    await conn.execute("""insert into store_release_items (release_id, key, value, private)
                          select %s, key, value, private from store_items where container_id = %s
                          and (expires_at is null or expires_at > now())""", (rel["id"], c["id"]))
    for uid, roles in approvers.items():
        await conn.execute("insert into store_release_approvals (release_id, approver_id, roles) values (%s, %s, %s)",
                           (rel["id"], uid, roles))
    await audit(conn, "store.release_proposed", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"],
                release_id=rel["id"], version=f"{major}.{minor}", kind=kind, rule=rule, fallback=fallback,
                approvers=sorted(approvers), breaking=len(a["breaking"]))
    await webhooks.emit(conn, "store.release.proposed", {"container_id": c["id"], "release_id": rel["id"],
                                                         "version": f"{major}.{minor}", "kind": kind})
    if auto:
        await _finish(conn, c, await get_release(conn, rel["id"], lock=True), "approved", actor_id=actor_id, meta=meta)
    else:
        for uid in approvers:
            await _mail(conn, "store.release.proposed", rel["id"], uid)
    return await get_release(conn, rel["id"])


async def can_decide(conn, rel: dict, user: dict) -> bool:
    if rel["status"] != "pending" or rel["proposed_by"] == user["id"]:
        return False
    if rel["fallback"]:
        return eligible(user)
    row = await db.fetch_one(conn, """select 1 from store_release_approvals where release_id = %s and approver_id = %s
                                      and decision = 'pending'""", (rel["id"], user["id"]))
    return row is not None and eligible(user)


async def decide(conn, release_id: int, *, user_id: int, approve: bool, note: str | None, meta: RequestMeta) -> dict:
    rel = await get_release(conn, release_id, lock=True)
    c = await get(conn, rel["container_id"], lock=True)
    user = await user_row(conn, user_id)
    if not await can_decide(conn, rel, user):
        if rel["status"] != "pending":
            raise HTTPException(409, "Versionen er allerede afgjort")
        raise HTTPException(403, "Du er ikke godkender af denne version")
    decision = "approved" if approve else "rejected"
    if rel["fallback"]:
        await conn.execute("""insert into store_release_approvals (release_id, approver_id, roles, decision, note, decided_at)
                              values (%s, %s, %s, %s, %s, now())""", (release_id, user_id, ["admin"], decision, note))
    else:
        await conn.execute("""update store_release_approvals set decision = %s, note = %s, decided_at = now()
                              where release_id = %s and approver_id = %s""", (decision, note, release_id, user_id))
    await audit(conn, "store.release_answered", actor_id=user_id, meta=meta, table=TABLE, row_id=c["id"],
                release_id=release_id, decision=decision, note=note)
    result = outcome(rel["approval_rule"], rel["fallback"], await release_approvals(conn, release_id))
    if result != "pending":
        await _finish(conn, c, rel, result, actor_id=user_id, meta=meta)
    return await get_release(conn, release_id)


async def cancel(conn, release_id: int, *, user_id: int, meta: RequestMeta) -> None:
    rel = await get_release(conn, release_id, lock=True)
    c = await get(conn, rel["container_id"])
    if rel["status"] != "pending":
        raise HTTPException(409, "Versionen er allerede afgjort")
    if rel["proposed_by"] != user_id:
        await require(conn, c, user_id, "manage")
    await _finish(conn, c, rel, "cancelled", actor_id=user_id, meta=meta)


async def _finish(conn, c: dict, rel: dict, status: str, *, actor_id: int | None, meta: RequestMeta) -> None:
    await conn.execute("update store_releases set status = %s, decided_at = now() where id = %s", (status, rel["id"]))
    version = f"{rel['major']}.{rel['minor']}"
    if status == "approved":
        if rel["kind"] == "major" and c["current_major"] is not None:
            await conn.execute("""update store_releases set sunset_at = now() + make_interval(days => %s)
                                  where container_id = %s and status = 'approved' and major < %s and sunset_at is null""",
                               (c["sunset_days"], c["id"], rel["major"]))
        await conn.execute("""update store_containers set current_major = %s, current_minor = %s, current_release_id = %s,
                              updated_at = now() where id = %s""", (rel["major"], rel["minor"], rel["id"], c["id"]))
    await audit(conn, f"store.release_{status}", actor_id=actor_id, meta=meta, table=TABLE, row_id=c["id"],
                release_id=rel["id"], version=version, kind=rel["kind"])
    await webhooks.emit(conn, "store.release.decided", {"container_id": c["id"], "release_id": rel["id"],
                                                        "version": version, "status": status})
    if status in ("approved", "rejected", "expired") and rel["proposed_by"] and rel["proposed_by"] != actor_id:
        await _mail(conn, "store.release.decided", rel["id"], rel["proposed_by"])
    if status == "approved" and c["group_id"] is not None:
        for uid, roles in (await raci_map(conn, c["id"])).items():
            if uid not in (actor_id, rel["proposed_by"]) and set(roles) & {"C", "I"}:
                await _mail(conn, "store.release.decided", rel["id"], uid)


async def waiting_for(conn, user: dict) -> list[dict]:
    """Pending releases this user can decide (their own row, or fallback releases when eligible)."""
    rows = await db.fetch_all(conn, f"""
        select {RELEASE_COLUMNS}, c.name as container_name, c.slug, c.kind as container_kind
        from store_releases r join store_containers c on c.id = r.container_id
        where r.status = 'pending' and r.proposed_by is distinct from %s
          and (exists (select 1 from store_release_approvals a where a.release_id = r.id and a.approver_id = %s
                       and a.decision = 'pending')
               or (r.fallback and %s))
        order by r.created_at""", (user["id"], user["id"], eligible(user)))
    return rows


# ── Notifications ──────────────────────────────────────────────────────────

async def _mail(conn, event: str, release_id: int, user_id: int) -> None:
    await jobs.enqueue(conn, "store.mail", {"event": event, "release_id": release_id, "user_id": user_id},
                       dedupe_key=f"store.mail:{event}:{release_id}:{user_id}")


STATUS_LABEL = {"approved": "godkendt", "rejected": "afvist", "expired": "udløbet", "cancelled": "annulleret",
                "pending": "venter"}


@jobs.handler("store.mail")
async def send_mail(payload: dict, job: dict) -> dict:
    async with db.connection() as conn:
        rel = await db.fetch_one(conn, f"""select {RELEASE_COLUMNS}, c.name as container_name from store_releases r
                                           join store_containers c on c.id = r.container_id where r.id = %s""",
                                 (payload["release_id"],))
        user = await db.fetch_one(conn, "select id, email from users where id = %s", (payload["user_id"],))
        if rel is None or user is None or not user["email"]:
            return {"skipped": "gone or no e-mail"}
        app_name = await settings_store.get(conn, "app_name")
    version = f"{rel['major']}.{rel['minor']}"
    name = rel["container_name"]
    link = f"{config.settings.origin.rstrip('/')}/admin/data/store/{rel['container_id']}?tab=versions"
    if payload["event"] == "store.release.proposed":
        subject = f"Godkend {name} {version}"
        lines = [f"Version {version} ({rel['kind']}) af {name} venter på dit svar.",
                 "Åbn versionen, se ændringerne og godkend eller afvis. Du skal bruge din anden faktor."]
    else:
        subject = f"{name} {version} er {STATUS_LABEL.get(rel['status'], rel['status'])}"
        lines = [f"Version {version} af {name} er {STATUS_LABEL.get(rel['status'], rel['status'])}."]
    text = "\n\n".join([*lines, f"Åbn: {link}", f"— {app_name}"])
    body = ("".join(f"<p>{html.escape(x)}</p>" for x in lines)
            + f'<p><a href="{html.escape(link)}" style="display:inline-block;background:#2563eb;color:#f8fafc;'
              f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600">Åbn i {html.escape(app_name)}</a></p>')
    message_id = await mail.send_mail(user["email"], subject, text, body, app_name)
    async with db.connection() as conn:
        await outbound_mail.record_sent(conn, message_id=message_id, recipient=user["email"], user_id=user["id"],
                                        purpose="store", subject=subject)
    return {"sent": payload["event"]}


# ── Maintenance ────────────────────────────────────────────────────────────

@jobs.handler("store.maintenance")
async def maintenance(payload: dict, job: dict) -> dict:
    counts = {"expired_releases": 0, "expired_keys": 0, "purged_snapshots": 0}
    meta = RequestMeta()
    async with db.connection() as conn:
        for r in await db.fetch_all(conn, f"""select {RELEASE_COLUMNS} from store_releases r
                                              where r.status = 'pending' and r.expires_at < now() for update"""):
            c = await get(conn, r["container_id"])
            await _finish(conn, c, r, "expired", actor_id=None, meta=meta)
            counts["expired_releases"] += 1
        counts["expired_keys"] = (await conn.execute(
            "delete from store_items where expires_at is not null and expires_at < now()")).rowcount
        purged = await db.fetch_all(conn, """
            with s as (select id from store_releases where sunset_at < now() and items_purged_at is null),
                 d as (delete from store_release_items i using s where i.release_id = s.id)
            update store_releases r set items_purged_at = now() from s where r.id = s.id returning r.id""")
        counts["purged_snapshots"] = len(purged)
        if any(counts.values()):
            await audit(conn, "store.maintenance", actor_id=None, meta=meta, **counts)
    return counts


# ── Users leaving ──────────────────────────────────────────────────────────

async def before_user_erased(conn, user_id: int, meta: RequestMeta) -> int:
    """Called by app.gdpr.erase_user: their personal containers cascade with them; group containers where they were A
    lose their accountable person (audited as store.orphaned, so an admin with Fuld adgang can appoint a new A)."""
    n = 0
    for r in await db.fetch_all(conn, "select container_id from store_raci where user_id = %s and role = 'A'", (user_id,)):
        await conn.execute("delete from store_raci where container_id = %s and user_id = %s", (r["container_id"], user_id))
        await audit(conn, "store.orphaned", actor_id=None, meta=meta, table=TABLE, row_id=r["container_id"])
        n += 1
    return n


async def export_for(conn, user_id: int) -> dict:
    """GDPR: their personal containers with every item, and their part in group containers (roles, approvals)."""
    own = await db.fetch_all(conn, """select id, kind, slug, name, description, visibility, versioning, created_at
                                      from store_containers where owner_id = %s order by id""", (user_id,))
    for c in own:
        c["items"] = await db.fetch_all(conn, """select key, value, private, rev, expires_at, updated_at from store_items
                                                 where container_id = %s order by key""", (c["id"],))
    return {
        "store_personal": own,
        "store_roles": await db.fetch_all(conn, """select c.name, c.slug, r.role, r.added_at from store_raci r
                                                   join store_containers c on c.id = r.container_id
                                                   where r.user_id = %s order by c.slug, r.role""", (user_id,)),
        "store_approvals": await db.fetch_all(conn, """select c.slug, r.major, r.minor, a.roles, a.decision, a.note,
                                                              a.decided_at from store_release_approvals a
                                                       join store_releases r on r.id = a.release_id
                                                       join store_containers c on c.id = r.container_id
                                                       where a.approver_id = %s order by r.id""", (user_id,)),
    }


def status_of(c: dict, *, has_a: bool, pending: int) -> str:
    if c["archived_at"] is not None:
        return "archived"
    if c["group_id"] is not None and not has_a:
        return "orphaned"
    if pending:
        return "pending"
    return "ok"

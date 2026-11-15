"""Tagging objects with nodes (migration 0023, Surdej's MetaObject/MetaAssignment).

An object is a URL (exact, a prefix pattern ending in "*", or a bare hostname), an entity ("clip:12", "runbook:ab12")
or a note. Assignments tag it with nodes in any dimension, the caller's private tree included.

Visibility follows the nodes (app/meta.py): the caller sees an object through its assignments to nodes they can see,
and only those assignments. Tagging and untagging is open to everyone who can see the node, as in Surdej; the title
and description are changed by whoever created the object or a meta admin. An object left without assignments is
deleted. Related nodes ("also tagged with") are derived from shared objects, never stored.
"""

import re
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from app import db, meta, webhooks
from app.logs import audit, changed
from app.sessions import Principal

KINDS = ("url", "entity", "note")
MAX_NODES = 50
ENTITY = re.compile(r"^[a-z][a-z0-9_-]{0,31}:\S{1,200}$")
HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9æøå]([a-z0-9æøå-]{0,61}[a-z0-9æøå])?(\.[a-z0-9æøå]([a-z0-9æøå-]{0,61}[a-z0-9æøå])?)+$")
TRACKING = {"fbclid", "gclid", "msclkid", "mc_eid", "igshid", "ref", "source", "dclid", "yclid"}

OBJ_COLUMNS = "o.id, o.kind, o.ref, o.title, o.description, o.created_by, o.created_at, o.updated_at"


# ── References ─────────────────────────────────────────────────────────────

def _tracking(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in TRACKING


def normalize_url(raw: str) -> str:
    """The same rules when a URL is stored and when a page is looked up, so they always meet:
    scheme and host lower case, no fragment, no tracking parameters, the rest sorted, no trailing slash.
    "https://github.com/org/*" is a prefix pattern; "status.example.com" a whole host."""
    value = raw.strip()
    if not value or len(value) > 2000 or any(c.isspace() for c in value):
        raise HTTPException(400, "Ugyldig URL")
    if "://" not in value:
        host = value.lower().rstrip("/")
        if HOST.match(host):
            return host
        raise HTTPException(400, "En URL skal starte med https:// eller være et værtsnavn som status.example.com")
    pattern = value.endswith("*")
    parts = urlsplit(value[:-1] if pattern else value)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "Kun http- og https-adresser kan tagges")
    host = parts.hostname.lower()
    port = parts.port
    netloc = host if port is None or (parts.scheme.lower(), port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    if pattern:
        return urlunsplit((parts.scheme.lower(), netloc, parts.path, "", "")) + "*"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _tracking(k)))
    path = parts.path.rstrip("/") if not query else parts.path
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def normalize_ref(kind: str, ref: str | None) -> str:
    if kind == "url":
        return normalize_url(ref or "")
    if kind == "entity":
        value = (ref or "").strip()
        if not ENTITY.match(value):
            raise HTTPException(400, "En entitet skrives som type:id, fx clip:12")
        return value
    if kind == "note":
        value = (ref or "").strip()
        return value if value.startswith("note:") and ENTITY.match(value) else f"note:{uuid.uuid4().hex[:12]}"
    raise HTTPException(400, "Ukendt type")


# ── Reading ────────────────────────────────────────────────────────────────

def visible_obj_sql(alias: str = "o") -> str:
    return f"""exists (select 1 from meta_assignments va join meta_nodes vn on vn.id = va.node_id
                       where va.object_id = {alias}.id and (vn.owner_id is null or vn.owner_id = %(me)s))"""


async def _nodes_of(conn, p: Principal, object_ids: list[int]) -> dict[int, list[dict]]:
    if not object_ids:
        return {}
    rows = await db.fetch_all(conn, f"""
        select a.object_id, {meta.NODE_COLUMNS}, d.slug as dimension_slug, d.label as dimension_label, d.color
        from meta_assignments a join meta_nodes n on n.id = a.node_id left join meta_dimensions d on d.id = n.dimension_id
        where a.object_id = any(%(ids)s) and {meta.visible_sql()}
        order by d.label nulls last, n.path""", {"ids": object_ids, "me": p.user_id})
    trails = await meta.trails(conn, [r["path"] for r in rows], p)
    out: dict[int, list[dict]] = {i: [] for i in object_ids}
    for r in rows:
        node = meta.out(r, p, trail=trails[r["path"]])
        node["dimension_label"] = r["dimension_label"] or "Mit træ"
        node["color"] = r["color"]
        out[r["object_id"]].append(node)
    return out


def _obj(row: dict, nodes: list[dict], p: Principal) -> dict:
    return {"id": row["id"], "kind": row["kind"], "ref": row["ref"], "title": row["title"], "description": row["description"],
            "mine": row["created_by"] == p.user_id, "created_at": row["created_at"], "updated_at": row["updated_at"],
            "nodes": nodes}


async def with_nodes(conn, p: Principal, rows: list[dict]) -> list[dict]:
    nodes = await _nodes_of(conn, p, [r["id"] for r in rows])
    return [_obj(r, nodes[r["id"]], p) for r in rows]


async def get(conn, p: Principal, object_id: int, *, for_update: bool = False) -> dict:
    row = await db.fetch_one(conn, f"""select {OBJ_COLUMNS} from meta_objects o where o.id = %(id)s and {visible_obj_sql()}
                                       {"for update" if for_update else ""}""", {"id": object_id, "me": p.user_id})
    if row is None:
        raise HTTPException(404, "Objektet findes ikke")
    return row


async def one(conn, p: Principal, object_id: int) -> dict:
    return (await with_nodes(conn, p, [await get(conn, p, object_id)]))[0]


async def by_ref(conn, p: Principal, kind: str, ref: str) -> dict:
    row = await db.fetch_one(conn, f"select {OBJ_COLUMNS} from meta_objects o where o.kind = %(kind)s and o.ref = %(ref)s and {visible_obj_sql()}",
                             {"kind": kind, "ref": normalize_ref(kind, ref), "me": p.user_id})
    if row is None:
        raise HTTPException(404, "Objektet er ikke tagget")
    return (await with_nodes(conn, p, [row]))[0]


async def at_node(conn, p: Principal, node_id: int, *, subtree: bool = False, kind: str | None = None,
                  q: str | None = None, limit: int = 100) -> list[dict]:
    node = await meta.node(conn, node_id, p)
    params = {"id": node_id, "prefix": meta.like_prefix(node["path"]), "me": p.user_id, "limit": max(1, min(limit, 500))}
    where = ["exists (select 1 from meta_assignments a join meta_nodes n on n.id = a.node_id where a.object_id = o.id and "
             + ("(n.id = %(id)s or n.path like %(prefix)s)" if subtree else "n.id = %(id)s") + f" and {meta.visible_sql()})"]
    if kind:
        where.append("o.kind = %(kind)s")
        params["kind"] = kind
    if q:
        where.append("(o.title ilike %(q)s or o.ref ilike %(q)s)")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = await db.fetch_all(conn, f"select {OBJ_COLUMNS} from meta_objects o where {' and '.join(where)} order by lower(o.title), o.id limit %(limit)s",
                              params)
    return await with_nodes(conn, p, rows)


async def context(conn, p: Principal, node_id: int) -> dict:
    """Surdej's cross-reference query: the node with parents, siblings, children, its objects, and the nodes those
    objects are also tagged with (outside this node's own line), most shared first."""
    node = await meta.node(conn, node_id, p)
    params = {"id": node_id, "parent": node["parent_id"], "dim": node["dimension_id"], "owner": node["owner_id"], "me": p.user_id}
    kids = await db.fetch_all(conn, f"""select {meta.NODE_COLUMNS}, (select count(*) from meta_assignments a where a.node_id = n.id) as objects
        from meta_nodes n where n.parent_id = %(id)s and n.archived_at is null and {meta.visible_sql()} order by {meta.CHILD_ORDER}""", params)
    sibs = await db.fetch_all(conn, f"""select {meta.NODE_COLUMNS}, (select count(*) from meta_assignments a where a.node_id = n.id) as objects
        from meta_nodes n where n.parent_id is not distinct from %(parent)s and n.dimension_id is not distinct from %(dim)s
          and n.owner_id is not distinct from %(owner)s and n.id <> %(id)s and n.archived_at is null and {meta.visible_sql()}
        order by {meta.CHILD_ORDER} limit 200""", params)
    objects = await at_node(conn, p, node_id, limit=200)
    line = {node["path"], *meta.ancestor_paths(node["path"])}
    related: dict[int, dict] = {}
    for o in objects:
        for n in o["nodes"]:
            if n["path"] in line or n["path"].startswith(node["path"] + "/"):
                continue
            r = related.setdefault(n["id"], {"node": n, "objects": []})
            r["objects"].append({"id": o["id"], "title": o["title"]})
    trail = (await meta.trails(conn, [node["path"]], p))[node["path"]]
    dims = await meta.dims_by_id(conn)

    def small(r):
        return meta.out(r, p, dims=dims) | {"objects": r["objects"]}

    return {
        "node": meta.out(node, p, trail=trail),
        "parents": trail,
        "siblings": [small(r) for r in sibs],
        "children": [small(r) for r in kids],
        "objects": objects,
        "related": sorted(related.values(), key=lambda r: (-len(r["objects"]), r["node"]["path"])),
    }


async def resolve_url(conn, p: Principal, url: str, *, limit: int = 3) -> dict:
    """A page in the browser → the tagged objects that match it: exact, then the longest prefix pattern, then the host."""
    norm = normalize_url(url)
    host = urlsplit(norm).hostname if "://" in norm else norm
    rows = await db.fetch_all(conn, f"""
        select {OBJ_COLUMNS},
               case when o.ref = %(norm)s then 0 when right(o.ref, 1) = '*' then 1 else 2 end as rank
        from meta_objects o
        where o.kind = 'url' and (o.ref = %(norm)s or o.ref = %(host)s
              or (right(o.ref, 1) = '*' and strpos(%(norm)s || '/', left(o.ref, length(o.ref) - 1)) = 1))
          and {visible_obj_sql()}
        order by rank, length(o.ref) desc limit %(limit)s""", {"norm": norm, "host": host, "me": p.user_id, "limit": limit})
    matches = await with_nodes(conn, p, rows)
    for m, r in zip(matches, rows):
        m["match"] = ("exact", "pattern", "host")[r["rank"]]
    return {"url": norm, "matches": matches}


# ── Changes ────────────────────────────────────────────────────────────────

async def _tag_nodes(conn, p: Principal, node_ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(node_ids))
    if len(ids) > MAX_NODES:
        raise HTTPException(400, f"Højst {MAX_NODES} noder pr. objekt")
    nodes = [await meta.node(conn, i, p) for i in ids]
    if any(n["archived_at"] is not None for n in nodes):
        raise HTTPException(400, "En arkiveret node kan ikke bruges som tag")
    return nodes


async def _apply(conn, p: Principal, object_id: int, add: list[dict], remove: list[int]) -> None:
    for n in add:
        await conn.execute("""insert into meta_assignments (object_id, node_id, created_by) values (%s, %s, %s)
                              on conflict do nothing""", (object_id, n["id"], p.user_id))
    shared_rm = []
    if remove:
        shared_rm = [r["id"] for r in await db.fetch_all(conn, "select id from meta_nodes where id = any(%s) and owner_id is null",
                                                           (remove,))]
        await conn.execute("delete from meta_assignments where object_id = %s and node_id = any(%s)", (object_id, remove))
    if add or remove:
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id,
                    tagged=[n["id"] for n in add], untagged=remove)
        shared_add = [n["id"] for n in add if n["owner_id"] is None]
        if shared_add:
            await webhooks.emit(conn, "meta.object.tagged", {"object_id": object_id, "node_ids": shared_add})
        if shared_rm:
            await webhooks.emit(conn, "meta.object.untagged", {"object_id": object_id, "node_ids": shared_rm})


async def tag(conn, p: Principal, *, kind: str, ref: str | None, title: str, description: str,
              node_ids: list[int]) -> dict:
    """Create the object (or find it by kind and reference) and tag it with the nodes."""
    if kind not in KINDS:
        raise HTTPException(400, "Ukendt type")
    title = " ".join(title.split())
    if not title:
        raise HTTPException(400, "Skriv en titel")
    if not node_ids:
        raise HTTPException(400, "Vælg mindst én node")
    norm = normalize_ref(kind, ref)
    nodes = await _tag_nodes(conn, p, node_ids)
    row = await db.fetch_one(conn, """insert into meta_objects (kind, ref, title, description, created_by, updated_by)
        values (%s, %s, %s, %s, %s, %s) on conflict (kind, ref) do nothing returning id""",
        (kind, norm, title, description, p.user_id, p.user_id))
    created = row is not None
    if created:
        object_id = row["id"]
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id,
                    after={"kind": kind, "ref": norm, "title": title})
    else:
        object_id = (await db.fetch_one(conn, "select id from meta_objects where kind = %s and ref = %s for update", (kind, norm)))["id"]
    have = {r["node_id"] for r in await db.fetch_all(conn, "select node_id from meta_assignments where object_id = %s", (object_id,))}
    await _apply(conn, p, object_id, [n for n in nodes if n["id"] not in have], [])
    return await one(conn, p, object_id) | {"created": created}


async def set_nodes(conn, p: Principal, object_id: int, node_ids: list[int]) -> dict:
    """The caller's view of the tags becomes node_ids. Assignments the caller cannot see (others' private trees) stay."""
    await get(conn, p, object_id, for_update=True)
    nodes = await _tag_nodes(conn, p, node_ids)
    seen = {r["node_id"] for r in await db.fetch_all(conn, f"""select a.node_id from meta_assignments a join meta_nodes n on n.id = a.node_id
        where a.object_id = %(id)s and {meta.visible_sql()}""", {"id": object_id, "me": p.user_id})}
    want = {n["id"] for n in nodes}
    await _apply(conn, p, object_id, [n for n in nodes if n["id"] not in seen], sorted(seen - want))
    return await _drop_if_empty(conn, p, object_id)


async def untag(conn, p: Principal, object_id: int, node_id: int) -> dict:
    await get(conn, p, object_id, for_update=True)
    await meta.node(conn, node_id, p)
    row = await db.fetch_one(conn, "select 1 as ok from meta_assignments where object_id = %s and node_id = %s", (object_id, node_id))
    if row is None:
        raise HTTPException(404, "Objektet er ikke tagget med den node")
    await _apply(conn, p, object_id, [], [node_id])
    return await _drop_if_empty(conn, p, object_id)


async def _drop_if_empty(conn, p: Principal, object_id: int) -> dict:
    left = (await db.fetch_one(conn, "select count(*) as n from meta_assignments where object_id = %s", (object_id,)))["n"]
    if left:
        try:
            return await one(conn, p, object_id)
        except HTTPException:
            return {"id": object_id, "deleted": False, "visible": False}
    row = await db.fetch_one(conn, "delete from meta_objects where id = %s returning kind, ref, title", (object_id,))
    await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id, before=row,
                reason="no_tags_left")
    return {"id": object_id, "deleted": True}


async def update(conn, p: Principal, object_id: int, fields: dict) -> dict:
    cur = await get(conn, p, object_id, for_update=True)
    if cur["created_by"] != p.user_id and not meta.is_meta_admin(p):
        raise HTTPException(403, "Kun den, der oprettede objektet, eller en admin kan rette det")
    new = {"title": cur["title"], "description": cur["description"]}
    if fields.get("title") is not None:
        new["title"] = " ".join(fields["title"].split()) or cur["title"]
    if fields.get("description") is not None:
        new["description"] = fields["description"]
    before, after = changed({"title": cur["title"], "description": cur["description"]}, new)
    if after:
        await conn.execute("update meta_objects set title = %s, description = %s, updated_by = %s, updated_at = now() where id = %s",
                           (new["title"], new["description"], p.user_id, object_id))
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id,
                    before=before, after=after)
    return await one(conn, p, object_id)


# ── For app/mapper.py ──────────────────────────────────────────────────────
# Mapper writes the same assignments and audit lines as tagging here; these names are its way in.

async def nodes_of(conn, p: Principal, object_ids: list[int]) -> dict[int, list[dict]]:
    """The visible nodes each object is tagged with, with trail, dimension label and colour."""
    return await _nodes_of(conn, p, object_ids)


async def apply_tags(conn, p: Principal, object_id: int, add: list[dict], remove: list[int]) -> None:
    """Adds and removes assignments with one audit line (and webhooks for shared nodes). add: node rows with
    id and owner_id."""
    await _apply(conn, p, object_id, add, remove)


async def drop_if_empty(conn, p: Principal, object_id: int) -> dict:
    """Deletes the object when its last assignment is gone (audited); else returns it as the caller sees it."""
    return await _drop_if_empty(conn, p, object_id)

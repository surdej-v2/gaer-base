"""Træstrukturer: dimensions and nodes with materialised paths (migration 0022, ported from Surdej's member-meta).

Paths
    /org/drift/support       shared: the dimension slug, then one slug per level (depth 1 = top level)
    /~/<user id>/projekter   private: the user's own tree ("Mit træ"); written ~/projekter by the API and UI

A slug is lower case letters (æøå too), digits and "-". Other tables store node ids; a path is what people see.

Who may create where (can_create):
    private tree      only its owner
    depth 1 (shared)  admins with the /meta section (admin role Indhold or Fuld) and a valid second factor
    locked dimension  the same admins, at any depth
    deeper            every logged-in user
Visibility: a node is shared (owner_id is null) or the caller's own. Archived nodes are left out of pickers but still
resolve by id, so stored values keep their label.

Ordering (migration 0041): children are listed by sort_order, then label (CHILD_ORDER). A node may also carry other
languages of its label (labels: {"en": …}), a sort_key, and child_sort (label / sort_key / manual) saying how a UI should
order its children; null keeps the default. Mapper (app/mapper.py) applies child_sort; the generic tree does not yet.
"""

import unicodedata

from fastapi import HTTPException
from psycopg import errors
from psycopg.types.json import Jsonb

from app import admin_roles, db, events, webhooks
from app.logs import audit, changed
from app.sessions import Principal, admin_mfa_ok

PRIVATE = "~"
MAX_DEPTH = 8
MAX_SLUG = 64
TREE_LIMIT = 2000
NOW = object()  # update_node: set a column to now()

NODE_COLUMNS = """n.id, n.dimension_id, n.owner_id, n.parent_id, n.path, n.slug, n.label, n.description, n.depth,
    n.sort_order, n.archived_at, n.created_at, n.updated_at, n.labels, n.sort_key, n.child_sort"""
CHILD_SORTS = ("label", "sort_key", "manual")
CHILD_ORDER = "n.sort_order, lower(n.label), n.id"


# ── Paths ──────────────────────────────────────────────────────────────────

def slugify(label: str) -> str:
    """'Kunde­service B2B' → 'kundeservice-b2b'. Keeps æøå and other letters; drops everything else."""
    text = unicodedata.normalize("NFC", label).strip().lower()
    out, dash = [], False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-")[:MAX_SLUG].strip("-")


def valid_slug(slug: str) -> bool:
    return 0 < len(slug) <= MAX_SLUG and slug[0] != "-" and slug[-1] != "-" and all(
        ch == "-" or (ch.isalnum() and ch == ch.lower()) for ch in slug)


def private_root(user_id: int) -> str:
    return f"/{PRIVATE}/{user_id}"


def display_path(path: str, me: int) -> str:
    root = private_root(me)
    return "~" + path[len(root):] if path == root or path.startswith(root + "/") else path


def like_prefix(path: str) -> str:
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def ancestor_paths(path: str) -> list[str]:
    """The paths of the node's ancestors that are nodes (not the dimension root), top first."""
    segs = path.split("/")[1:]
    skip = 2 if segs[0] == PRIVATE else 1       # /~/<id> and /<dimension> are roots, not nodes
    return ["/" + "/".join(segs[:i]) for i in range(skip + 1, len(segs))]


# ── Rights ─────────────────────────────────────────────────────────────────

def is_meta_admin(p: Principal) -> bool:
    return p.is_admin and admin_roles.allows(p.admin_roles, "POST", "/api/admin/meta") and admin_mfa_ok(p)


def create_rule(p: Principal, dim: dict | None, parent: dict | None, *, as_admin: bool | None = None) -> str | None:
    """None when p may create a child under parent (None = top level) in dim (None = private), else the reason."""
    admin = is_meta_admin(p) if as_admin is None else as_admin
    depth = (parent["depth"] if parent else 0) + 1
    if depth > MAX_DEPTH:
        return f"Træet må højst være {MAX_DEPTH} niveauer dybt"
    if dim is None:
        return None if parent is None or parent["owner_id"] == p.user_id else "Det er en andens private træ"
    if dim["locked"] and not admin:
        return "Dimensionen er låst, kun admin kan tilføje"
    if depth == 1 and not admin:
        return "Topniveauet oprettes af admin"
    return None


def visible_sql(alias: str = "n") -> str:
    return f"({alias}.owner_id is null or {alias}.owner_id = %(me)s)"


# ── Reading ────────────────────────────────────────────────────────────────

async def dimension(conn, slug: str) -> dict | None:
    """The dimension by slug; None for the private tree. 404 for an unknown slug."""
    if slug == PRIVATE:
        return None
    dim = await db.fetch_one(conn, "select * from meta_dimensions where slug = %s", (slug,))
    if dim is None:
        raise HTTPException(404, "Dimensionen findes ikke")
    return dim


async def node(conn, node_id: int, p: Principal, *, for_update: bool = False) -> dict:
    row = await db.fetch_one(conn, f"""select {NODE_COLUMNS}, d.slug as dimension_slug, d.locked
        from meta_nodes n left join meta_dimensions d on d.id = n.dimension_id
        where n.id = %(id)s and {visible_sql()} {"for update of n" if for_update else ""}""",
        {"id": node_id, "me": p.user_id})
    if row is None:
        raise HTTPException(404, "Noden findes ikke")
    return row


def out(row: dict, p: Principal, *, dims: dict[int, dict] | None = None, has_children: bool | None = None,
        trail: list[dict] | None = None, can_create: str | None | bool = False) -> dict:
    dim = (dims or {}).get(row["dimension_id"]) if row["dimension_id"] else None
    o = {
        "id": row["id"], "parent_id": row["parent_id"], "path": row["path"],
        "display_path": display_path(row["path"], p.user_id), "slug": row["slug"], "label": row["label"],
        "description": row["description"], "depth": row["depth"], "sort_order": row["sort_order"],
        "dimension": row.get("dimension_slug") or (dim["slug"] if dim else PRIVATE),
        "private": row["owner_id"] is not None, "archived": row["archived_at"] is not None,
        "labels": row.get("labels") or {}, "sort_key": row.get("sort_key"), "child_sort": row.get("child_sort"),
    }
    if "objects" in row:
        o["objects"] = row["objects"]
    if has_children is not None:
        o["has_children"] = has_children
    if trail is not None:
        o["trail"] = trail
    if can_create is not False:
        o["can_create"] = can_create is None
        o["create_denied"] = can_create
    return o


async def trails(conn, paths: list[str], p: Principal) -> dict[str, list[dict]]:
    """Ancestors (id, label) of each path, top first, for showing 'Drift › Support'."""
    wanted = sorted({a for path in paths for a in ancestor_paths(path)})
    found = {}
    if wanted:
        rows = await db.fetch_all(conn, f"select n.id, n.path, n.label from meta_nodes n where n.path = any(%(paths)s) and {visible_sql()}",
                                  {"paths": wanted, "me": p.user_id})
        found = {r["path"]: {"id": r["id"], "label": r["label"]} for r in rows}
    return {path: [found[a] for a in ancestor_paths(path) if a in found] for path in paths}


async def dims_by_id(conn) -> dict[int, dict]:
    return {d["id"]: d for d in await db.fetch_all(conn, "select * from meta_dimensions")}


async def tree(conn, p: Principal, *, dim_slug: str | None, root_id: int | None, depth: int,
               include_archived: bool = False) -> dict:
    """The nodes under a dimension or a start node, `depth` levels down, flat and in child order."""
    root = None
    if root_id is not None:
        root = await node(conn, root_id, p)
        dim_slug = root["dimension_slug"] or PRIVATE
    if not dim_slug:
        raise HTTPException(400, "Angiv dimension eller root")
    dim = await dimension(conn, dim_slug)
    base = root["path"] if root else ("/" + dim["slug"] if dim else private_root(p.user_id))
    base_depth = root["depth"] if root else 0
    params = {"prefix": like_prefix(base), "max": base_depth + max(1, min(depth, MAX_DEPTH)), "me": p.user_id,
              "limit": TREE_LIMIT + 1}
    archived = "" if include_archived else "and n.archived_at is null"
    rows = await db.fetch_all(conn, f"""
        select {NODE_COLUMNS},
               exists (select 1 from meta_nodes c where c.parent_id = n.id and {visible_sql("c")}
                       {"" if include_archived else "and c.archived_at is null"}) as has_children,
               (select count(*) from meta_assignments a where a.node_id = n.id) as objects
        from meta_nodes n
        where n.path like %(prefix)s and n.depth <= %(max)s and {visible_sql()} {archived}
        order by n.depth, {CHILD_ORDER} limit %(limit)s""", params)
    truncated = len(rows) > TREE_LIMIT
    rows = rows[:TREE_LIMIT]
    dims = {dim["id"]: dim} if dim else {}
    admin = is_meta_admin(p)
    nodes = [out(r, p, dims=dims, has_children=r["has_children"],
                 can_create=create_rule(p, dim, r, as_admin=admin)) for r in rows]
    trail = (await trails(conn, [root["path"]], p))[root["path"]] if root else []
    return {
        "dimension": dim_out(dim, p, admin=admin),
        "root": out(root, p, dims=dims, trail=trail, can_create=create_rule(p, dim, root, as_admin=admin)) if root else None,
        "nodes": nodes, "truncated": truncated,
    }


def dim_out(dim: dict | None, p: Principal, *, admin: bool, count: int | None = None) -> dict:
    if dim is None:
        o = {"id": None, "slug": PRIVATE, "label": "Mit træ", "description": "Dit private træ. Kun du kan se det.",
             "color": None, "locked": False, "private": True}
    else:
        o = {k: dim[k] for k in ("id", "slug", "label", "description", "color", "locked")} | {"private": False}
    rule = create_rule(p, dim, None, as_admin=admin)
    o["can_create"] = rule is None
    o["create_denied"] = rule
    if count is not None:
        o["nodes"] = count
    return o


async def search(conn, p: Principal, q: str, *, dim_slug: str | None, root_id: int | None, limit: int,
                 include_archived: bool = False) -> list[dict]:
    where, params = [visible_sql(), "(n.label ilike %(q)s or n.slug ilike %(q)s)"], {
        "me": p.user_id, "q": "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
        "limit": max(1, min(limit, 200))}
    if not include_archived:
        where.append("n.archived_at is null")
    if root_id is not None:
        root = await node(conn, root_id, p)
        where.append("n.path like %(prefix)s")
        params["prefix"] = like_prefix(root["path"])
    elif dim_slug:
        dim = await dimension(conn, dim_slug)
        where.append("n.path like %(prefix)s")
        params["prefix"] = like_prefix("/" + dim["slug"] if dim else private_root(p.user_id))
    rows = await db.fetch_all(conn, f"""select {NODE_COLUMNS}, d.slug as dimension_slug from meta_nodes n
        left join meta_dimensions d on d.id = n.dimension_id where {" and ".join(where)}
        order by (lower(n.label) = lower(%(exact)s)) desc, (n.label ilike %(starts)s) desc, n.depth, lower(n.label)
        limit %(limit)s""", params | {"exact": q, "starts": q.replace("%", "") + "%"})
    t = await trails(conn, [r["path"] for r in rows], p)
    return [out(r, p, trail=t[r["path"]]) for r in rows]


async def resolve(conn, p: Principal, ids: list[int]) -> list[dict]:
    """Stored values → nodes with trail, in the order asked. Unknown or invisible ids are left out."""
    if not ids:
        return []
    rows = await db.fetch_all(conn, f"""select {NODE_COLUMNS}, d.slug as dimension_slug from meta_nodes n
        left join meta_dimensions d on d.id = n.dimension_id where n.id = any(%(ids)s) and {visible_sql()}""",
                              {"ids": ids, "me": p.user_id})
    t = await trails(conn, [r["path"] for r in rows], p)
    by_id = {r["id"]: out(r, p, trail=t[r["path"]]) for r in rows}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


async def expand_subtree(conn, p: Principal, ids: list[int]) -> list[int]:
    """For filters with cascade="subtree": the nodes themselves plus every visible node below them."""
    roots = await db.fetch_all(conn, f"select n.id, n.path from meta_nodes n where n.id = any(%(ids)s) and {visible_sql()}",
                               {"ids": ids, "me": p.user_id})
    if not roots:
        return []
    rows = await db.fetch_all(conn, f"""select n.id from meta_nodes n
        where (n.id = any(%(ids)s) or n.path like any(%(prefixes)s)) and {visible_sql()}""",
        {"ids": [r["id"] for r in roots], "prefixes": [like_prefix(r["path"]) for r in roots], "me": p.user_id})
    return sorted(r["id"] for r in rows)


# ── Changes ────────────────────────────────────────────────────────────────

async def create_node(conn, p: Principal, *, dim_slug: str, parent_id: int | None, label: str,
                      slug: str | None = None, description: str = "", as_admin: bool | None = None) -> dict:
    label = " ".join(label.split())
    if not label:
        raise HTTPException(400, "Skriv et navn")
    slug = slug.strip().lower() if slug else slugify(label)
    if not valid_slug(slug):
        raise HTTPException(400, "Navnet skal indeholde mindst ét bogstav eller tal")
    parent = None
    if parent_id is not None:
        parent = await node(conn, parent_id, p, for_update=True)
        if (parent["dimension_slug"] or PRIVATE) != dim_slug:
            raise HTTPException(400, "Forælderen hører til en anden dimension")
        if parent["archived_at"] is not None:
            raise HTTPException(400, "Forælderen er arkiveret")
    dim = await dimension(conn, dim_slug)
    reason = create_rule(p, dim, parent, as_admin=as_admin)
    if reason:
        raise HTTPException(403, reason)
    base = parent["path"] if parent else ("/" + dim["slug"] if dim else private_root(p.user_id))
    path = f"{base}/{slug}"
    existing = await db.fetch_one(conn, "select id, label from meta_nodes where path = %s", (path,))
    if existing:
        raise HTTPException(409, f"„{existing['label']}“ findes allerede her")
    try:
        row = await db.fetch_one(conn, f"""insert into meta_nodes as n (dimension_id, owner_id, parent_id, path, slug, label,
                description, depth, sort_order, created_by, updated_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s,
                    coalesce((select max(sort_order) + 1 from meta_nodes where parent_id is not distinct from %s
                              and path like %s), 0), %s, %s)
            returning {NODE_COLUMNS}""",
            (dim["id"] if dim else None, None if dim else p.user_id, parent_id, path, slug, label, description,
             (parent["depth"] if parent else 0) + 1, parent_id, like_prefix(base), p.user_id, p.user_id))
    except errors.UniqueViolation:
        raise HTTPException(409, "Noden findes allerede") from None
    await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=row["id"],
                after={"path": path, "label": label, "private": dim is None})
    if dim is not None:
        await webhooks.emit(conn, "meta.node.created", {"node_id": row["id"], "dimension_id": dim["id"],
                                                        "parent_id": parent_id})
    return out(row, p, dims={dim["id"]: dim} if dim else None, has_children=False,
               trail=(await trails(conn, [path], p))[path],
               can_create=create_rule(p, dim, row, as_admin=as_admin))


async def update_node(conn, p: Principal, node_id: int, changes: dict, *, admin: bool) -> dict:
    """Label, description, sort order, archived, and moving (parent_id and/or slug), which rewrites the paths of the
    whole subtree in this transaction. Admins edit shared nodes; owners edit their private ones."""
    cur = await node(conn, node_id, p, for_update=True)
    private = cur["owner_id"] is not None
    if not (admin and not private) and not (private and cur["owner_id"] == p.user_id):
        raise HTTPException(403, "Du kan ikke rette denne node")
    sets, before, after = {}, {}, {}
    if "label" in changes and changes["label"] is not None:
        label = " ".join(changes["label"].split())
        if not label:
            raise HTTPException(400, "Skriv et navn")
        sets["label"] = label
    for key in ("description", "sort_order"):
        if key in changes and changes[key] is not None:
            sets[key] = changes[key]
    if "labels" in changes and changes["labels"] is not None:
        sets["labels"] = {k: " ".join(str(v).split()) for k, v in changes["labels"].items() if str(v).strip()}
    for key in ("sort_key", "child_sort"):        # sent as null or "": cleared
        if key in changes:
            value = (changes[key] or "").strip() or None
            if key == "child_sort" and value not in (None, *CHILD_SORTS):
                raise HTTPException(400, "Ukendt sortering")
            sets[key] = value
    if "archived" in changes and changes["archived"] is not None and changes["archived"] != (cur["archived_at"] is not None):
        sets["archived_at"] = NOW if changes["archived"] else None
    new_parent_id = changes["parent_id"] if "parent_id" in changes else cur["parent_id"]
    new_slug = changes.get("slug") or cur["slug"]
    if not valid_slug(new_slug):
        raise HTTPException(400, "Ugyldig slug")
    moved = new_parent_id != cur["parent_id"] or new_slug != cur["slug"]
    if moved:
        parent = None
        if new_parent_id is not None:
            parent = await node(conn, new_parent_id, p, for_update=True)
            if parent["dimension_id"] != cur["dimension_id"] or parent["owner_id"] != cur["owner_id"]:
                raise HTTPException(400, "En node kan kun flyttes inden for sin egen dimension")
            if parent["path"] == cur["path"] or parent["path"].startswith(cur["path"] + "/"):
                raise HTTPException(400, "En node kan ikke flyttes ind under sig selv")
        base = parent["path"] if parent else cur["path"].rsplit("/", cur["depth"])[0]
        new_path = f"{base}/{new_slug}"
        if await db.fetch_one(conn, "select 1 from meta_nodes where path = %s", (new_path,)):
            raise HTTPException(409, "Der findes allerede en node med det navn der")
        new_depth = (parent["depth"] if parent else 0) + 1
        deepest = await db.fetch_one(conn, "select max(depth) as d from meta_nodes where path like %s",
                                     (like_prefix(cur["path"]),))
        if (deepest["d"] or cur["depth"]) - cur["depth"] + new_depth > MAX_DEPTH:
            raise HTTPException(400, f"Træet må højst være {MAX_DEPTH} niveauer dybt")
        await conn.execute("""update meta_nodes set path = %s || substr(path, %s), depth = depth + %s, updated_at = now()
                              where path like %s""",
                           (new_path, len(cur["path"]) + 1, new_depth - cur["depth"], like_prefix(cur["path"])))
        sets |= {"parent_id": new_parent_id, "slug": new_slug, "path": new_path, "depth": new_depth}
    if sets:
        cols = ", ".join(f"{k} = now()" if v is NOW else f"{k} = %({k})s" for k, v in sets.items())
        await conn.execute(f"update meta_nodes set {cols}, updated_by = %(by)s, updated_at = now() where id = %(id)s",
                           {k: Jsonb(v) if k == "labels" else v for k, v in sets.items() if v is not NOW}
                           | {"by": p.user_id, "id": node_id})
        old = {k: cur[k] for k in ("label", "description", "sort_order", "parent_id", "slug", "path", "labels", "sort_key",
                                   "child_sort")}
        old["archived"] = cur["archived_at"] is not None
        new = old | {k: v for k, v in sets.items() if k in old}
        if "archived_at" in sets:
            new["archived"] = sets["archived_at"] is not None
        before, after = changed(old, new)
        if after:
            await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=node_id,
                        before=before, after=after)
            if not private:
                await webhooks.emit(conn, "meta.node.updated", {"node_id": node_id, "moved": moved})
    row = await node(conn, node_id, p)
    return out(row, p, trail=(await trails(conn, [row["path"]], p))[row["path"]])


async def delete_node(conn, p: Principal, node_id: int, *, admin: bool, force: bool = False) -> dict:
    cur = await node(conn, node_id, p, for_update=True)
    private = cur["owner_id"] is not None
    if not (admin and not private) and not (private and cur["owner_id"] == p.user_id):
        raise HTTPException(403, "Du kan ikke slette denne node")
    below = (await db.fetch_one(conn, "select count(*) as n from meta_nodes where path like %s",
                                (like_prefix(cur["path"]),)))["n"]
    tags = (await db.fetch_one(conn, """select count(*) as n from meta_assignments a join meta_nodes n on n.id = a.node_id
                                       where n.id = %s or n.path like %s""", (node_id, like_prefix(cur["path"]))))["n"]
    if (below or tags) and not force:
        what = " og ".join(x for x in (f"{below} noder under sig" if below else "", f"{tags} tags på objekter" if tags else "") if x)
        raise HTTPException(409, f"Noden har {what}. Arkivér den, eller slet med det hele.")
    await conn.execute("delete from meta_nodes where id = %s", (node_id,))
    orphans = await delete_orphan_objects(conn) if tags else []
    await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=node_id,
                before={"path": cur["path"], "label": cur["label"]}, descendants=below, tags=tags,
                objects_removed=orphans)
    if not private:
        await webhooks.emit(conn, "meta.node.deleted", {"node_id": node_id, "descendants": below})
    return {"ok": True, "deleted": below + 1}


async def delete_orphan_objects(conn) -> list[int]:
    """Objects whose last tag went with a deleted node (or an erased user's private tree) go too. Returns their ids."""
    rows = await db.fetch_all(conn, """delete from meta_objects o
        where not exists (select 1 from meta_assignments a where a.object_id = o.id) returning id""")
    return [r["id"] for r in rows]


async def count_event(kind: str, node_id: int) -> None:
    await events.emit(kind, ref=str(node_id))

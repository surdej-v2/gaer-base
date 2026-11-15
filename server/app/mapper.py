"""Mapper (specs/mapper/): the user's own folders with shortcuts, in the extension's side panel (/extension/mapper).

Nothing new underneath, only Træstrukturer (app/meta.py, app/meta_objects.py):

    ~/mapper                 the root, a private node made the first time it is needed (label "Mapper")
    ~/mapper/arbejde/drift   a folder = a private node under the root (depth 2–8)
    a shortcut               a URL object (meta_objects, kind 'url') tagged with one or more folders; a shortcut tagged
                             with the root itself lies at the top level. Its other tags (shared dimensions, the rest
                             of the private tree) are the "Emner" listing in the UI.

On top of that, migration 0040 keeps what is the user's alone: their title for a URL someone else created first
(mapper_titles), pinned folders (mapper_pins) and saved views (mapper_views). Every change is audited in the same
transaction, through meta's own functions where they exist.

Folder properties (migration 0041): the name, an English label (meta labels.en), a sort key, how subfolders are ordered
(meta child_sort: label = default, sort_key, manual = sort_order), and in mapper_folder_settings how the folder's
shortcuts are sorted (manual uses meta_assignments.sort_order) and the layout it opens in. Copy and move: a shortcut is
moved by retagging it from one folder to another and copied by adding a tag; a folder is moved with meta's path rewrite
and copied by cloning its subtree with the tags, in one transaction.

Preview: frame_check() fetches only the response headers of a page (through the SSRF guard in app.webhooks, no
redirects to private addresses) and tells whether a browser would let the side panel show it in a frame.
"""

import asyncio
import ipaddress
import socket
import time
from collections import deque
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import config, db, integrations, meta, meta_objects, webhooks
from app.logs import audit, changed
from app.sessions import Principal

ROOT_SLUG = "mapper"
ROOT_LABEL = "Mapper"
IMPORT_LABEL = "Fra browseren"
MAX_FOLDERS = 2000
MAX_SHORTCUTS = 5000
MAX_IMPORT_FOLDERS = 1000
MAX_IMPORT_ITEMS = 5000
MAX_TITLE = 300
MAX_LABEL = 120


def root_path(user_id: int) -> str:
    return f"{meta.private_root(user_id)}/{ROOT_SLUG}"


def _small(row: dict, user_id: int) -> dict:
    """The fields Mapper needs of a node, from meta.node() or meta.create_node()."""
    return {"id": row["id"], "path": row["path"], "depth": row["depth"], "label": row["label"],
            "parent_id": row["parent_id"], "sort_order": row["sort_order"], "owner_id": user_id,
            "labels": row.get("labels") or {}, "sort_key": row.get("sort_key"), "child_sort": row.get("child_sort")}


SHORTCUT_SORTS = ("manual", "name", "site", "added")
LAYOUTS = ("list", "grid", "table")


# ── Root and folders ───────────────────────────────────────────────────────

async def root(conn, p: Principal, *, create: bool = False) -> dict | None:
    row = await db.fetch_one(conn, "select id from meta_nodes where path = %s and owner_id = %s",
                             (root_path(p.user_id), p.user_id))
    if row is None:
        if not create:
            return None
        made = await meta.create_node(conn, p, dim_slug=meta.PRIVATE, parent_id=None, label=ROOT_LABEL, slug=ROOT_SLUG,
                                      description="Dine mapper med genveje (Mapper i sidepanelet)")
        row = {"id": made["id"]}
    node = await meta.node(conn, row["id"], p)
    if node["archived_at"] is not None:
        raise HTTPException(409, "Din mappe „Mapper“ er arkiveret. Gendan den under Tags › Mit træ.")
    return _small(node, p.user_id)


async def folder(conn, p: Principal, node_id: int | None, base: dict, *, allow_root: bool = True) -> dict:
    """A folder of the caller's (None = the root). 404 for anything outside their ~/mapper."""
    if node_id is None or node_id == base["id"]:
        if not allow_root:
            raise HTTPException(400, "Roden kan ikke ændres")
        return base
    try:
        node = await meta.node(conn, node_id, p)
    except HTTPException:
        node = None
    if node is None or node["owner_id"] != p.user_id or not node["path"].startswith(base["path"] + "/") \
            or node["archived_at"] is not None:
        raise HTTPException(404, "Mappen findes ikke")
    return _small(node, p.user_id)


async def _children_slug_taken(conn, parent: dict, slug: str) -> dict | None:
    return await db.fetch_one(conn, "select id from meta_nodes where path = %s", (f"{parent['path']}/{slug}",))


async def create_folder(conn, p: Principal, parent_id: int | None, label: str) -> dict:
    base = await root(conn, p, create=True)
    parent = await folder(conn, p, parent_id, base)
    made = await meta.create_node(conn, p, dim_slug=meta.PRIVATE, parent_id=parent["id"], label=label)
    return folder_out(made, base, pinned=False)


async def _settings(conn, node_id: int) -> dict:
    row = await db.fetch_one(conn, "select shortcut_sort, layout from mapper_folder_settings where node_id = %s", (node_id,))
    return row or {"shortcut_sort": None, "layout": None}


async def _set_settings(conn, p: Principal, node_id: int, changes: dict) -> None:
    """shortcut_sort and layout of one folder (or the root); null/"" = the default."""
    cur = await _settings(conn, node_id)
    new = dict(cur)
    for key, allowed in (("shortcut_sort", SHORTCUT_SORTS), ("layout", LAYOUTS)):
        if key in changes:
            value = changes[key] or None
            if value not in (None, *allowed):
                raise HTTPException(400, "Ugyldig værdi")
            new[key] = value
    before, after = changed(cur, new)
    if not after:
        return
    await conn.execute("""insert into mapper_folder_settings (node_id, user_id, shortcut_sort, layout) values (%s, %s, %s, %s)
        on conflict (node_id) do update set shortcut_sort = excluded.shortcut_sort, layout = excluded.layout, updated_at = now()""",
                       (node_id, p.user_id, new["shortcut_sort"], new["layout"]))
    await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="mapper_folder_settings", row_id=node_id,
                before=before, after=after)


def _inside(node: dict, target: dict) -> bool:
    """True when target is node itself or lies below it."""
    return target["path"] == node["path"] or target["path"].startswith(node["path"] + "/")


async def update_folder(conn, p: Principal, node_id: int, changes: dict) -> dict:
    """Name, English label, sort key, subfolder order (child_sort), parent (move), and Mapper's own shortcut sort and
    layout. The root takes only the orderings and the layout."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    is_root = node_id == base["id"]
    if is_root and any(k in changes for k in ("label", "en_label", "sort_key", "parent_id")):
        raise HTTPException(400, "Roden kan ikke omdøbes eller flyttes")
    cur = await folder(conn, p, node_id, base)
    patch: dict = {}
    if "en_label" in changes:
        labels = dict(cur["labels"])
        if (changes["en_label"] or "").strip():
            labels["en"] = changes["en_label"]
        else:
            labels.pop("en", None)
        patch["labels"] = labels
    for key in ("sort_key", "child_sort"):
        if key in changes:
            patch[key] = changes[key]
    if changes.get("label"):
        patch["label"] = changes["label"]
        slug = meta.slugify(changes["label"])
        if meta.valid_slug(slug):
            patch["slug"] = slug  # the path follows the name, so a new folder may use the old one
    if "parent_id" in changes:
        target = await folder(conn, p, changes["parent_id"], base)
        if _inside(cur, target):
            raise HTTPException(400, "En mappe kan ikke flyttes ind i sig selv eller en af sine undermapper")
        patch["parent_id"] = target["id"]
    if "sort_order" in changes and changes["sort_order"] is not None:
        patch["sort_order"] = changes["sort_order"]
    if "parent_id" in patch and patch["parent_id"] == cur["parent_id"]:
        patch.pop("parent_id")
    if patch:
        await meta.update_node(conn, p, cur["id"], patch, admin=False)
    await _set_settings(conn, p, node_id, changes)
    updated = await meta.node(conn, node_id, p)
    pinned = await db.fetch_one(conn, "select 1 as x from mapper_pins where user_id = %s and node_id = %s", (p.user_id, node_id))
    return folder_out(updated, base, pinned=pinned is not None, settings=await _settings(conn, node_id))


async def order_children(conn, p: Principal, node_id: int, ids: list[int]) -> dict:
    """Manual order of a folder's subfolders (or the top level): sort_order = position, child_sort = manual."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    parent = await folder(conn, p, node_id, base)
    kids = await db.fetch_all(conn, """select id, sort_order from meta_nodes where parent_id = %s and owner_id = %s
                                       and archived_at is null for update""", (parent["id"], p.user_id))
    known = {k["id"]: k["sort_order"] for k in kids}
    if len(set(ids)) != len(ids) or not set(ids) <= set(known):
        raise HTTPException(400, "Rækkefølgen skal være mappens egne undermapper")
    order = list(ids) + sorted((i for i in known if i not in ids), key=lambda i: known[i])
    for pos, child in enumerate(order):
        if known[child] != pos:
            await conn.execute("update meta_nodes set sort_order = %s, updated_by = %s, updated_at = now() where id = %s",
                               (pos, p.user_id, child))
    if parent["child_sort"] != "manual":
        await meta.update_node(conn, p, parent["id"], {"child_sort": "manual"}, admin=False)
    await audit(conn, "mapper.reorder", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=parent["id"],
                kind="folders", order=order)
    return {"id": parent["id"], "order": order}


async def order_shortcuts(conn, p: Principal, node_id: int, ids: list[int]) -> dict:
    """Manual order of the shortcuts in a folder (meta_assignments.sort_order); the folder's shortcut sort becomes manual."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    node = await folder(conn, p, node_id, base)
    have = {r["object_id"] for r in await db.fetch_all(conn, "select object_id from meta_assignments where node_id = %s for update",
                                                       (node["id"],))}
    if len(set(ids)) != len(ids) or not set(ids) <= have:
        raise HTTPException(400, "Rækkefølgen skal være genveje i mappen")
    for pos, object_id in enumerate(ids):
        await conn.execute("update meta_assignments set sort_order = %s where node_id = %s and object_id = %s",
                           (pos, node["id"], object_id))
    await conn.execute("update meta_assignments set sort_order = null where node_id = %s and not (object_id = any(%s))",
                       (node["id"], ids))
    await _set_settings(conn, p, node["id"], {"shortcut_sort": "manual"})
    await audit(conn, "mapper.reorder", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=node["id"],
                kind="shortcuts", order=ids)
    return {"id": node["id"], "order": ids}


async def _free_slug(conn, parent: dict, slug: str) -> str:
    candidate, n = slug, 1
    while await _children_slug_taken(conn, parent, candidate):
        n += 1
        candidate = f"{slug[:meta.MAX_SLUG - 4]}-{n}"
    return candidate


async def copy_folder(conn, p: Principal, node_id: int, to_id: int | None) -> dict:
    """Clones the folder with its subfolders, properties and shortcuts (tags) under to_id (None = the top level), in
    this transaction. The copy is called "<name> (kopi)"."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    src = await folder(conn, p, node_id, base, allow_root=False)
    dst = await folder(conn, p, to_id, base)
    if _inside(src, dst):
        raise HTTPException(400, "En mappe kan ikke kopieres ind i sig selv eller en af sine undermapper")
    rows = await db.fetch_all(conn, f"""select {meta.NODE_COLUMNS} from meta_nodes n
        where n.owner_id = %(me)s and (n.id = %(id)s or n.path like %(prefix)s) and n.archived_at is null
        order by n.depth, {meta.CHILD_ORDER}""", {"me": p.user_id, "id": src["id"], "prefix": meta.like_prefix(src["path"])})
    if max(r["depth"] for r in rows) - src["depth"] + dst["depth"] + 1 > meta.MAX_DEPTH:
        raise HTTPException(400, f"Kopien ville blive dybere end {meta.MAX_DEPTH} niveauer")
    made: dict[int, dict] = {}
    for r in rows:
        if r["id"] != src["id"] and r["parent_id"] not in made:
            continue  # below an archived folder
        parent = dst if r["id"] == src["id"] else made[r["parent_id"]]
        label = f"{r['label']} (kopi)"[:MAX_LABEL] if r["id"] == src["id"] else r["label"]
        slug = await _free_slug(conn, parent, meta.slugify(label) or "mappe")
        node = await meta.create_node(conn, p, dim_slug=meta.PRIVATE, parent_id=parent["id"], label=label, slug=slug,
                                      description=r["description"])
        await conn.execute("""update meta_nodes set labels = %s, sort_key = %s, child_sort = %s, sort_order = %s where id = %s""",
                           (Jsonb(r["labels"] or {}), r["sort_key"], r["child_sort"],
                            node["sort_order"] if r["id"] == src["id"] else r["sort_order"], node["id"]))
        await conn.execute("""insert into mapper_folder_settings (node_id, user_id, shortcut_sort, layout)
            select %s, user_id, shortcut_sort, layout from mapper_folder_settings where node_id = %s""", (node["id"], r["id"]))
        made[r["id"]] = _small(node, p.user_id)
    tags = await db.fetch_all(conn, """select object_id, node_id, sort_order from meta_assignments
                                       where node_id = any(%s) order by object_id""", (list(made),))
    per_object: dict[int, list[int]] = {}
    for t in tags:
        new_id = made[t["node_id"]]["id"]
        await conn.execute("""insert into meta_assignments (object_id, node_id, created_by, sort_order) values (%s, %s, %s, %s)
                              on conflict do nothing""", (t["object_id"], new_id, p.user_id, t["sort_order"]))
        per_object.setdefault(t["object_id"], []).append(new_id)
    for object_id, nodes in per_object.items():
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id,
                    tagged=nodes, untagged=[], via="mapper.copy")
    top = made[src["id"]]
    await audit(conn, "mapper.copy", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=top["id"],
                source=src["id"], folders=len(made), shortcuts=len(tags))
    return {"id": top["id"], "label": top["label"], "folders": len(made), "shortcuts": len(tags)}


async def delete_folder(conn, p: Principal, node_id: int, *, force: bool) -> dict:
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    await folder(conn, p, node_id, base, allow_root=False)
    return await meta.delete_node(conn, p, node_id, admin=False, force=force)


async def set_pin(conn, p: Principal, node_id: int, pinned: bool) -> dict:
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Mappen findes ikke")
    await folder(conn, p, node_id, base, allow_root=False)
    if pinned:
        row = await db.fetch_one(conn, """insert into mapper_pins (user_id, node_id) values (%s, %s)
                                         on conflict do nothing returning node_id""", (p.user_id, node_id))
        if row:
            await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="mapper_pins", row_id=node_id)
    else:
        row = await db.fetch_one(conn, "delete from mapper_pins where user_id = %s and node_id = %s returning node_id",
                                 (p.user_id, node_id))
        if row:
            await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="mapper_pins", row_id=node_id)
    return {"id": node_id, "pinned": pinned}


def folder_out(row: dict, base: dict, *, pinned: bool, settings: dict | None = None) -> dict:
    settings = settings or {}
    return {"id": row["id"], "parent_id": None if row["parent_id"] == base["id"] else row["parent_id"],
            "label": row["label"], "depth": row["depth"] - base["depth"], "sort_order": row["sort_order"], "pinned": pinned,
            "en_label": (row.get("labels") or {}).get("en"), "sort_key": row.get("sort_key"), "child_sort": row.get("child_sort"),
            "shortcut_sort": settings.get("shortcut_sort"), "layout": settings.get("layout")}


# ── Reading everything ─────────────────────────────────────────────────────

def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "") if "://" in url else url


async def _shortcut_rows(conn, p: Principal, base: dict, object_ids: list[int] | None = None, limit: int = MAX_SHORTCUTS + 1):
    only = "and o.id = any(%(ids)s)" if object_ids is not None else ""
    return await db.fetch_all(conn, f"""
        select o.id, o.ref, coalesce(t.title, o.title) as title, o.created_by, o.updated_at,
               array_agg(a.node_id order by a.created_at, a.node_id) as folders,
               array_agg(a.sort_order order by a.created_at, a.node_id) as positions,
               array_agg(a.created_at order by a.created_at, a.node_id) as placed, min(a.created_at) as added_at
        from meta_assignments a
        join meta_nodes n on n.id = a.node_id
        join meta_objects o on o.id = a.object_id
        left join mapper_titles t on t.object_id = o.id and t.user_id = %(me)s
        where n.owner_id = %(me)s and (n.id = %(root)s or n.path like %(prefix)s) and n.archived_at is null
          and o.kind = 'url' {only}
        group by o.id, t.title
        order by min(a.created_at), o.id limit %(limit)s""",
        {"me": p.user_id, "root": base["id"], "prefix": meta.like_prefix(base["path"]), "ids": object_ids or [], "limit": limit})


async def _shortcuts_out(conn, p: Principal, base: dict, rows: list[dict]) -> list[dict]:
    tags = await meta_objects.nodes_of(conn, p, [r["id"] for r in rows])
    inside = base["path"] + "/"
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "url": r["ref"], "title": r["title"], "host": host_of(r["ref"]),
            "folders": [None if f == base["id"] else f for f in r["folders"]],
            "positions": r["positions"],  # the manual order in each of those folders (null: not ordered yet)
            "placed": r["placed"],        # when it was put in each of those folders
            "added_at": r["added_at"], "mine": r["created_by"] == p.user_id,
            # Emner: every other tag the user can see (shared dimensions and the rest of their private tree).
            "tags": [{k: n[k] for k in ("id", "label", "dimension", "dimension_label", "color", "trail", "private", "parent_id")}
                     for n in tags[r["id"]] if n["path"] != base["path"] and not n["path"].startswith(inside)],
        })
    return out


async def state(conn, p: Principal) -> dict:
    """Everything the Mapper tab shows: the folders, the pins and every shortcut with its folders and other tags."""
    base = await root(conn, p)
    if base is None:
        return {"root": None, "folders": [], "shortcuts": [], "truncated": False}
    rows = await db.fetch_all(conn, f"""select {meta.NODE_COLUMNS}, (pin.node_id is not null) as pinned,
               fs.shortcut_sort, fs.layout
        from meta_nodes n left join mapper_pins pin on pin.node_id = n.id and pin.user_id = %(me)s
        left join mapper_folder_settings fs on fs.node_id = n.id
        where n.owner_id = %(me)s and n.path like %(prefix)s and n.archived_at is null
        order by n.depth, {meta.CHILD_ORDER} limit %(limit)s""",
        {"me": p.user_id, "prefix": meta.like_prefix(base["path"]), "limit": MAX_FOLDERS})
    shortcuts = await _shortcut_rows(conn, p, base)
    truncated = len(shortcuts) > MAX_SHORTCUTS
    return {"root": {"id": base["id"], "label": base["label"], "child_sort": base["child_sort"]} | await _settings(conn, base["id"]),
            "folders": [folder_out(r, base, pinned=r["pinned"], settings=r) for r in rows],
            "shortcuts": await _shortcuts_out(conn, p, base, shortcuts[:MAX_SHORTCUTS]),
            "truncated": truncated or len(rows) >= MAX_FOLDERS}


async def shortcut(conn, p: Principal, base: dict, object_id: int) -> dict:
    rows = await _shortcut_rows(conn, p, base, [object_id], limit=1)
    if not rows:
        raise HTTPException(404, "Genvejen findes ikke")
    return (await _shortcuts_out(conn, p, base, rows))[0]


# ── Shortcuts ──────────────────────────────────────────────────────────────

def clean_title(title: str | None, url: str) -> str:
    text = " ".join((title or "").split())[:MAX_TITLE]
    return text or host_of(url) or url[:MAX_TITLE]


def normalize(url: str) -> str:
    norm = meta_objects.normalize_url(url)
    if "://" not in norm or norm.endswith("*"):
        raise HTTPException(400, "En genvej skal være en hel adresse, der starter med https://")
    return norm


async def _set_title(conn, p: Principal, object_id: int, title: str | None) -> None:
    """The caller's own title for an object someone else created (None removes it)."""
    cur = await db.fetch_one(conn, "select title from mapper_titles where user_id = %s and object_id = %s for update",
                             (p.user_id, object_id))
    if title is None:
        if cur:
            await conn.execute("delete from mapper_titles where user_id = %s and object_id = %s", (p.user_id, object_id))
            await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="mapper_titles", row_id=object_id,
                        before={"title": cur["title"]})
        return
    if cur and cur["title"] == title:
        return
    await conn.execute("""insert into mapper_titles (user_id, object_id, title) values (%s, %s, %s)
                          on conflict (user_id, object_id) do update set title = excluded.title, updated_at = now()""",
                       (p.user_id, object_id, title))
    await audit(conn, "update" if cur else "create", actor_id=p.user_id, meta=p.meta, table="mapper_titles",
                row_id=object_id, before={"title": cur["title"]} if cur else None, after={"title": title})


async def save_url(conn, p: Principal, norm: str, title: str, target: dict) -> dict:
    """A normalised URL into a folder: finds or creates the object, tags it, keeps the caller's own title.
    Returns {id, created, added}. The same audit lines as tagging in app/meta_objects.py."""
    row = await db.fetch_one(conn, """insert into meta_objects (kind, ref, title, created_by, updated_by)
        values ('url', %s, %s, %s, %s) on conflict (kind, ref) do nothing returning id""", (norm, title, p.user_id, p.user_id))
    created = row is not None
    if created:
        object_id = row["id"]
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="meta_objects", row_id=object_id,
                    after={"kind": "url", "ref": norm, "title": title}, via="mapper")
    else:
        cur = await db.fetch_one(conn, "select id, title, created_by from meta_objects where kind = 'url' and ref = %s for update",
                                 (norm,))
        object_id = cur["id"]
        if cur["created_by"] != p.user_id and cur["title"] != title:
            await _set_title(conn, p, object_id, title)
    have = await db.fetch_one(conn, "select 1 as x from meta_assignments where object_id = %s and node_id = %s",
                              (object_id, target["id"]))
    if not have:
        await meta_objects.apply_tags(conn, p, object_id, [target], [])
    return {"id": object_id, "created": created, "added": not have}


async def add_shortcut(conn, p: Principal, *, url: str, title: str | None, folder_id: int | None) -> dict:
    base = await root(conn, p, create=True)
    target = await folder(conn, p, folder_id, base)
    norm = normalize(url)
    res = await save_url(conn, p, norm, clean_title(title, norm), target)
    return await shortcut(conn, p, base, res["id"]) | {"created": res["created"], "added": res["added"]}


async def _in_mapper(conn, p: Principal, base: dict, object_id: int) -> list[int]:
    """The caller's folders (root included) the object is in; 404 when none."""
    rows = await db.fetch_all(conn, """select a.node_id from meta_assignments a join meta_nodes n on n.id = a.node_id
        where a.object_id = %s and n.owner_id = %s and (n.id = %s or n.path like %s)""",
        (object_id, p.user_id, base["id"], meta.like_prefix(base["path"])))
    if not rows:
        raise HTTPException(404, "Genvejen findes ikke")
    return [r["node_id"] for r in rows]


async def update_shortcut(conn, p: Principal, object_id: int, *, title: str | None, move: tuple[int | None, int | None] | None) -> dict:
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Genvejen findes ikke")
    placed = await _in_mapper(conn, p, base, object_id)
    if title is not None:
        title = " ".join(title.split())[:MAX_TITLE]
        if not title:
            raise HTTPException(400, "Skriv en titel")
        obj = await db.fetch_one(conn, "select title, created_by from meta_objects where id = %s for update", (object_id,))
        if obj["created_by"] == p.user_id:
            await meta_objects.update(conn, p, object_id, {"title": title})
            await _set_title(conn, p, object_id, None)
        else:
            await _set_title(conn, p, object_id, None if title == obj["title"] else title)
    if move is not None:
        src = await folder(conn, p, move[0], base)
        dst = await folder(conn, p, move[1], base)
        if src["id"] not in placed:
            raise HTTPException(404, "Genvejen ligger ikke i den mappe")
        if src["id"] != dst["id"]:
            await meta_objects.apply_tags(conn, p, object_id, [] if dst["id"] in placed else [dst], [src["id"]])
    return await shortcut(conn, p, base, object_id)


async def copy_shortcut(conn, p: Principal, object_id: int, to_id: int | None) -> dict:
    """The shortcut into one more folder: one more tag on the same object."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Genvejen findes ikke")
    placed = await _in_mapper(conn, p, base, object_id)
    dst = await folder(conn, p, to_id, base)
    if dst["id"] in placed:
        raise HTTPException(409, f"Genvejen ligger allerede i „{dst['label']}“")
    await meta_objects.apply_tags(conn, p, object_id, [dst], [])
    return await shortcut(conn, p, base, object_id)


async def remove_shortcut(conn, p: Principal, object_id: int, folder_id: int | None, *, everywhere: bool) -> dict:
    """Out of one folder, or (everywhere) out of all of the caller's folders. Its other tags stay; an object left
    without tags is deleted, as everywhere in meta."""
    base = await root(conn, p)
    if base is None:
        raise HTTPException(404, "Genvejen findes ikke")
    placed = await _in_mapper(conn, p, base, object_id)
    if everywhere:
        remove = placed
    else:
        node = await folder(conn, p, folder_id, base)
        if node["id"] not in placed:
            raise HTTPException(404, "Genvejen ligger ikke i den mappe")
        remove = [node["id"]]
    await meta_objects.apply_tags(conn, p, object_id, [], remove)
    left = set(placed) - set(remove)
    if not left:
        await _set_title(conn, p, object_id, None)
    res = await meta_objects.drop_if_empty(conn, p, object_id)
    return {"id": object_id, "removed": len(remove), "deleted": bool(res.get("deleted")), "in_mapper": bool(left)}


# ── Import of browser favourites ───────────────────────────────────────────

async def _ensure_child(conn, p: Principal, parent: dict, label: str, stats: dict) -> dict:
    label = " ".join(label.split())[:MAX_LABEL] or "Uden navn"
    slug = meta.slugify(label) or "mappe"
    if parent["depth"] >= meta.MAX_DEPTH:
        return parent  # deeper than a tree may go: the shortcuts land in the deepest folder there is room for
    existing = await _children_slug_taken(conn, parent, slug)
    if existing:
        node = await meta.node(conn, existing["id"], p)
        if node["owner_id"] == p.user_id and node["archived_at"] is None:
            stats["folders_reused"] += 1
            return _small(node, p.user_id)
        slug = f"{slug}-{existing['id']}"[:meta.MAX_SLUG]
    made = await meta.create_node(conn, p, dim_slug=meta.PRIVATE, parent_id=parent["id"], label=label, slug=slug)
    stats["folders_created"] += 1
    return _small(made, p.user_id)


async def import_bookmarks(conn, p: Principal, *, folders: list[dict], items: list[dict], target: str, target_label: str,
                           keep_structure: bool, skip_duplicates: bool, pin_bar: bool) -> dict:
    """One transaction: the chosen bookmark folders become folders (same names under the same parent are reused,
    so importing twice does not double the tree) and the bookmarks become shortcuts.

    folders: [{key, parent (key or None), label, bar}] · items: [{folder (key or None), url, title}]
    target "new": everything under a new folder target_label; "top": straight under the root."""
    base = await root(conn, p, create=True)
    stats = {"folders_created": 0, "folders_reused": 0, "shortcuts_created": 0, "shortcuts_added": 0,
             "duplicates": 0, "invalid": 0, "pinned": 0}
    top = await _ensure_child(conn, p, base, target_label or IMPORT_LABEL, stats) if target == "new" else base
    seen = {r["ref"] for r in await db.fetch_all(conn, """select distinct o.ref from meta_assignments a
        join meta_nodes n on n.id = a.node_id join meta_objects o on o.id = a.object_id
        where n.owner_id = %s and (n.id = %s or n.path like %s) and o.kind = 'url'""",
        (p.user_id, base["id"], meta.like_prefix(base["path"])))}

    by_key = {f["key"]: f for f in folders}
    nodes: dict[str, dict] = {}

    async def resolve(key: str, visiting: frozenset) -> dict:
        if key in nodes:
            return nodes[key]
        f = by_key[key]
        parent_key = f.get("parent")
        parent = top if parent_key is None or parent_key not in by_key or parent_key in visiting \
            else await resolve(parent_key, visiting | {key})
        node = await _ensure_child(conn, p, parent, f["label"], stats)
        nodes[key] = node
        if f.get("bar") and pin_bar and node["id"] not in (base["id"], top["id"]):
            await set_pin(conn, p, node["id"], True)
            stats["pinned"] += 1
        return node

    if keep_structure:
        for f in folders:
            await resolve(f["key"], frozenset())

    for item in items:
        where = nodes.get(item.get("folder")) if keep_structure else None
        where = where or top
        try:
            norm = normalize(item["url"])
        except HTTPException:
            stats["invalid"] += 1
            continue
        if skip_duplicates and norm in seen:
            stats["duplicates"] += 1
            continue
        seen.add(norm)
        res = await save_url(conn, p, norm, clean_title(item.get("title"), norm), where)
        stats["shortcuts_created"] += res["created"]
        stats["shortcuts_added"] += res["added"]
    await audit(conn, "mapper.import", actor_id=p.user_id, meta=p.meta, table="meta_nodes", row_id=top["id"],
                target=target, keep_structure=keep_structure, skip_duplicates=skip_duplicates, **stats)
    return stats | {"root_id": base["id"], "target_id": None if top["id"] == base["id"] else top["id"]}


# ── Views ──────────────────────────────────────────────────────────────────

def view_out(row: dict, p: Principal) -> dict:
    return {"id": row["id"], "name": row["name"], "shared": row["shared"], "spec": row["spec"],
            "mine": row["owner_id"] == p.user_id, "owner_name": row.get("owner_name"), "updated_at": row["updated_at"]}


async def views(conn, p: Principal) -> list[dict]:
    rows = await db.fetch_all(conn, """select v.*, u.display_name as owner_name from mapper_views v
        join users u on u.id = v.owner_id where v.owner_id = %s or v.shared order by v.shared, lower(v.name), v.id""",
        (p.user_id,))
    return [view_out(r, p) for r in rows]


async def create_view(conn, p: Principal, *, name: str, shared: bool, spec: dict) -> dict:
    row = await db.fetch_one(conn, """insert into mapper_views (owner_id, name, shared, spec) values (%s, %s, %s, %s)
                                      returning *""", (p.user_id, name.strip(), shared, Jsonb(spec)))
    await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="mapper_views", row_id=row["id"],
                after={"name": row["name"], "shared": shared, "spec": spec})
    return view_out(row, p)


async def _view(conn, p: Principal, view_id: int) -> dict:
    row = await db.fetch_one(conn, "select * from mapper_views where id = %s and (owner_id = %s or shared) for update",
                             (view_id, p.user_id))
    if row is None:
        raise HTTPException(404, "Visningen findes ikke")
    return row


async def update_view(conn, p: Principal, view_id: int, fields: dict) -> dict:
    cur = await _view(conn, p, view_id)
    if cur["owner_id"] != p.user_id:
        raise HTTPException(403, "Kun den, der lavede visningen, kan rette den")
    new = {"name": cur["name"], "shared": cur["shared"], "spec": cur["spec"]}
    for k in new:
        if fields.get(k) is not None:
            new[k] = fields[k].strip() if k == "name" else fields[k]
    before, after = changed({k: cur[k] for k in new}, new)
    if after:
        row = await db.fetch_one(conn, """update mapper_views set name = %s, shared = %s, spec = %s, updated_at = now()
                                         where id = %s returning *""", (new["name"], new["shared"], Jsonb(new["spec"]), view_id))
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="mapper_views", row_id=view_id,
                    before=before, after=after)
        cur = row
    return view_out(cur, p)


async def delete_view(conn, p: Principal, view_id: int) -> dict:
    cur = await _view(conn, p, view_id)
    if cur["owner_id"] != p.user_id and not (cur["shared"] and meta.is_meta_admin(p)):
        raise HTTPException(403, "Kun den, der lavede visningen, eller en admin kan slette den")
    await conn.execute("delete from mapper_views where id = %s", (view_id,))
    await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="mapper_views", row_id=view_id,
                before={"name": cur["name"], "shared": cur["shared"], "owner_id": cur["owner_id"]})
    return {"ok": True}


# ── Preview: may the page be shown in a frame? ─────────────────────────────

# Tests replace this with an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None
FRAME_TTL = 600
FRAME_PER_MINUTE = 60
MAX_REDIRECTS = 4
_frame_cache: dict[str, tuple[float, dict]] = {}
_frame_calls: dict[int, deque] = {}


def _xfo_blocks(value: str | None) -> bool:
    """X-Frame-Options: DENY and SAMEORIGIN (and the obsolete ALLOW-FROM) stop us; browsers ignore other values."""
    v = (value or "").strip().lower()
    return v in ("deny", "sameorigin") or v.startswith("allow-from")


def _ancestors_block(csp: str | None) -> bool:
    """A frame-ancestors directive that is not '*' leaves out the side panel (our origin inside chrome-extension:)."""
    for policy in (csp or "").split(","):
        for directive in policy.split(";"):
            parts = directive.strip().split()
            if parts and parts[0].lower() == "frame-ancestors" and "*" not in parts[1:]:
                return True
    return False


def _rate_limit(user_id: int) -> None:
    now = time.monotonic()
    q = _frame_calls.setdefault(user_id, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= FRAME_PER_MINUTE:
        raise HTTPException(429, "For mange forhåndsvisninger på kort tid. Vent et øjeblik")
    q.append(now)


def _pinned(url: str) -> tuple[str, dict, dict]:
    """Checks the address and resolves its host once, then returns what to request: the URL with the checked IP
    address in place of the host name, plus the Host header and the TLS name (SNI) so the certificate is checked
    against the name as usual. Connecting to the address we checked closes the gap where a name passes the check
    and then resolves to an internal address when the connection is made (DNS rebinding). Raises UnsafeUrl."""
    webhooks.check_url(url)  # scheme, no user info, length, no internal host names
    parts = urlsplit(url)
    host = parts.hostname or ""
    if config.settings.webhook_allow_private:  # development and tests: no lookup, as for webhooks
        return url, {}, {}
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global or literal.is_multicast:
            raise webhooks.UnsafeUrl("Adressen peger på et internt eller reserveret net")
        return url, {}, {}
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise webhooks.UnsafeUrl("Værtsnavnet kunne ikke slås op") from None
    addresses = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addresses or any(not a.is_global or a.is_multicast for a in addresses):
        raise webhooks.UnsafeUrl("Adressen peger på et internt eller reserveret net")
    ip = addresses[0]
    netloc = f"[{ip}]" if ip.version == 6 else str(ip)
    if parts.port:
        netloc += f":{parts.port}"
    target = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    host_header = host if not parts.port else f"{host}:{parts.port}"
    return target, {"Host": host_header}, {"sni_hostname": host}


def _verdict(url: str, *, framable: bool, kind: str = "html", reason: str | None = None, final: str | None = None) -> dict:
    return {"url": url, "framable": framable, "kind": kind, "reason": reason, "final_url": final or url}


async def frame_check(p: Principal, url: str) -> dict:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme == "http":
        return _verdict(url, framable=False, reason="Siden bruger http og kan ikke vises sikkert her")
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(400, "Kun https-adresser kan forhåndsvises")
    cached = _frame_cache.get(url)
    if cached and time.monotonic() - cached[0] < FRAME_TTL:
        return cached[1]
    _rate_limit(p.user_id)
    current = url
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=False, transport=transport,
                                     event_hooks=integrations.hooks("mapper_preview")) as client:
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    target, host_header, tls = await asyncio.to_thread(_pinned, current)
                except webhooks.UnsafeUrl:
                    raise HTTPException(400, "Adressen peger på et internt net og kan ikke forhåndsvises") from None
                async with client.stream("GET", target, extensions=tls, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; Gaer-preview/1)",
                        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
                        **host_header}) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        nxt = urljoin(current, r.headers["location"])
                        if urlsplit(nxt).scheme != "https":
                            verdict = _verdict(url, framable=False, reason="Siden sender videre til en http-adresse",
                                               final=nxt)
                            break
                        current = nxt
                        continue
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    kind = "pdf" if ctype == "application/pdf" else "html" if ctype in ("text/html", "application/xhtml+xml", "") else "other"
                    if r.status_code >= 400:
                        verdict = _verdict(url, framable=False, kind=kind, final=current,
                                           reason=f"Siden svarede {r.status_code}, da den blev tjekket")
                    elif _xfo_blocks(r.headers.get("x-frame-options")) or _ancestors_block(r.headers.get("content-security-policy")):
                        verdict = _verdict(url, framable=False, kind=kind, final=current,
                                           reason="Siden tillader ikke at blive vist inde i en anden side")
                    elif r.headers.get("content-disposition", "").lower().startswith("attachment"):
                        verdict = _verdict(url, framable=False, kind=kind, final=current, reason="Siden er en fil til download")
                    else:
                        verdict = _verdict(url, framable=True, kind=kind, final=current)
                    break
            else:
                verdict = _verdict(url, framable=False, reason="For mange viderestillinger", final=current)
    except httpx.TimeoutException:
        verdict = _verdict(url, framable=False, reason="Siden svarede ikke i tide")
    except httpx.HTTPError:
        verdict = _verdict(url, framable=False, reason="Siden kunne ikke nås")
    if len(_frame_cache) > 2000:
        _frame_cache.clear()
    _frame_cache[url] = (time.monotonic(), verdict)
    return verdict

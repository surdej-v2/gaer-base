"""/api/meta — træstrukturer for everyone logged in: browse, search, resolve stored ids, and create nodes where the
rules allow (app/meta.py). Owners also rename, move, archive and delete nodes in their private tree; shared nodes are
edited in /admin/meta (routes/admin_meta.py). Tagging objects (URLs, entities, notes) with nodes, the cross-reference
context of a node and URL look-up: app/meta_objects.py."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import db, meta, meta_objects
from app.sessions import Principal, current_user

router = APIRouter(prefix="/api/meta", tags=["meta"])

DIM = r"^[^/\s]{1,64}$"


class NodeIn(BaseModel):
    dimension: str = Field(pattern=DIM)
    parent_id: int | None = None
    label: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=meta.MAX_SLUG)
    description: str = Field(default="", max_length=2000)


class NodePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    archived: bool | None = None
    parent_id: int | None = None
    slug: str | None = Field(default=None, max_length=meta.MAX_SLUG)
    labels: dict[str, str] | None = Field(default=None, max_length=10)   # other languages: {"en": "Operations"}
    sort_key: str | None = Field(default=None, max_length=64)             # null or "": cleared
    child_sort: str | None = Field(default=None, pattern="^(label|sort_key|manual)?$")


def patch_dict(body: NodePatch) -> dict:
    """Only the fields sent; parent_id: null means 'move to the top level' when it is sent."""
    return body.model_dump(exclude_unset=True)


def parse_ids(ids: str) -> list[int]:
    try:
        out = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids skal være tal adskilt af komma") from None
    if len(out) > 500:
        raise HTTPException(400, "Højst 500 ids ad gangen")
    return out


@router.get("/dimensions")
async def dimensions(p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select d.*, (select count(*) from meta_nodes n where n.dimension_id = d.id
                                                         and n.archived_at is null) as n
                                           from meta_dimensions d order by lower(d.label)""")
        mine = await db.fetch_one(conn, "select count(*) as n from meta_nodes where owner_id = %s and archived_at is null",
                                  (p.user_id,))
    admin = meta.is_meta_admin(p)
    return [meta.dim_out(d, p, admin=admin, count=d["n"]) for d in rows] + [
        meta.dim_out(None, p, admin=admin, count=mine["n"])]


@router.get("/tree")
async def tree(dimension: str | None = Query(default=None, pattern=DIM), root: int | None = None,
               depth: int = Query(default=2, ge=1, le=meta.MAX_DEPTH), include_archived: bool = False,
               p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta.tree(conn, p, dim_slug=dimension, root_id=root, depth=depth, include_archived=include_archived)


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=120), dimension: str | None = Query(default=None, pattern=DIM),
                 root: int | None = None, limit: int = 50, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta.search(conn, p, q.strip(), dim_slug=dimension, root_id=root, limit=limit)


@router.get("/nodes")
async def resolve(ids: str = Query(max_length=6000), p: Principal = Depends(current_user)):
    """Stored node ids → nodes with their trail ('Organisation › Drift'), for showing a saved value."""
    async with db.connection() as conn:
        return await meta.resolve(conn, p, parse_ids(ids))


@router.get("/expand")
async def expand(ids: str = Query(max_length=6000), p: Principal = Depends(current_user)):
    """A filter value with cascade="subtree" → every node id it covers."""
    async with db.connection() as conn:
        return {"ids": await meta.expand_subtree(conn, p, parse_ids(ids))}


@router.post("/nodes")
async def create(body: NodeIn, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        node = await meta.create_node(conn, p, dim_slug=body.dimension, parent_id=body.parent_id, label=body.label,
                                      slug=body.slug, description=body.description)
    await meta.count_event("meta.node_created", node["id"])
    return node


@router.patch("/nodes/{node_id}")
async def update(node_id: int, body: NodePatch, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta.update_node(conn, p, node_id, patch_dict(body), admin=False)


@router.delete("/nodes/{node_id}")
async def delete(node_id: int, force: bool = False, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta.delete_node(conn, p, node_id, admin=False, force=force)


# ── Objects and tags (app/meta_objects.py) ─────────────────────────────────

KIND = "^(url|entity|note)$"


class ObjectIn(BaseModel):
    kind: str = Field(pattern=KIND)
    ref: str | None = Field(default=None, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    node_ids: list[int] = Field(min_length=1, max_length=meta_objects.MAX_NODES)


class ObjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)


class NodesIn(BaseModel):
    node_ids: list[int] = Field(max_length=meta_objects.MAX_NODES)


@router.get("/objects")
async def objects(node: int, subtree: bool = False, kind: str | None = Query(default=None, pattern=KIND),
                  q: str | None = Query(default=None, max_length=200), limit: int = 100, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta_objects.at_node(conn, p, node, subtree=subtree, kind=kind, q=q, limit=limit)


@router.get("/objects/by-ref")
async def object_by_ref(kind: str = Query(pattern=KIND), ref: str = Query(max_length=2000), p: Principal = Depends(current_user)):
    """The tags of a URL or an entity, for integrations ("which nodes is clip:12 tagged with?")."""
    async with db.connection() as conn:
        return await meta_objects.by_ref(conn, p, kind, ref)


@router.get("/objects/{object_id}")
async def object_get(object_id: int, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta_objects.one(conn, p, object_id)


@router.post("/objects")
async def object_tag(body: ObjectIn, p: Principal = Depends(current_user)):
    """Create or find the object by kind and reference, and tag it with the nodes."""
    async with db.connection() as conn:
        return await meta_objects.tag(conn, p, kind=body.kind, ref=body.ref, title=body.title,
                                      description=body.description, node_ids=body.node_ids)


@router.patch("/objects/{object_id}")
async def object_update(object_id: int, body: ObjectPatch, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta_objects.update(conn, p, object_id, body.model_dump(exclude_unset=True))


@router.put("/objects/{object_id}/nodes")
async def object_set_nodes(object_id: int, body: NodesIn, p: Principal = Depends(current_user)):
    """Your tags on the object become exactly these. Removing the last tag deletes the object."""
    async with db.connection() as conn:
        return await meta_objects.set_nodes(conn, p, object_id, body.node_ids)


@router.delete("/objects/{object_id}/nodes/{node_id}")
async def object_untag(object_id: int, node_id: int, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await meta_objects.untag(conn, p, object_id, node_id)


@router.get("/context")
async def context(node: int, p: Principal = Depends(current_user)):
    """A node with its parents, siblings, children, objects and the nodes those objects are also tagged with."""
    async with db.connection() as conn:
        return await meta_objects.context(conn, p, node)


@router.get("/resolve-url")
async def resolve_url(url: str = Query(max_length=2000), p: Principal = Depends(current_user)):
    """The current page (browser extension) → matching tagged objects: exact, prefix pattern, host."""
    async with db.connection() as conn:
        return await meta_objects.resolve_url(conn, p, url)


@router.get("/normalize-url")
async def normalize(url: str = Query(max_length=2000), _: Principal = Depends(current_user)):
    return {"url": meta_objects.normalize_url(url)}

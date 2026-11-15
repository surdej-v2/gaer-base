"""/api/mapper — the Mapper tab in the extension's side panel (app/mapper.py, specs/mapper/): the caller's own folders
(private nodes under ~/mapper) and shortcuts (URL objects tagged with them), pins, saved views, the import of browser
favourites in one transaction, and the header check that decides whether a page can be previewed in a frame."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from app import db, events, mapper
from app.sessions import Principal, current_user

router = APIRouter(prefix="/api/mapper", tags=["mapper"])


@router.get("")
async def state(p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.state(conn, p)


# ── Folders ────────────────────────────────────────────────────────────────

class FolderIn(BaseModel):
    parent_id: int | None = None
    label: str = Field(min_length=1, max_length=mapper.MAX_LABEL)


class FolderPatch(BaseModel):
    """Egenskaber and moving. Fields sent as null or "" are cleared (en_label, sort_key, child_sort, shortcut_sort, layout)."""
    label: str | None = Field(default=None, min_length=1, max_length=mapper.MAX_LABEL)
    parent_id: int | None = None   # sent as null: move to the top level
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    en_label: str | None = Field(default=None, max_length=mapper.MAX_LABEL)
    sort_key: str | None = Field(default=None, max_length=64)
    child_sort: Literal["label", "sort_key", "manual", ""] | None = None
    shortcut_sort: Literal["manual", "name", "site", "added", ""] | None = None
    layout: Literal["list", "grid", "table", ""] | None = None


class OrderIn(BaseModel):
    ids: list[int] = Field(max_length=mapper.MAX_SHORTCUTS)


class CopyIn(BaseModel):
    to_id: int | None = None   # None: the top level


class PinIn(BaseModel):
    pinned: bool


@router.post("/folders")
async def create_folder(body: FolderIn, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.create_folder(conn, p, body.parent_id, body.label)


@router.patch("/folders/{node_id}")
async def update_folder(node_id: int, body: FolderPatch, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.update_folder(conn, p, node_id, body.model_dump(exclude_unset=True))


@router.delete("/folders/{node_id}")
async def delete_folder(node_id: int, force: bool = False, p: Principal = Depends(current_user)):
    """Without force a folder with subfolders or shortcuts is refused (409, the message says what is in it)."""
    async with db.connection() as conn:
        return await mapper.delete_folder(conn, p, node_id, force=force)


@router.put("/folders/{node_id}/children-order")
async def order_children(node_id: int, body: OrderIn, p: Principal = Depends(current_user)):
    """Manual order of the subfolders (node_id may be the root): the folder's subfolder sort becomes Manuel."""
    async with db.connection() as conn:
        return await mapper.order_children(conn, p, node_id, body.ids)


@router.put("/folders/{node_id}/shortcut-order")
async def order_shortcuts(node_id: int, body: OrderIn, p: Principal = Depends(current_user)):
    """Manual order of the shortcuts in a folder (drag and drop): its shortcut sort becomes manual."""
    async with db.connection() as conn:
        return await mapper.order_shortcuts(conn, p, node_id, body.ids)


@router.post("/folders/{node_id}/copy")
async def copy_folder(node_id: int, body: CopyIn, p: Principal = Depends(current_user)):
    """Paste of a copied folder: the subtree with its shortcuts, cloned under to_id in one transaction."""
    async with db.connection() as conn:
        return await mapper.copy_folder(conn, p, node_id, body.to_id)


@router.put("/folders/{node_id}/pin")
async def pin_folder(node_id: int, body: PinIn, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.set_pin(conn, p, node_id, body.pinned)


# ── Shortcuts ──────────────────────────────────────────────────────────────

class ShortcutIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=mapper.MAX_TITLE)
    folder_id: int | None = None   # None: the top level


class MoveIn(BaseModel):
    from_id: int | None = None
    to_id: int | None = None


class ShortcutPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=mapper.MAX_TITLE)
    move: MoveIn | None = None


@router.post("/shortcuts")
async def add_shortcut(body: ShortcutIn, p: Principal = Depends(current_user)):
    """"Gem denne side" and "Ny genvej": the URL into a folder. Saving a URL that is there already only adds it."""
    async with db.connection() as conn:
        return await mapper.add_shortcut(conn, p, url=body.url, title=body.title, folder_id=body.folder_id)


@router.patch("/shortcuts/{object_id}")
async def update_shortcut(object_id: int, body: ShortcutPatch, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.update_shortcut(conn, p, object_id, title=body.title,
                                            move=(body.move.from_id, body.move.to_id) if body.move else None)


@router.post("/shortcuts/{object_id}/copy")
async def copy_shortcut(object_id: int, body: CopyIn, p: Principal = Depends(current_user)):
    """Paste of a copied shortcut: the same shortcut in one more folder."""
    async with db.connection() as conn:
        return await mapper.copy_shortcut(conn, p, object_id, body.to_id)


@router.delete("/shortcuts/{object_id}")
async def remove_shortcut(object_id: int, folder: int | None = None, everywhere: bool = False,
                          p: Principal = Depends(current_user)):
    """Out of one folder (folder=<id>, none = the top level) or out of every folder (everywhere=true)."""
    async with db.connection() as conn:
        return await mapper.remove_shortcut(conn, p, object_id, folder, everywhere=everywhere)


# ── Import ─────────────────────────────────────────────────────────────────

class ImportFolder(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    parent: str | None = Field(default=None, max_length=64)
    label: str = Field(max_length=500)
    bar: bool = False


class ImportItem(BaseModel):
    folder: str | None = Field(default=None, max_length=64)
    url: str = Field(max_length=4000)
    title: str = Field(default="", max_length=2000)


class ImportIn(BaseModel):
    target: Literal["new", "top"] = "new"
    target_label: str = Field(default=mapper.IMPORT_LABEL, min_length=1, max_length=mapper.MAX_LABEL)
    keep_structure: bool = True
    skip_duplicates: bool = True
    pin_bar: bool = True
    folders: list[ImportFolder] = Field(default_factory=list, max_length=mapper.MAX_IMPORT_FOLDERS)
    items: list[ImportItem] = Field(default_factory=list, max_length=mapper.MAX_IMPORT_ITEMS)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({f.key for f in self.folders}) != len(self.folders):
            raise ValueError("Mappernes nøgler skal være forskellige")
        return self


@router.post("/import")
async def import_bookmarks(body: ImportIn, p: Principal = Depends(current_user)):
    """Browser favourites (from chrome.bookmarks or an exported .html file, parsed in the browser) in one
    transaction: folders + shortcuts, audited like the rest of meta, plus one mapper.import line with the counts."""
    async with db.connection() as conn:
        res = await mapper.import_bookmarks(
            conn, p, folders=[f.model_dump() for f in body.folders], items=[i.model_dump() for i in body.items],
            target=body.target, target_label=body.target_label, keep_structure=body.keep_structure,
            skip_duplicates=body.skip_duplicates, pin_bar=body.pin_bar)
    await events.emit("mapper.import", ref=str(res["root_id"]), value=res["shortcuts_added"])
    return res


# ── Views ──────────────────────────────────────────────────────────────────

Column = Literal["site", "folder", "added"]


class ViewSpec(BaseModel):
    layout: Literal["list", "grid", "table"] = "list"
    # "folder" = Mappens sortering (each folder's own shortcut sort); "manual" is the older name for it.
    sort: Literal["folder", "manual", "name", "site", "added"] = "folder"
    columns: list[Column] = Field(default_factory=list, max_length=3)
    filter: str = Field(default="", max_length=200)


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    shared: bool = False
    spec: ViewSpec


class ViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    shared: bool | None = None
    spec: ViewSpec | None = None


@router.get("/views")
async def views(p: Principal = Depends(current_user)):
    """"Mine visninger" (the caller's) and "Delt med teamet" (shared by anyone)."""
    async with db.connection() as conn:
        return await mapper.views(conn, p)


@router.post("/views")
async def create_view(body: ViewIn, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.create_view(conn, p, name=body.name, shared=body.shared, spec=body.spec.model_dump())


@router.patch("/views/{view_id}")
async def update_view(view_id: int, body: ViewPatch, p: Principal = Depends(current_user)):
    fields = body.model_dump(exclude_unset=True)
    if body.spec is not None:
        fields["spec"] = body.spec.model_dump()  # always the whole spec, defaults included
    async with db.connection() as conn:
        return await mapper.update_view(conn, p, view_id, fields)


@router.delete("/views/{view_id}")
async def delete_view(view_id: int, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await mapper.delete_view(conn, p, view_id)


# ── Preview ────────────────────────────────────────────────────────────────

@router.get("/frame-check")
async def frame_check(url: str = Query(min_length=1, max_length=2000), p: Principal = Depends(current_user)):
    """May the page be shown in a frame in the side panel? Only the response headers are read (X-Frame-Options,
    CSP frame-ancestors, Content-Type, Content-Disposition), through the SSRF guard. Answers {framable, kind, reason}."""
    return await mapper.frame_check(p, url)

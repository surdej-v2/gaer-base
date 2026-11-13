"""/admin/data/transfer: data export and import (app/transfer.py).

Only admins with Fuld adgang (the section is in no other role). Starting an export, downloading it, running an
import and undoing one need a recent second factor (require_admin_stepup): a package can carry everyone's data out
of the app, and an import can change anything. The upload and the dry run change nothing but the transfer row.
"""

import hashlib
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from psycopg.types.json import Jsonb

from app import admin_roles, config, db, jobs, secrets_box, transfer
from app.logs import audit
from app.sessions import Principal, require_admin, require_admin_stepup
from app.storage import get_storage

router = APIRouter(prefix="/api/admin/transfer", tags=["admin"], dependencies=[Depends(require_admin)])

COLUMNS = ("id, kind, status, options, filename, size_bytes, manifest, report, error, created_by, created_at, "
           "updated_at, finished_at, expires_at, undo_until, storage_key is not null as has_package, "
           "password_enc is not null as has_password, dry_run_key, "
           "(select coalesce(u.display_name, u.email, u.phone) from users u where u.id = created_by) as created_by_name")


def _public(row: dict) -> dict:
    out = dict(row)
    key = out.pop("dry_run_key", None)
    out["dry_run_current"] = bool(key) and out["kind"] == "import" and key == transfer.options_key(out["options"] or {})
    return out


async def _get(conn, transfer_id: int, *, lock: bool = False) -> dict:
    row = await db.fetch_one(conn, f"select {COLUMNS} from data_transfers where id = %s"
                             + (" for update" if lock else ""), (transfer_id,))
    if row is None:
        raise HTTPException(404, "Eksporten eller importen findes ikke")
    return row


@router.get("/areas")
async def areas():
    """What can be exported: each area with its rows and an estimate of its size."""
    out = []
    async with db.connection() as conn:
        for a in transfer.AREAS:
            rows = size = 0
            for t in transfer.area_tables(a.key):
                if t.lookup_only:
                    continue
                where = f" where {t.where}" if t.where else ""
                r = await db.fetch_one(conn, f"""select count(*) as n,
                    (select greatest(pg_total_relation_size('{t.name}'), 0)) as bytes,
                    (select count(*) from {t.name}) as total from {t.name} t{where}""")
                rows += r["n"]
                size += int(r["bytes"] * r["n"] / r["total"]) if r["total"] else 0
            out.append({"key": a.key, "title": a.title, "description": a.description, "group": a.group,
                        "importable": a.importable, "personal": transfer.area_personal(a.key),
                        "rows": rows if a.key != "roles" else None, "bytes": size})
        blobs = await db.fetch_one(conn, """select count(*) as n, coalesce(sum(size_bytes), 0) as bytes from files
                                            where kind = 'file' and space in ('user', 'shared')""")
        migration = await db.fetch_one(conn, "select max(name) as name from schema_migrations")
    return {"areas": out, "blobs": blobs, "never": transfer.NEVER_EXPORTED, "max_mb": config.settings.transfer_max_mb,
            "download_hours": transfer.DOWNLOAD_HOURS, "undo_days": transfer.UNDO_DAYS,
            "migration": migration["name"] if migration else None}


@router.get("")
async def history(kind: Literal["export", "import"] | None = None, limit: int = 50):
    limit = max(1, min(limit, 200))
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {COLUMNS} from data_transfers
                                            where %s::text is null or kind = %s order by id desc limit %s""",
                                  (kind, kind, limit))
    return {"items": [_public(r) for r in rows]}


@router.get("/{transfer_id}")
async def get_transfer(transfer_id: int):
    async with db.connection() as conn:
        row = await _get(conn, transfer_id)
    out = _public(row)
    if row["kind"] == "import" and row["manifest"]:
        out["summary"] = row["manifest"].get("_summary")
    return out


# ── Export ──────────────────────────────────────────────────────────────────

class ExportIn(BaseModel):
    areas: list[str] = Field(min_length=1, max_length=len(transfer.AREAS))
    personal: Literal["with", "pseudo", "without"] = "pseudo"
    format: Literal["json", "csv"] = "json"
    since_days: int | None = Field(default=None, ge=1, le=3650)
    include_blobs: bool = False
    password: str | None = Field(default=None, max_length=200)
    filename: str = Field(default="", max_length=120)


def _filename(name: str, fallback: str) -> str:
    name = "".join(c for c in name.strip() if c.isalnum() or c in "-_. ")[:120].strip()
    return name or fallback


@router.post("/exports")
async def start_export(body: ExportIn, admin: Principal = Depends(require_admin_stepup)):
    try:
        options = transfer.clean_export_options(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    password = (body.password or "").strip()
    if password and len(password) < 10:
        raise HTTPException(400, "Adgangskoden skal være mindst 10 tegn")
    ext = ".gaerx" if password else ".zip"
    async with db.connection() as conn:
        today = (await db.fetch_one(conn, "select to_char(now(), 'YYYY-MM-DD') as d"))["d"]
        filename = _filename(body.filename.removesuffix(".zip").removesuffix(".gaerx"), f"gaer-eksport-{today}") + ext
        row = await db.fetch_one(conn, """
            insert into data_transfers (kind, status, options, password_enc, filename, created_by)
            values ('export', 'queued', %s, %s, %s, %s) returning id""",
            (Jsonb(options), secrets_box.encrypt(password, transfer.PASSWORD_PURPOSE) if password else None,
             filename, admin.user_id))
        job_id = await jobs.enqueue(conn, "data.export", {"transfer_id": row["id"]}, max_attempts=1)
        await conn.execute("update data_transfers set job_id = %s where id = %s", (job_id, row["id"]))
        await audit(conn, "transfer.export_requested", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=row["id"], areas=options["areas"], personal=options["personal"], format=options["format"],
                    include_blobs=options["include_blobs"], encrypted=bool(password))
        return _public(await _get(conn, row["id"]))


@router.get("/{transfer_id}/download")
async def download(transfer_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select * from data_transfers where id = %s", (transfer_id,))
        if row is None or row["kind"] != "export":
            raise HTTPException(404, "Eksporten findes ikke")
        if row["status"] != "ready" or not row["storage_key"]:
            raise HTTPException(409, "Eksporten kan ikke hentes (den er ikke klar, eller den er udløbet)")
        await audit(conn, "transfer.downloaded", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=transfer_id)
    return {"url": await get_storage().download_url(row["storage_key"], row["filename"])}


@router.post("/{transfer_id}/cancel")
async def cancel(transfer_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await _get(conn, transfer_id, lock=True)
        if row["kind"] != "export" or row["status"] not in ("queued", "running"):
            raise HTTPException(409, "Kun en eksport, der venter eller kører, kan annulleres")
        await conn.execute("""update data_transfers set status = 'cancelled', password_enc = null, finished_at = now(),
                              updated_at = now() where id = %s""", (transfer_id,))
        await audit(conn, "transfer.cancelled", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=transfer_id)
    return {"ok": True}


@router.delete("/{transfer_id}/package")
async def delete_package(transfer_id: int, admin: Principal = Depends(require_admin)):
    """Deletes the package now (it would be deleted by data.transfer_cleanup anyway)."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select * from data_transfers where id = %s for update", (transfer_id,))
        if row is None:
            raise HTTPException(404, "Eksporten eller importen findes ikke")
        if row["status"] in ("queued", "running", "dry_running", "undoing"):
            raise HTTPException(409, "Vent til jobbet er færdigt")
        if not row["storage_key"]:
            raise HTTPException(409, "Pakken er allerede slettet")
        await conn.execute("""update data_transfers set storage_key = null, password_enc = null, updated_at = now(),
                              status = case when status in ('ready', 'uploaded', 'dry_ready') then 'expired' else status end
                              where id = %s""", (transfer_id,))
        await audit(conn, "transfer.deleted", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=transfer_id)
    await transfer.delete_package_objects([row["storage_key"]])
    return {"ok": True}


# ── Import ──────────────────────────────────────────────────────────────────

@router.post("/imports")
async def upload_package(file: UploadFile = File(...), password: str = Form(default="", max_length=200),
                         admin: Principal = Depends(require_admin)):
    """Stores an uploaded package and reads its manifest. Nothing is imported yet."""
    path = transfer._temp(".pkg")
    temps = [path]
    pkg = None
    try:
        size, digest = 0, hashlib.sha256()
        with open(path, "wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > transfer.max_bytes():
                    raise HTTPException(413, f"Pakken er større end {config.settings.transfer_max_mb} MB")
                digest.update(chunk)
                f.write(chunk)
        encrypted = transfer.is_encrypted(path)
        try:
            pkg, more = transfer.open_package(path, password.strip() or None)
        except transfer.PackageError as e:
            raise HTTPException(400, str(e))
        temps += more
        manifest = dict(pkg.manifest)
        manifest["_summary"] = transfer.summary(pkg)
        key = f"transfers/{uuid.uuid4()}"
        with open(path, "rb") as f:
            await get_storage().put_stream(key, f, "application/octet-stream")
        async with db.connection() as conn:
            row = await db.fetch_one(conn, """
                insert into data_transfers (kind, status, options, password_enc, storage_key, filename, size_bytes,
                                            manifest, created_by)
                values ('import', 'uploaded', '{}', %s, %s, %s, %s, %s, %s) returning id""",
                (secrets_box.encrypt(password.strip(), transfer.PASSWORD_PURPOSE) if encrypted else None, key,
                 _filename(file.filename or "", "pakke.zip"), size, Jsonb(manifest), admin.user_id))
            await audit(conn, "transfer.uploaded", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                        row_id=row["id"], bytes=size, sha256=digest.hexdigest(), encrypted=encrypted,
                        source=manifest.get("source"), areas=manifest.get("areas"), personal=manifest.get("personal"))
            out = _public(await _get(conn, row["id"]))
        out["summary"] = manifest["_summary"]
        return out
    finally:
        if pkg:
            pkg.close()
        for p in temps:
            if os.path.exists(p):
                os.unlink(p)


class ImportIn(BaseModel):
    areas: list[str] = Field(min_length=1, max_length=len(transfer.AREAS))
    conflict: Literal["skip", "overwrite", "merge", "copy"] = "skip"
    match: Literal["natural", "id"] = "natural"
    missing: Literal["create", "skip", "me"] = "create"
    on_error: Literal["all", "continue"] = "all"
    keep_times: bool = True
    file_blobs: bool = True


def _import_options(body: ImportIn, row: dict, admin: Principal) -> dict:
    if row["kind"] != "import":
        raise HTTPException(404, "Importen findes ikke")
    if not row["has_package"]:
        raise HTTPException(409, "Pakken er slettet. Upload den igen.")
    try:
        options = transfer.clean_import_options(body.model_dump(), row["manifest"] or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if "roles" in options["areas"] and admin_roles.FULL not in admin.admin_roles and not admin.super_admin:
        raise HTTPException(403, "Kun admins med Fuld adgang kan importere admin-roller")
    return options


@router.post("/{transfer_id}/dry-run")
async def dry_run(transfer_id: int, body: ImportIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await _get(conn, transfer_id, lock=True)
        options = _import_options(body, row, admin)
        if row["status"] not in ("uploaded", "dry_ready"):
            raise HTTPException(409, "Importen kan ikke tørkøres nu")
        job_id = await jobs.enqueue(conn, "data.import", {"transfer_id": transfer_id, "dry_run": True,
                                                          "options": options, "actor_id": admin.user_id},
                                    max_attempts=1, dedupe_key=f"data.import:{transfer_id}")
        await conn.execute("""update data_transfers set status = 'dry_running', options = %s, job_id = %s, error = null,
                              updated_at = now() where id = %s""", (Jsonb(options), job_id, transfer_id))
        return _public(await _get(conn, transfer_id))


@router.post("/{transfer_id}/run")
async def run_import(transfer_id: int, body: ImportIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = await _get(conn, transfer_id, lock=True)
        options = _import_options(body, row, admin)
        if row["status"] != "dry_ready" or row["dry_run_key"] != transfer.options_key(options):
            raise HTTPException(409, "Kør en tørkørsel med de samme optioner først")
        if (row["report"] or {}).get("blocked"):
            raise HTTPException(409, "Tørkørslen fandt fejl. Ret dem, eller vælg Fortsæt og rapportér.")
        if await db.fetch_one(conn, "select 1 from data_transfers where kind = 'import' and status in ('running', 'undoing')"):
            raise HTTPException(409, "En anden import kører. Vent til den er færdig.")
        job_id = await jobs.enqueue(conn, "data.import", {"transfer_id": transfer_id, "dry_run": False,
                                                          "options": options, "actor_id": admin.user_id},
                                    max_attempts=1, dedupe_key=f"data.import:{transfer_id}")
        await conn.execute("""update data_transfers set status = 'running', job_id = %s, error = null,
                              updated_at = now() where id = %s""", (job_id, transfer_id))
        await audit(conn, "transfer.import_requested", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=transfer_id, areas=options["areas"], conflict=options["conflict"], match=options["match"],
                    missing=options["missing"], on_error=options["on_error"])
        return _public(await _get(conn, transfer_id))


@router.post("/{transfer_id}/undo")
async def undo_import(transfer_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = await _get(conn, transfer_id, lock=True)
        if row["kind"] != "import" or row["status"] != "done" or row["undo_until"] is None:
            raise HTTPException(409, "Importen kan ikke fortrydes (for gammel, eller den er allerede fortrudt)")
        job_id = await jobs.enqueue(conn, "data.undo", {"transfer_id": transfer_id, "actor_id": admin.user_id},
                                    max_attempts=1, dedupe_key=f"data.undo:{transfer_id}")
        await conn.execute("update data_transfers set status = 'undoing', job_id = %s, updated_at = now() where id = %s",
                           (job_id, transfer_id))
        await audit(conn, "transfer.undo_requested", actor_id=admin.user_id, meta=admin.meta, table="data_transfers",
                    row_id=transfer_id)
        return _public(await _get(conn, transfer_id))

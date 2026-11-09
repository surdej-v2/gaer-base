"""Upload and ingestion for Viden: store the file, then a job extracts, chunks, embeds and indexes it.

    doc = await ingest.add_document(conn, space_id, path, data, actor_id=…, meta=…)   # stores + enqueues

Jobs (app.jobs, so they are retried, visible in /admin/jobs and safe with several containers):
- knowledge.ingest  one document. UnsupportedFormat → 'skipped', BrokenFile → 'failed' (not retried); any other
                    error marks the document 'error' and raises, so the job is retried with backoff.
- knowledge.bin     daily: spaces that have been in the bin for BIN_DAYS are deleted with their files.
"""

import hashlib
import io
import logging
import posixpath
import uuid
import zipfile

import httpx

from app import db, jobs
from app.knowledge import chunk as chunker
from app.knowledge import engine
from app.knowledge.extract import BrokenFile, UnsupportedFormat, content_type_for, extract, is_supported
from app.logs import RequestMeta, audit
from app.storage import get_storage
from app import config

log = logging.getLogger("gaer.knowledge")

BIN_DAYS = 30


class ZipError(Exception):
    pass


async def write_log(space_id: int, document_id: int | None, stage: str, message: str, level: str = "info") -> None:
    """In its own transaction, so a failing job still leaves its log line."""
    async with db.connection() as conn:
        await conn.execute("""insert into knowledge_ingest_log (space_id, document_id, level, stage, message)
                              values (%s, %s, %s, %s, %s)""", (space_id, document_id, level, stage, message[:2000]))


def expand_zip(data: bytes) -> list[tuple[str, bytes]]:
    """The files in a ZIP (paths cleaned, directories and hidden files skipped), within the configured limits."""
    s = config.settings
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ZipError("ZIP-filen er ødelagt") from e
    out, total = [], 0
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            path = posixpath.normpath(info.filename.replace("\\", "/")).lstrip("/")
            parts = path.split("/")
            if path.startswith("..") or any(p.startswith(".") or p == "__MACOSX" for p in parts):
                continue
            if len(out) >= s.knowledge_max_zip_files:
                raise ZipError(f"ZIP-filen har mere end {s.knowledge_max_zip_files} filer")
            total += info.file_size
            if total > s.knowledge_max_zip_mb * 1024 * 1024:
                raise ZipError(f"ZIP-filen fylder mere end {s.knowledge_max_zip_mb} MB udpakket")
            out.append((path[:500], zf.read(info)))
    return out


async def add_document(conn, space_id: int, path: str, data: bytes, *, actor_id: int | None, meta: RequestMeta) -> dict:
    """Stores the bytes in Object Storage, inserts a pending document and enqueues its ingest job — in conn's
    transaction. Object keys never contain user input; the name lives in the row."""
    filename = posixpath.basename(path) or "dokument"
    key = f"knowledge/{uuid.uuid4()}"
    await get_storage().put(key, data, content_type_for(filename))
    doc = await db.fetch_one(conn, """
        insert into knowledge_documents (space_id, filename, path, content_type, size_bytes, storage_key, sha256, uploaded_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning id, space_id, filename, path, size_bytes, status, created_at""",
        (space_id, filename[:300], path, content_type_for(filename), len(data), key,
         hashlib.sha256(data).hexdigest(), actor_id))
    await jobs.enqueue(conn, "knowledge.ingest", {"document_id": doc["id"]}, max_attempts=4,
                       dedupe_key=f"knowledge.ingest:{doc['id']}")
    await conn.execute("""insert into knowledge_ingest_log (space_id, document_id, stage, message)
                          values (%s, %s, 'queued', %s)""", (space_id, doc["id"], f"{path} er i kø"))
    await audit(conn, "file.upload", actor_id=actor_id, meta=meta, table="knowledge_documents", row_id=doc["id"],
                space_id=space_id, filename=filename, size=len(data))
    return doc


async def enqueue_retry(conn, document_id: int) -> None:
    await conn.execute("update knowledge_documents set status = 'pending', error = '', updated_at = now() where id = %s",
                       (document_id,))
    await jobs.enqueue(conn, "knowledge.ingest", {"document_id": document_id}, max_attempts=4,
                       dedupe_key=f"knowledge.ingest:{document_id}")


@jobs.handler("knowledge.ingest")
async def ingest_job(payload: dict, job: dict) -> dict | None:
    doc_id = int(payload["document_id"])
    async with db.connection() as conn:
        doc = await db.fetch_one(conn, """
            update knowledge_documents d set status = 'processing', error = '', updated_at = now()
            from knowledge_spaces s
            where d.id = %s and s.id = d.space_id and s.deleted_at is null
            returning d.id, d.space_id, d.filename, d.path, d.storage_key""", (doc_id,))
    if doc is None:
        return {"skipped": "document gone or space in the bin"}
    space_id = doc["space_id"]

    async def finish(status: str, error: str = "", **fields) -> None:
        sets = ", ".join(f"{k} = %s" for k in fields)
        async with db.connection() as conn:
            await conn.execute(
                f"update knowledge_documents set status = %s, error = %s, updated_at = now(){', ' + sets if sets else ''}"
                " where id = %s", (status, error[:1000], *fields.values(), doc_id))

    try:
        data, _ = await get_storage().get(doc["storage_key"])
        if not is_supported(doc["filename"]):
            raise UnsupportedFormat("Formatet kan ikke læses")
        result = extract(data, doc["filename"])
    except UnsupportedFormat as e:
        await finish("skipped", str(e))
        await write_log(space_id, doc_id, "skipped", f"{doc['path']}: {e}", "warn")
        return {"status": "skipped"}
    except BrokenFile as e:
        await finish("failed", str(e))
        await write_log(space_id, doc_id, "failed", f"{doc['path']}: {e} — prøves ikke igen", "error")
        return {"status": "failed"}
    except Exception as e:
        await finish("error", f"Udtræk fejlede: {e}")
        await write_log(space_id, doc_id, "retry", f"{doc['path']}: udtræk fejlede ({e}); prøves igen", "error")
        raise

    markdown = result.markdown.strip()
    await write_log(space_id, doc_id, "extract", f"{doc['path']}: {len(markdown):,} tegn"
                    + (f", {result.page_count} sider" if result.page_count else ""))
    pieces = chunker.chunk_markdown(markdown)
    await write_log(space_id, doc_id, "chunk", f"{doc['path']} → {len(pieces)} afsnit")

    vectors: list[list[float] | None] = [None] * len(pieces)
    model = None
    if pieces and engine.embeddings_enabled():
        try:
            vectors = await engine.embed([(p.section + "\n" + p.content).strip() for p in pieces])
            model = config.settings.knowledge_embed_model
            await write_log(space_id, doc_id, "embed", f"{len(pieces)} afsnit har fået vektorer ({model})")
        except (httpx.HTTPError, ValueError) as e:
            await write_log(space_id, doc_id, "embed", f"Ingen vektorer ({e}); fuldtekst bruges", "warn")

    async with db.connection() as conn:
        await conn.execute("delete from knowledge_chunks where document_id = %s", (doc_id,))
        await conn.execute("""insert into knowledge_texts (document_id, markdown) values (%s, %s)
                              on conflict (document_id) do update set markdown = excluded.markdown""", (doc_id, markdown))
        async with conn.cursor() as cur:
            await cur.executemany("""
                insert into knowledge_chunks (document_id, space_id, chunk_index, section, page, content, tokens, embedding, embed_model)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(doc_id, space_id, p.index, p.section, p.page, p.content, p.tokens, vectors[i], model if vectors[i] else None)
                 for i, p in enumerate(pieces)])
        await conn.execute("""update knowledge_documents set status = 'indexed', error = '', page_count = %s, chunk_count = %s,
                              char_count = %s, embedded = %s, indexed_at = now(), updated_at = now() where id = %s""",
                           (result.page_count, len(pieces), len(markdown), model is not None, doc_id))
        await conn.execute("update knowledge_spaces set updated_at = now() where id = %s", (space_id,))
    await write_log(space_id, doc_id, "done", f"{doc['path']} er indekseret")
    return {"status": "indexed", "chunks": len(pieces), "embedded": model is not None}


async def purge_space(space_id: int, *, actor_id: int | None, meta: RequestMeta) -> int:
    """Deletes a space, its documents, chunks and logs, and the files in Object Storage."""
    async with db.connection() as conn:
        keys = [r["storage_key"] for r in await db.fetch_all(
            conn, "select storage_key from knowledge_documents where space_id = %s", (space_id,))]
        row = await db.fetch_one(conn, "delete from knowledge_spaces where id = %s returning name", (space_id,))
        if row is None:
            return 0
        await audit(conn, "knowledge.purge", actor_id=actor_id, meta=meta, table="knowledge_spaces", row_id=space_id,
                    name=row["name"], files=len(keys))
    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # the row is gone; a stray object is harmless and logged
            log.warning("could not delete %s", key)
    return len(keys)


@jobs.handler("knowledge.bin")
async def bin_job(payload: dict, job: dict) -> dict | None:
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select id from knowledge_spaces
            where deleted_at is not null and deleted_at < now() - make_interval(days => %s)""", (BIN_DAYS,))
    files = 0
    for r in rows:
        files += await purge_space(r["id"], actor_id=None, meta=RequestMeta())
    return {"spaces": len(rows), "files": files}

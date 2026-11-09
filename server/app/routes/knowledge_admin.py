"""/api/admin/knowledge — Viden in /admin (specs/knowledge/README.md): vidensrum, documents and agents.

Guarded by require_admin at router level (section /knowledge in admin_roles). Every change is audited in the same
transaction. Uploads are stored in Object Storage and indexed by the knowledge.ingest job.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import ai, config, db
from app.knowledge import engine, ingest
from app.knowledge.extract import ext_of, is_supported
from app.logs import audit, changed
from app.sessions import Principal, require_admin
from app.storage import get_storage

router = APIRouter(prefix="/api/admin/knowledge", tags=["admin"], dependencies=[Depends(require_admin)])

SLUG = r"^[a-z0-9][a-z0-9-]{0,59}$"
DOC_FIELDS = """d.id, d.space_id, d.filename, d.path, d.size_bytes, d.status, d.error, d.page_count, d.chunk_count,
                d.char_count, d.embedded, d.created_at, d.indexed_at"""


def slugify(name: str) -> str:
    s = name.lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].strip("-")
    return s or "agent"


async def _space(conn, space_id: int, *, deleted: bool | None = False) -> dict:
    where = "" if deleted is None else (" and deleted_at is not null" if deleted else " and deleted_at is null")
    row = await db.fetch_one(conn, f"select * from knowledge_spaces where id = %s{where}", (space_id,))
    if row is None:
        raise HTTPException(404, "Vidensrummet findes ikke")
    return row


async def _agent(conn, agent_id: int, *, lock: bool = False) -> dict:
    row = await db.fetch_one(conn, "select * from knowledge_agents where id = %s" + (" for update" if lock else ""),
                             (agent_id,))
    if row is None:
        raise HTTPException(404, "Agenten findes ikke")
    return row


# ── Overview ───────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview():
    async with db.connection() as conn:
        spaces = await db.fetch_all(conn, """
            select s.id, s.name, s.description, s.created_at, s.updated_at, s.deleted_at,
                   count(d.id)::int as documents,
                   count(d.id) filter (where d.status = 'indexed')::int as indexed,
                   count(d.id) filter (where d.status in ('pending', 'processing'))::int as working,
                   count(d.id) filter (where d.status in ('failed', 'error'))::int as failed,
                   (select count(*)::int from knowledge_agents a where s.id = any(a.space_ids)) as agents
            from knowledge_spaces s left join knowledge_documents d on d.space_id = s.id
            group by s.id order by s.deleted_at nulls first, s.updated_at desc""")
        agents = await db.fetch_all(conn, """
            select id, slug, name, description, status, access, version_major, version_minor, space_ids, updated_at
            from knowledge_agents order by name""")
        stats = await db.fetch_one(conn, """
            select (select count(*)::int from knowledge_chunks) as chunks,
                   (select count(*)::int from knowledge_conversations where updated_at > now() - interval '30 days') as conversations_30d,
                   (select count(*)::int from knowledge_messages where role = 'user' and created_at > now() - interval '30 days') as questions_30d""")
    return {"spaces": [s for s in spaces if s["deleted_at"] is None], "bin": [s for s in spaces if s["deleted_at"]],
            "agents": agents, "stats": stats, "bin_days": ingest.BIN_DAYS,
            "ai": {"enabled": ai.enabled(), "embeddings": engine.embeddings_enabled(),
                   "embed_model": config.settings.knowledge_embed_model if engine.embeddings_enabled() else None}}


# ── Spaces ─────────────────────────────────────────────────────────────────

class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class SpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


@router.post("/spaces")
async def create_space(body: SpaceIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """insert into knowledge_spaces (name, description, created_by)
            values (%s, %s, %s) returning id, name, description, created_at""",
            (body.name.strip(), body.description.strip(), p.user_id))
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="knowledge_spaces", row_id=row["id"],
                    after={"name": row["name"]})
    return row


@router.get("/spaces/{space_id}")
async def get_space(space_id: int):
    async with db.connection() as conn:
        space = await _space(conn, space_id, deleted=None)
        docs = await db.fetch_all(conn, f"select {DOC_FIELDS} from knowledge_documents d where d.space_id = %s "
                                        "order by d.path, d.id", (space_id,))
        agents = await db.fetch_all(conn, """select id, slug, name, status, version_major, version_minor
            from knowledge_agents where %s = any(space_ids) order by name""", (space_id,))
    return {**space, "documents": docs, "agents": agents}


@router.patch("/spaces/{space_id}")
async def update_space(space_id: int, body: SpacePatch, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await _space(conn, space_id)
        after = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items()}
        if not after:
            return before
        row = await db.fetch_one(conn, """update knowledge_spaces set name = coalesce(%s, name),
            description = coalesce(%s, description), updated_at = now() where id = %s returning *""",
            (after.get("name"), after.get("description"), space_id))
        b, a = changed(before, after)
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="knowledge_spaces", row_id=space_id, before=b, after=a)
    return row


@router.delete("/spaces/{space_id}")
async def delete_space(space_id: int, p: Principal = Depends(require_admin)):
    """Moves the space to the bin. Queued ingest jobs skip it; it is purged after BIN_DAYS."""
    async with db.connection() as conn:
        space = await _space(conn, space_id)
        await conn.execute("update knowledge_spaces set deleted_at = now(), deleted_by = %s where id = %s",
                           (p.user_id, space_id))
        await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="knowledge_spaces", row_id=space_id,
                    name=space["name"], bin=True)
    return {"ok": True, "purge_after_days": ingest.BIN_DAYS}


@router.post("/spaces/{space_id}/restore")
async def restore_space(space_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        space = await _space(conn, space_id, deleted=True)
        await conn.execute("update knowledge_spaces set deleted_at = null, deleted_by = null where id = %s", (space_id,))
        pending = await db.fetch_all(conn, """select id from knowledge_documents
            where space_id = %s and status in ('pending', 'processing')""", (space_id,))
        for d in pending:
            await ingest.enqueue_retry(conn, d["id"])
        await audit(conn, "knowledge.restore", actor_id=p.user_id, meta=p.meta, table="knowledge_spaces", row_id=space_id,
                    name=space["name"], requeued=len(pending))
    return {"ok": True, "requeued": len(pending)}


@router.delete("/spaces/{space_id}/purge")
async def purge_space(space_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _space(conn, space_id, deleted=True)
    files = await ingest.purge_space(space_id, actor_id=p.user_id, meta=p.meta)
    return {"ok": True, "files": files}


@router.post("/spaces/{space_id}/upload")
async def upload(space_id: int, files: list[UploadFile], p: Principal = Depends(require_admin)):
    """One or more files; a .zip is expanded on the server and each supported file becomes a document."""
    limit = config.settings.max_upload_mb * 1024 * 1024
    entries: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    for f in files[:200]:
        data = await f.read(limit + 1)
        name = (f.filename or "fil").replace("\\", "/").rsplit("/", 1)[-1][:300]
        if len(data) > limit:
            raise HTTPException(413, f"{name} er større end {config.settings.max_upload_mb} MB")
        if ext_of(name) == "zip":
            try:
                inner = ingest.expand_zip(data)
            except ingest.ZipError as e:
                raise HTTPException(400, str(e)) from e
            for path, content in inner:
                if is_supported(path):
                    entries.append((path, content))
                else:
                    skipped.append(path)
        elif is_supported(name):
            entries.append((name, data))
        else:
            skipped.append(name)
    if not entries:
        raise HTTPException(400, "Ingen understøttede filer (PDF, Word, PowerPoint, Excel, CSV, HTML, Markdown eller tekst)")
    async with db.connection() as conn:
        await _space(conn, space_id)
        added = [await ingest.add_document(conn, space_id, path, content, actor_id=p.user_id, meta=p.meta)
                 for path, content in entries]
        await conn.execute("update knowledge_spaces set updated_at = now() where id = %s", (space_id,))
    return {"added": added, "skipped": skipped}


@router.get("/spaces/{space_id}/log")
async def space_log(space_id: int, limit: int = 200):
    async with db.connection() as conn:
        await _space(conn, space_id, deleted=None)
        return await db.fetch_all(conn, """select id, document_id, level, stage, message, created_at
            from knowledge_ingest_log where space_id = %s order by id desc limit %s""",
            (space_id, max(1, min(limit, 500))))


@router.post("/spaces/{space_id}/retry")
async def retry_space(space_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _space(conn, space_id)
        docs = await db.fetch_all(conn, "select id from knowledge_documents where space_id = %s and status = 'error'",
                                  (space_id,))
        for d in docs:
            await ingest.enqueue_retry(conn, d["id"])
        if docs:
            await audit(conn, "job.retry", actor_id=p.user_id, meta=p.meta, table="knowledge_spaces", row_id=space_id,
                        documents=len(docs))
    return {"requeued": len(docs)}


# ── Documents ──────────────────────────────────────────────────────────────

async def _document(conn, doc_id: int) -> dict:
    row = await db.fetch_one(conn, f"select {DOC_FIELDS}, d.storage_key from knowledge_documents d where d.id = %s", (doc_id,))
    if row is None:
        raise HTTPException(404, "Dokumentet findes ikke")
    return row


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int):
    async with db.connection() as conn:
        doc = await _document(conn, doc_id)
        text = await db.fetch_one(conn, "select markdown from knowledge_texts where document_id = %s", (doc_id,))
        chunks = await db.fetch_all(conn, """select chunk_index, section, page, tokens, embedding is not null as embedded,
            left(content, 400) as preview from knowledge_chunks where document_id = %s order by chunk_index limit 100""", (doc_id,))
    doc.pop("storage_key")
    md = (text or {}).get("markdown") or ""
    return {**doc, "markdown": md[:200_000], "truncated": len(md) > 200_000, "chunks": chunks}


@router.get("/documents/{doc_id}/link")
async def document_link(doc_id: int):
    async with db.connection() as conn:
        doc = await _document(conn, doc_id)
    return {"url": await get_storage().download_url(doc["storage_key"], doc["filename"])}


@router.post("/documents/{doc_id}/retry")
async def retry_document(doc_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        doc = await _document(conn, doc_id)
        await ingest.enqueue_retry(conn, doc_id)
        await audit(conn, "job.retry", actor_id=p.user_id, meta=p.meta, table="knowledge_documents", row_id=doc_id,
                    filename=doc["filename"])
    return {"ok": True}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        doc = await _document(conn, doc_id)
        await conn.execute("delete from knowledge_documents where id = %s", (doc_id,))
        await audit(conn, "file.delete", actor_id=p.user_id, meta=p.meta, table="knowledge_documents", row_id=doc_id,
                    space_id=doc["space_id"], filename=doc["filename"])
    await get_storage().delete(doc["storage_key"])
    return {"ok": True}


# ── Agents ─────────────────────────────────────────────────────────────────

class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, pattern=SLUG)
    space_ids: list[int] = Field(default_factory=list, max_length=50)


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, pattern=SLUG)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=20_000)
    welcome: str | None = Field(default=None, max_length=2000)
    suggested_prompts: list[str] | None = Field(default=None, max_length=12)
    space_ids: list[int] | None = Field(default=None, max_length=50)
    access: str | None = Field(default=None, pattern="^(all|members)$")


class PublishIn(BaseModel):
    kind: str = Field(default="minor", pattern="^(minor|major)$")
    note: str = Field(default="", max_length=500)


class MemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    privilege: str = Field(default="use", pattern="^(use|manage)$")


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


SNAPSHOT_FIELDS = ("name", "description", "instructions", "welcome", "suggested_prompts", "space_ids")


def snapshot_of(agent: dict) -> dict:
    return {k: agent[k] for k in SNAPSHOT_FIELDS}


async def _check_spaces(conn, space_ids: list[int]) -> list[int]:
    ids = sorted(set(space_ids))
    if ids:
        found = await db.fetch_all(conn, "select id from knowledge_spaces where id = any(%s) and deleted_at is null", (ids,))
        if len(found) != len(ids):
            raise HTTPException(400, "Et af vidensrummene findes ikke")
    return ids


async def _unique_slug(conn, base: str, agent_id: int | None = None) -> str:
    slug, n = base, 2
    while await db.fetch_one(conn, "select 1 as x from knowledge_agents where slug = %s and id is distinct from %s",
                             (slug, agent_id)):
        slug = f"{base[:56]}-{n}"
        n += 1
    return slug


@router.post("/agents")
async def create_agent(body: AgentIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        space_ids = await _check_spaces(conn, body.space_ids)
        slug = await _unique_slug(conn, body.slug or slugify(body.name))
        row = await db.fetch_one(conn, """insert into knowledge_agents (slug, name, space_ids, created_by)
            values (%s, %s, %s, %s) returning id, slug, name""", (slug, body.name.strip(), space_ids, p.user_id))
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="knowledge_agents", row_id=row["id"],
                    after={"name": row["name"], "slug": slug, "space_ids": space_ids})
    return row


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: int):
    async with db.connection() as conn:
        agent = await _agent(conn, agent_id)
        versions = await db.fetch_all(conn, """select v.id, v.major, v.minor, v.note, v.created_at, u.display_name as created_by
            from knowledge_agent_versions v left join users u on u.id = v.created_by
            where v.agent_id = %s order by v.major desc, v.minor desc""", (agent_id,))
        members = await db.fetch_all(conn, """select m.user_id, m.privilege, m.created_at, u.display_name, u.email
            from knowledge_agent_members m join users u on u.id = m.user_id
            where m.agent_id = %s order by coalesce(u.display_name, u.email)""", (agent_id,))
        latest = await db.fetch_one(conn, """select snapshot from knowledge_agent_versions where agent_id = %s
            order by major desc, minor desc limit 1""", (agent_id,))
    dirty = latest is None or latest["snapshot"] != {**snapshot_of(agent), "space_ids": list(agent["space_ids"])}
    return {**agent, "versions": versions, "members": members, "unpublished_changes": dirty}


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: int, body: AgentPatch, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await _agent(conn, agent_id, lock=True)
        after = body.model_dump(exclude_none=True)
        if "space_ids" in after:
            after["space_ids"] = await _check_spaces(conn, after["space_ids"])
        if "slug" in after and after["slug"] != before["slug"]:
            if await db.fetch_one(conn, "select 1 as x from knowledge_agents where slug = %s and id <> %s",
                                  (after["slug"], agent_id)):
                raise HTTPException(409, "Ruten er allerede i brug af en anden agent")
        if "suggested_prompts" in after:
            after["suggested_prompts"] = [s.strip()[:300] for s in after["suggested_prompts"] if s.strip()]
        for k in ("name", "description", "instructions", "welcome"):
            if k in after:
                after[k] = after[k].strip()
        if not after:
            return before
        sets = ", ".join(f"{k} = %s" for k in after)
        row = await db.fetch_one(conn, f"update knowledge_agents set {sets}, updated_at = now() where id = %s returning *",
                                 (*after.values(), agent_id))
        b, a = changed(before, after)
        for k in ("instructions",):  # long text: log the length, not the content
            if k in a:
                b[k], a[k] = len(b.get(k) or ""), len(a[k] or "")
        await audit(conn, "update", actor_id=p.user_id, meta=p.meta, table="knowledge_agents", row_id=agent_id, before=b, after=a)
    return row


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        agent = await _agent(conn, agent_id)
        await conn.execute("delete from knowledge_agents where id = %s", (agent_id,))
        await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="knowledge_agents", row_id=agent_id,
                    name=agent["name"], slug=agent["slug"])
    return {"ok": True}


@router.post("/agents/{agent_id}/publish")
async def publish_agent(agent_id: int, body: PublishIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        agent = await _agent(conn, agent_id, lock=True)
        major, minor = agent["version_major"], agent["version_minor"]
        major, minor = (major + 1, 0) if body.kind == "major" or major == 0 else (major, minor + 1)
        snap = {**snapshot_of(agent), "space_ids": list(agent["space_ids"])}
        await conn.execute("""insert into knowledge_agent_versions (agent_id, major, minor, snapshot, note, created_by)
            values (%s, %s, %s, %s, %s, %s)""", (agent_id, major, minor, Jsonb(snap), body.note.strip(), p.user_id))
        await conn.execute("""update knowledge_agents set status = 'published', version_major = %s, version_minor = %s,
            updated_at = now() where id = %s""", (major, minor, agent_id))
        await audit(conn, "knowledge.publish", actor_id=p.user_id, meta=p.meta, table="knowledge_agents", row_id=agent_id,
                    version=f"{major}.{minor}", note=body.note.strip())
    return {"version": f"{major}.{minor}"}


@router.post("/agents/{agent_id}/versions/{version_id}/promote")
async def promote_version(agent_id: int, version_id: int, p: Principal = Depends(require_admin)):
    """Copies a snapshot into the working copy and publishes it as a new minor version."""
    async with db.connection() as conn:
        agent = await _agent(conn, agent_id, lock=True)
        v = await db.fetch_one(conn, "select * from knowledge_agent_versions where id = %s and agent_id = %s",
                               (version_id, agent_id))
        if v is None:
            raise HTTPException(404, "Versionen findes ikke")
        snap = v["snapshot"]
        live = await db.fetch_all(conn, "select id from knowledge_spaces where id = any(%s) and deleted_at is null",
                                  ([int(x) for x in snap.get("space_ids") or []],))
        snap = {**snap, "space_ids": [r["id"] for r in live]}
        major, minor = agent["version_major"], agent["version_minor"] + 1
        await conn.execute("""update knowledge_agents set name = %s, description = %s, instructions = %s, welcome = %s,
            suggested_prompts = %s, space_ids = %s, status = 'published', version_major = %s, version_minor = %s,
            updated_at = now() where id = %s""",
            (snap["name"], snap["description"], snap["instructions"], snap["welcome"], snap["suggested_prompts"],
             snap["space_ids"], major, minor, agent_id))
        await conn.execute("""insert into knowledge_agent_versions (agent_id, major, minor, snapshot, note, created_by)
            values (%s, %s, %s, %s, %s, %s)""",
            (agent_id, major, minor, Jsonb(snap), f"Gendannet fra v{v['major']}.{v['minor']}", p.user_id))
        await audit(conn, "knowledge.promote", actor_id=p.user_id, meta=p.meta, table="knowledge_agents", row_id=agent_id,
                    source=f"{v['major']}.{v['minor']}", version=f"{major}.{minor}")
    return {"version": f"{major}.{minor}"}


@router.post("/agents/{agent_id}/preview")
async def preview(agent_id: int, body: AskIn, p: Principal = Depends(require_admin)):
    """Asks the working copy (not the published version) — for "Prøv agenten" in the builder. Not stored."""
    async with db.connection() as conn:
        agent = await _agent(conn, agent_id)
    return await engine.answer({**snapshot_of(agent), "space_ids": list(agent["space_ids"])}, body.question.strip(), p.user_id)


@router.put("/agents/{agent_id}/members")
async def add_member(agent_id: int, body: MemberIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _agent(conn, agent_id)
        user = await db.fetch_one(conn, "select id, display_name, email from users where email = %s",
                                  (body.email.strip().lower(),))
        if user is None:
            raise HTTPException(404, "Der er ingen bruger med den e-mail. Brugeren skal have logget ind én gang.")
        before = await db.fetch_one(conn, "select privilege from knowledge_agent_members where agent_id = %s and user_id = %s",
                                    (agent_id, user["id"]))
        await conn.execute("""insert into knowledge_agent_members (agent_id, user_id, privilege, added_by)
            values (%s, %s, %s, %s) on conflict (agent_id, user_id) do update set privilege = excluded.privilege""",
            (agent_id, user["id"], body.privilege, p.user_id))
        await audit(conn, "update" if before else "create", actor_id=p.user_id, meta=p.meta,
                    table="knowledge_agent_members", row_id=f"{agent_id}:{user['id']}",
                    before=before, after={"privilege": body.privilege})
    return {"user_id": user["id"], "display_name": user["display_name"], "email": user["email"], "privilege": body.privilege}


@router.delete("/agents/{agent_id}/members/{user_id}")
async def remove_member(agent_id: int, user_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """delete from knowledge_agent_members where agent_id = %s and user_id = %s
            returning privilege""", (agent_id, user_id))
        if row is None:
            raise HTTPException(404, "Ikke medlem")
        await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="knowledge_agent_members",
                    row_id=f"{agent_id}:{user_id}", before=row)
    return {"ok": True}

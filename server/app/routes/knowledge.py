"""/api/knowledge — the user side of Viden: published agents on /extension/agent/:slug.

A user may use a published agent when its access is 'all', when they are a member, or when they are an admin.
They chat with the latest published version (the snapshot), never the working copy. Conversations are the
user's own (scoped by user_id; GDPR export, cascade on erasure).
"""

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.knowledge import engine
from app.logs import audit
from app.sessions import Principal, current_user
from app.storage import get_storage

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


ACCESS = """a.status = 'published' and (a.access = 'all' or %(admin)s
            or exists (select 1 from knowledge_agent_members m where m.agent_id = a.id and m.user_id = %(uid)s))"""


async def _agent(conn, slug: str, p: Principal) -> dict:
    """The published agent with its latest snapshot, or 404 (also when the user has no access)."""
    row = await db.fetch_one(conn, f"""
        select a.id, a.slug, a.version_major, a.version_minor, v.snapshot,
               (%(admin)s or exists (select 1 from knowledge_agent_members m
                where m.agent_id = a.id and m.user_id = %(uid)s and m.privilege = 'manage')) as can_manage
        from knowledge_agents a
        join lateral (select snapshot from knowledge_agent_versions where agent_id = a.id
                      order by major desc, minor desc limit 1) v on true
        where a.slug = %(slug)s and {ACCESS}""", {"slug": slug, "uid": p.user_id, "admin": p.is_admin})
    if row is None:
        raise HTTPException(404, "Agenten findes ikke, eller du har ikke adgang til den")
    return row


def _profile(a: dict) -> dict:
    s = a["snapshot"]
    return {"slug": a["slug"], "name": s["name"], "description": s.get("description") or "",
            "welcome": s.get("welcome") or "", "suggested_prompts": s.get("suggested_prompts") or [],
            "version": f"{a['version_major']}.{a['version_minor']}", "can_manage": a["can_manage"]}


@router.get("/agents")
async def list_agents(p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select a.slug, a.version_major, a.version_minor, v.snapshot, false as can_manage
            from knowledge_agents a
            join lateral (select snapshot from knowledge_agent_versions where agent_id = a.id
                          order by major desc, minor desc limit 1) v on true
            where {ACCESS} order by a.name""", {"uid": p.user_id, "admin": p.is_admin})
    return [_profile(r) for r in rows]


@router.get("/agents/{slug}")
async def get_agent(slug: str, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return _profile(await _agent(conn, slug, p))


@router.post("/agents/{slug}/ask")
async def ask(slug: str, body: AskIn, p: Principal = Depends(current_user)):
    question = body.question.strip()
    async with db.connection() as conn:
        agent = await _agent(conn, slug, p)
        history: list[dict] = []
        if body.conversation_id is not None:
            conv = await db.fetch_one(conn, """select id from knowledge_conversations
                where id = %s and user_id = %s and agent_id = %s""", (body.conversation_id, p.user_id, agent["id"]))
            if conv is None:
                raise HTTPException(404, "Samtalen findes ikke")
            history = await db.fetch_all(conn, """select role, text from knowledge_messages
                where conversation_id = %s order by id""", (conv["id"],))
    result = await engine.answer(agent["snapshot"], question, p.user_id, history)
    version = f"{agent['version_major']}.{agent['version_minor']}"
    async with db.connection() as conn:
        if body.conversation_id is None:
            conv = await db.fetch_one(conn, """insert into knowledge_conversations (agent_id, user_id, title)
                values (%s, %s, %s) returning id""", (agent["id"], p.user_id, question[:120]))
            await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="knowledge_conversations",
                        row_id=conv["id"], agent=slug)
            conv_id = conv["id"]
        else:
            conv_id = body.conversation_id
            await conn.execute("update knowledge_conversations set updated_at = now() where id = %s", (conv_id,))
        await conn.execute("insert into knowledge_messages (conversation_id, role, text) values (%s, 'user', %s)",
                           (conv_id, question))
        msg = await db.fetch_one(conn, """insert into knowledge_messages (conversation_id, role, text, citations, agent_version)
            values (%s, 'agent', %s, %s, %s) returning id, role, text, citations, agent_version, created_at""",
            (conv_id, result["answer"], Jsonb(result["citations"]), version))
    return {"conversation_id": conv_id, "message": msg, "method": result["method"]}


@router.get("/agents/{slug}/conversations")
async def list_conversations(slug: str, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        agent = await _agent(conn, slug, p)
        return await db.fetch_all(conn, """
            select c.id, c.title, c.created_at, c.updated_at,
                   (select count(*)::int from knowledge_messages m where m.conversation_id = c.id) as messages
            from knowledge_conversations c where c.agent_id = %s and c.user_id = %s
            order by c.updated_at desc limit 100""", (agent["id"], p.user_id))


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: int, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        conv = await db.fetch_one(conn, """select id, title, created_at, updated_at from knowledge_conversations
            where id = %s and user_id = %s""", (conv_id, p.user_id))
        if conv is None:
            raise HTTPException(404, "Samtalen findes ikke")
        messages = await db.fetch_all(conn, """select id, role, text, citations, agent_version, created_at
            from knowledge_messages where conversation_id = %s order by id""", (conv_id,))
    return {**conv, "messages": messages}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: int, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from knowledge_conversations where id = %s and user_id = %s returning id",
                                 (conv_id, p.user_id))
        if row is None:
            raise HTTPException(404, "Samtalen findes ikke")
        await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="knowledge_conversations", row_id=conv_id)
    return {"ok": True}


@router.get("/agents/{slug}/documents/{doc_id}/link")
async def document_link(slug: str, doc_id: int, p: Principal = Depends(current_user)):
    """A short-lived link to a cited original — only for documents in the agent's published spaces."""
    async with db.connection() as conn:
        agent = await _agent(conn, slug, p)
        space_ids = [int(x) for x in agent["snapshot"].get("space_ids") or []]
        doc = await db.fetch_one(conn, """select d.filename, d.storage_key from knowledge_documents d
            join knowledge_spaces s on s.id = d.space_id
            where d.id = %s and d.space_id = any(%s) and s.deleted_at is null""", (doc_id, space_ids))
    if doc is None:
        raise HTTPException(404, "Dokumentet findes ikke")
    return {"url": await get_storage().download_url(doc["storage_key"], doc["filename"]), "filename": doc["filename"]}

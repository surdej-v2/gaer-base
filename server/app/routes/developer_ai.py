"""/api/admin/dev/ai: AI chat and suggestions in Gær Developer (specs/developer/AI.md).

Access: admin section "/developer", like the rest of /api/admin/dev. The chat uses the instance's AI layer
(app/ai.py) with surface "developer"; the server builds the context from {kind, id} (app/devkit/ai_context.py), so
only metadata goes to the model (AI-1). Every AI call passes consumption.check (AI-3). Suggestions are found by rules
(app/devkit/ai_suggestions.py) and work without AI (AI-4); dismissing one is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import ai, consumption, db, events, settings_store
from app.devkit import ai_context, ai_suggestions, ai_tools
from app.logs import audit
from app.sessions import Principal, require_admin

router = APIRouter(prefix="/api/admin/dev/ai", tags=["developer"], dependencies=[Depends(require_admin)])


class Ref(BaseModel):
    kind: str = Field(pattern="^(overview|artifact|dataset|run)$")
    id: int | None = Field(default=None, ge=1)


class DevChatIn(BaseModel):
    messages: list[ai.ChatMessage] = Field(min_length=1, max_length=60)
    ref: Ref | None = None
    page: str | None = Field(default=None, max_length=300)


@router.post("/chat")
async def chat(body: DevChatIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        if blocked := await consumption.check(conn, p.user_id):
            raise HTTPException(*blocked)
        app_name = await settings_store.get(conn, "app_name")
        ctx = await ai_context.build(conn, body.ref.kind, body.ref.id and str(body.ref.id), p.user_id) if body.ref else None
    chat_in = ai.ChatIn(surface="developer", messages=body.messages, page=body.page,
                        context=ctx.text if ctx else None, context_label=ctx.label if ctx else None)
    who = ai.SignedIn(name=p.display_name, role=p.role, impersonated=p.impersonator_id is not None)
    return StreamingResponse(ai.stream_chat(chat_in, p.user_id, app_name, who), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/context")
async def context(kind: str = Query(pattern="^(overview|artifact|dataset|run)$"), id: int | None = Query(default=None, ge=1),
                  text: bool = False, p: Principal = Depends(require_admin)):
    """What the AI knows about {kind, id}: the label and chips for "Kender:" in the panel, and — for the tool
    context_get — the same metadata text the chat sends."""
    async with db.connection() as conn:
        ctx = await ai_context.build(conn, kind, id and str(id), p.user_id)
    return {"label": ctx.label, "chips": ctx.chips, **({"text": ctx.text} if text else {})}


@router.get("/search")
async def search(q: str = Query(min_length=2, max_length=80)):
    """The tool catalog_search: datasets and artifacts as metadata."""
    async with db.connection() as conn:
        return {"items": await ai_tools.search(conn, q)}


@router.get("/suggestions")
async def suggestions(scope: str = Query(default="overview", max_length=40), p: Principal = Depends(require_admin)):
    try:
        ai_suggestions.parse_scope(scope)
    except ValueError:
        raise HTTPException(400, "Ukendt scope") from None
    async with db.connection() as conn:
        items = await ai_suggestions.for_user(conn, p.user_id, scope)
    return {"items": items, "ai": ai.enabled()}


class DismissIn(BaseModel):
    key: str = Field(min_length=1, max_length=ai_suggestions.MAX_KEY, pattern=r"^[a-z_]+:[A-Za-z0-9_:.\-]*$")


@router.post("/suggestions/dismiss", status_code=204)
async def dismiss(body: DismissIn, p: Principal = Depends(require_admin)):
    kind = body.key.split(":", 1)[0]
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            insert into dev_ai_dismissals (user_id, key, kind) values (%s, %s, %s)
            on conflict (user_id, key) do nothing returning key""", (p.user_id, body.key, kind))
        if row:
            await audit(conn, "dev.ai.suggestion.dismissed", actor_id=p.user_id, meta=p.meta, table="dev_ai_dismissals",
                        row_id=body.key, after={"key": body.key, "kind": kind})
    if row:
        await events.emit("developer.ai.dismissed", ref=kind)


@router.delete("/suggestions/dismiss/{key}", status_code=204)
async def restore(key: str, p: Principal = Depends(require_admin)):
    """Fortryd: the suggestion shows again."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from dev_ai_dismissals where user_id = %s and key = %s returning key, kind",
                                 (p.user_id, key[:ai_suggestions.MAX_KEY]))
        if row is None:
            raise HTTPException(404, "Forslaget er ikke afvist")
        await audit(conn, "dev.ai.suggestion.restored", actor_id=p.user_id, meta=p.meta, table="dev_ai_dismissals",
                    row_id=row["key"], before={"key": row["key"], "kind": row["kind"]})

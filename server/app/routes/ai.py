"""/api/ai — the optional chat used by /extension, the Office tools and /m. See app/ai.py."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import ai, db, settings_store
from app.sessions import Principal, current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def status(p: Principal = Depends(current_user)):
    if not ai.enabled():
        return {"enabled": False}
    async with db.connection() as conn:
        limit = await ai.limit_for(conn, p.user_id)
        used = await ai.used_this_month(conn, p.user_id)
    return {"enabled": limit > 0, "used": used, "limit": limit}


@router.post("/chat")
async def chat(body: ai.ChatIn, p: Principal = Depends(current_user)):
    if not ai.enabled():
        raise HTTPException(404, "AI er ikke slået til på denne instans")
    async with db.connection() as conn:
        limit = await ai.limit_for(conn, p.user_id)
        used = await ai.used_this_month(conn, p.user_id)
        app_name = await settings_store.get(conn, "app_name")
    if limit <= 0:
        raise HTTPException(403, "AI er ikke slået til for din bruger")
    if used >= limit:
        raise HTTPException(429, "Du har brugt din AI-kvote for denne måned")
    return StreamingResponse(ai.stream_chat(body, p.user_id, app_name), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

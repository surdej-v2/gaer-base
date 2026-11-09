"""Embeddings, retrieval and grounded answers for Viden.

Retrieval uses vectors when the agent's spaces have embedded chunks and an embedding model is set up
(KNOWLEDGE_EMBED_MODEL + AI_API_KEY); otherwise — or if the embedding call fails — Postgres full-text search.
Embeddings are stored at unit length in a real[] column, so cosine similarity is a dot product in SQL and no
extension (pgvector) is needed.

Answers are written by AI_MODEL with the agent's instructions plus a fixed grounding rule the instructions cannot
remove. Without AI the agent answers with the best excerpts, so Viden is useful (and testable) without a provider.
"""

import math
import re

import httpx
from fastapi import HTTPException

from app import ai, config, db

TOP_K = 8
MIN_SIMILARITY = 0.2
MAX_EXCERPT_CHARS = 1800
EMBED_BATCH = 32

GROUNDING = ("Brug KUN de nummererede uddrag nedenfor. Henvis til dem i teksten som [1], [2] … "
             "Står svaret ikke i uddragene, så sig det ærligt — opfind aldrig fakta.")

STOPWORDS = set("""
og i at er det en den til på som de med for af har ikke der var jeg kan skal vil om hvad hvor hvordan hvem
min mit mine din dit dine vi du man så også eller men fra ved efter når være blive bliver får have
the a an and or of to in on for is are be with what how when where who can do does i my you your
""".split())


def embeddings_enabled() -> bool:
    return ai.enabled() and bool(config.settings.knowledge_embed_model)


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed(texts: list[str]) -> list[list[float]]:
    """Unit-length embeddings for texts, in batches. Raises httpx.HTTPError / ValueError on failure."""
    s = config.settings
    out: list[list[float]] = []
    async with httpx.AsyncClient(transport=ai.transport, timeout=httpx.Timeout(120, connect=10)) as client:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = [t[:8000] for t in texts[i:i + EMBED_BATCH]]
            r = await client.post(f"{s.ai_base_url.rstrip('/')}/embeddings",
                                  json={"model": s.knowledge_embed_model, "input": batch},
                                  headers={"Authorization": f"Bearer {s.ai_api_key}"})
            if r.status_code >= 300:
                raise ValueError(f"Embedding-tjenesten svarede {r.status_code}")
            data = sorted(r.json().get("data") or [], key=lambda d: d.get("index", 0))
            if len(data) != len(batch):
                raise ValueError("Embedding-tjenesten returnerede et forkert antal vektorer")
            out += [_normalize([float(x) for x in d["embedding"]]) for d in data]
    return out


def fulltext_query(question: str) -> str | None:
    words = []
    for w in re.findall(r"\w{2,}", question.lower()):
        if w not in STOPWORDS and w not in words:
            words.append(w)
    return " | ".join(f"{w}:*" for w in words[:20]) or None


_SELECT = """
    select c.id, c.document_id, c.space_id, c.section, c.page, c.content, d.filename, s.name as space_name, {score} as score
    from knowledge_chunks c
    join knowledge_documents d on d.id = c.document_id
    join knowledge_spaces s on s.id = c.space_id
    where c.space_id = any(%s) and s.deleted_at is null and {where}
    order by score desc, c.id limit %s"""


async def retrieve(conn, space_ids: list[int], question: str) -> tuple[list[dict], str]:
    """The best chunks for a question in these spaces, and the method used ('vector' or 'fulltext')."""
    if not space_ids:
        return [], "none"
    if embeddings_enabled():
        has_vectors = await db.fetch_one(conn, """select 1 as x from knowledge_chunks
            where space_id = any(%s) and embedding is not null limit 1""", (space_ids,))
        if has_vectors:
            try:
                [qv] = await embed([question])
            except (httpx.HTTPError, ValueError):
                qv = None
            if qv is not None:
                rows = await db.fetch_all(conn, _SELECT.format(
                    score="(select sum(a * b) from unnest(c.embedding, %s::real[]) as t(a, b))",
                    where="c.embedding is not null and array_length(c.embedding, 1) = %s"),
                    (qv, space_ids, len(qv), TOP_K))
                rows = [r for r in rows if r["score"] is not None and r["score"] >= MIN_SIMILARITY]
                if rows:
                    return rows, "vector"
    q = fulltext_query(question)
    if not q:
        return [], "fulltext"
    rows = await db.fetch_all(conn, _SELECT.format(
        score="ts_rank_cd(c.tsv, to_tsquery('simple', %s))", where="c.tsv @@ to_tsquery('simple', %s)"),
        (q, space_ids, q, TOP_K))
    return rows, "fulltext"


def citations_for(rows: list[dict]) -> list[dict]:
    """One citation per excerpt number; the UI groups them by document."""
    return [{"n": i, "document_id": r["document_id"], "filename": r["filename"], "space_id": r["space_id"],
             "section": r["section"] or None, "page": r["page"]} for i, r in enumerate(rows, start=1)]


def _excerpt(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


async def _chat(messages: list[dict], user_id: int) -> str:
    s = config.settings
    async with db.connection() as conn:
        if await ai.used_this_month(conn, user_id) >= await ai.limit_for(conn, user_id):
            raise HTTPException(429, "Du har brugt din AI-kvote for denne måned.")
    payload = {"model": s.ai_model, "messages": messages, "max_tokens": s.ai_max_output_tokens, "temperature": 0.2}
    try:
        async with httpx.AsyncClient(transport=ai.transport, timeout=httpx.Timeout(120, connect=10)) as client:
            r = await client.post(f"{s.ai_base_url.rstrip('/')}/chat/completions", json=payload,
                                  headers={"Authorization": f"Bearer {s.ai_api_key}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, "AI-tjenesten kunne ikke nås.") from e
    if r.status_code >= 300:
        raise HTTPException(502, f"AI-tjenesten svarede {r.status_code}.")
    body = r.json()
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = body.get("usage") or {}
    estimated = not usage
    prompt = int(usage.get("prompt_tokens", 0)) if usage else sum(len(m["content"]) for m in messages) // 4
    completion = int(usage.get("completion_tokens", 0)) if usage else len(text) // 4
    await ai.record_usage(user_id, "knowledge", prompt, completion, estimated)
    return text.strip()


async def answer(snapshot: dict, question: str, user_id: int, history: list[dict] | None = None) -> dict:
    """Answers a question as the agent described by snapshot (a published version or the draft)."""
    space_ids = [int(x) for x in snapshot.get("space_ids") or []]
    async with db.connection() as conn:
        spaces = await db.fetch_all(conn, """select id, name from knowledge_spaces
            where id = any(%s) and deleted_at is null order by name""", (space_ids,))
        live = [s["id"] for s in spaces]
        rows, method = await retrieve(conn, live, question) if live else ([], "none")
    names = ", ".join(s["name"] for s in spaces)
    if not spaces:
        return {"answer": "Agenten har endnu ingen vidensrum. Tilføj et vidensrum i /admin/knowledge, og spørg igen.",
                "citations": [], "method": "none"}
    if not rows:
        return {"answer": f"Jeg kunne ikke finde noget om det i {names}. Prøv at formulere spørgsmålet anderledes, "
                          "eller tjek at de rigtige dokumenter er uploadet og indekseret.", "citations": [], "method": method}

    citations = citations_for(rows)
    if not ai.enabled():
        best = rows[:3]
        lines = [f"[{i}] {_excerpt(r['content'], 500)}" for i, r in enumerate(best, start=1)]
        return {"answer": f"Her er det mest relevante, jeg fandt i {names}:\n\n" + "\n\n".join(lines),
                "citations": citations[:3], "method": method}

    context = "\n\n".join(
        f"[{i}] {r['filename']}{' · ' + r['section'] if r['section'] else ''}{f' · side {r['page']}' if r['page'] else ''} "
        f"({r['space_name']}):\n{_excerpt(r['content'], MAX_EXCERPT_CHARS)}" for i, r in enumerate(rows, start=1))
    instructions = (snapshot.get("instructions") or "").strip()
    identity = instructions or (f"Du er {snapshot.get('name') or 'en agent'}. Svar kort og præcist, "
                                "på det sprog spørgsmålet er stillet på.")
    messages = [{"role": "system", "content": f"{identity}\n\n{GROUNDING}"}]
    for m in (history or [])[-6:]:
        messages.append({"role": "assistant" if m["role"] == "agent" else "user", "content": m["text"][:4000]})
    messages.append({"role": "user", "content": f"Uddrag:\n\n{context}\n\nSpørgsmål: {question}"})
    text = await _chat(messages, user_id)
    used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return {"answer": text, "citations": [c for c in citations if c["n"] in used] or citations[:3], "method": method}

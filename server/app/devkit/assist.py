"""AI in Gær Developer: suggestions and analyses through the instance's own AI layer (app/ai.py settings).

Everything here returns None (or an empty suggestion) when AI is off, and every caller works without it: the UI
hides the AI buttons when /api/ai/status says enabled: false, and the heuristic fallbacks below keep the studios
usable. Only metadata from the catalog and rows that are already masked are ever sent (F-8, 2.2).
"""

import json
import re

import httpx

from app import ai, config, integrations


def enabled() -> bool:
    return ai.enabled()


async def complete(system: str, user: str, *, user_id: int | None = None, max_tokens: int = 900) -> str | None:
    """One non-streaming chat completion. None when AI is off or the provider fails (callers fall back)."""
    if not enabled():
        return None
    s = config.settings
    payload = {"model": s.ai_model, "temperature": 0.1, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user[:24000]}]}
    try:
        async with httpx.AsyncClient(transport=ai.transport, timeout=httpx.Timeout(60, connect=10),
                                     event_hooks=integrations.hooks("ai")) as client:
            r = await client.post(f"{s.ai_base_url.rstrip('/')}/chat/completions", json=payload,
                                  headers={"Authorization": f"Bearer {s.ai_api_key}"})
    except httpx.HTTPError:
        return None
    if r.status_code >= 300:
        return None
    body = r.json()
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = body.get("usage") or {}
    if user_id:
        await ai.record_usage(user_id, "developer", int(usage.get("prompt_tokens", len(user) // 4)),
                              int(usage.get("completion_tokens", len(text) // 4)), not usage)
    return text.strip() or None


def _json(text: str | None):
    if not text:
        return None
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


# ── Heuristics used with and without AI ─────────────────────────────────────

WORD = re.compile(r"[a-zæøå0-9]+")


def tokens(text: str) -> set[str]:
    words = set(WORD.findall((text or "").lower().replace("_", " ")))
    return {w for w in words if len(w) > 2} | {w[:-1] for w in words if len(w) > 4 and w.endswith(("r", "e"))}


def rank_datasets(description: str, datasets: list[dict]) -> list[dict]:
    """DS-5 without AI: datasets whose names, descriptions and fields share words with the description."""
    want = tokens(description)
    out = []
    for d in datasets:
        have = tokens(" ".join([d.get("name", ""), d.get("key", ""), d.get("description", ""),
                                " ".join(d.get("field_names") or [])]))
        score = len(want & have)
        if score:
            out.append({"id": d["id"], "name": d["name"], "score": score,
                        "relevance": "Høj relevans" if score >= 2 else "Mulig",
                        "why": ", ".join(sorted(want & have))[:120]})
    return sorted(out, key=lambda x: -x["score"])[:8]


def similar_field(name: str, fields: list[dict]) -> dict | None:
    """F-8 without AI: the classified field whose name shares the most with a new field's name."""
    want = tokens(name)
    best, score = None, 0
    for f in fields:
        s = len(want & tokens(f["name"] + " " + (f.get("label") or "")))
        if s > score:
            best, score = f, s
    return best


# ── AI functions ────────────────────────────────────────────────────────────

SYSTEM = ("Du er assistent i Gær Developer. Du får kun metadata fra datakataloget (navne, typer, klassifikation) "
          "og maskerede eksempler. Svar kort og på dansk.")


async def suggest_sources(description: str, datasets: list[dict], user_id: int) -> list[dict]:
    """DS-5: datasets for a description, with a relevance. Falls back to rank_datasets."""
    fallback = rank_datasets(description, datasets)
    meta = [{"id": d["id"], "navn": d["name"], "beskrivelse": d.get("description", "")[:200],
             "felter": (d.get("field_names") or [])[:30]} for d in datasets[:80]]
    text = await complete(SYSTEM + " Svar som JSON: [{\"id\": 1, \"relevance\": \"Høj relevans\"|\"Mulig\", \"why\": \"…\"}].",
                          f"Beskrivelse: {description}\n\nDatasæt: {json.dumps(meta, ensure_ascii=False)}", user_id=user_id)
    parsed = _json(text)
    if not isinstance(parsed, list):
        return fallback
    by_id = {d["id"]: d for d in datasets}
    out = [{"id": p["id"], "name": by_id[p["id"]]["name"], "relevance": p.get("relevance") or "Mulig",
            "why": str(p.get("why") or "")[:200], "ai": True}
           for p in parsed if isinstance(p, dict) and p.get("id") in by_id]
    return out or fallback


async def suggest_classification(field: dict, catalog_fields: list[dict], user_id: int | None) -> dict:
    """F-8: a classification for a new field: the most similar classified field, refined by AI when it is on."""
    like = similar_field(field["name"], catalog_fields)
    base = {"classification": like["classification"] if like else "yellow",
            "personal_data": bool(like and like.get("personal_data")),
            "why": f"Ligner {like['name']}" if like else "Ukendt felt: gul indtil det er klassificeret"}
    text = await complete(SYSTEM + " Svar som JSON {\"classification\": \"white|green|yellow|red\", \"personal_data\": bool, \"why\": \"…\"}.",
                          json.dumps({"felt": field, "lignende": like}, ensure_ascii=False, default=str), user_id=user_id)
    parsed = _json(text)
    if isinstance(parsed, dict) and parsed.get("classification") in ("white", "green", "yellow", "red"):
        return {"classification": parsed["classification"], "personal_data": bool(parsed.get("personal_data")),
                "why": str(parsed.get("why") or "")[:200], "ai": True}
    return base


async def suggest_pipeline(description: str, datasets: list[dict], user_id: int) -> list[dict] | None:
    """PL-3: stages for a description. None without AI."""
    meta = [{"key": d["key"], "navn": d["name"], "felter": (d.get("field_names") or [])[:30]} for d in datasets[:60]]
    text = await complete(SYSTEM + " Foreslå en pipeline som JSON-liste af trin i formatet "
                          "[{\"source\": \"datasæt-key\"}, {\"match\": {\"felt\": værdi}}, {\"lookup\": {\"from\": …, \"local\": …, "
                          "\"foreign\": …, \"fields\": […]}}, {\"group\": {\"by\": …, \"navn\": \"count\"|\"sum(felt)\"}}, "
                          "{\"project\": […]}, {\"sort\": {\"felt\": 1}}, {\"limit\": 10}]. Kun JSON.",
                          f"Beskrivelse: {description}\n\nDatasæt: {json.dumps(meta, ensure_ascii=False)}", user_id=user_id)
    parsed = _json(text)
    return parsed if isinstance(parsed, list) else None


async def check_description(tool: dict, user_id: int) -> dict:
    """MCP-7: is the tool's description clear enough for an AI client? Heuristic without AI."""
    desc = (tool.get("description") or "").strip()
    problems = []
    if len(desc) < 25:
        problems.append("Beskrivelsen er meget kort. Sig hvad toolet returnerer, og hvornår det skal bruges")
    if not tool.get("examples"):
        problems.append("Tilføj mindst ét eksempel på et kald")
    text = await complete(SYSTEM + " Vurdér om beskrivelsen af et MCP-tool er tydelig nok for en AI-klient. "
                          "Svar som JSON {\"ok\": bool, \"problems\": [\"…\"]}.",
                          json.dumps(tool, ensure_ascii=False, default=str), user_id=user_id, max_tokens=400)
    parsed = _json(text)
    if isinstance(parsed, dict) and "ok" in parsed:
        return {"ok": bool(parsed["ok"]) and not problems, "problems": problems + [str(p) for p in parsed.get("problems") or []][:5],
                "ai": True}
    return {"ok": not problems, "problems": problems, "ai": False}


async def impact(cause: dict | None, affected: dict | None, impact_data: dict) -> str | None:
    """F-6: what a major in one artifact means for one that uses it."""
    if cause is None or affected is None:
        return None
    return await complete(SYSTEM + " Beskriv i højst fem punkter, hvad ændringen betyder for det berørte artefakt, "
                          "og hvad ejeren skal tjekke.",
                          json.dumps({"ændringer": cause.get("changes"), "ny_kontrakt": cause.get("contract"),
                                      "berørt_definition": affected.get("definition"), "bruger": impact_data.get("uses")},
                                     ensure_ascii=False, default=str), max_tokens=600)

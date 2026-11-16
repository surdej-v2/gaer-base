"""The AI's tools in Gær Developer (specs/developer/AI.md). Like every tool in app/ai.py they are *client tools*: the
model asks, the browser runs them and sends the result back in the next request.

- Reading tools call /api/admin/dev/ai/… with the user's own session, so the same access rules apply, and get the
  same metadata the context has (app/devkit/ai_context.py) — never values.
- Proposal tools put a draft in the open editor. The browser shows it and the user saves it with the studio's own
  endpoints (which audit). The AI never saves anything itself (AI-2).
"""

from app import ai, db

_fn = ai._fn

TOOLS = [
    _fn("catalog_search", "Søg i datakataloget og blandt artefakterne (pipelines, MCP-endpoints, API'er, flows) på navn og "
        "beskrivelse. Giver id, type, navn og klassifikation — aldrig data.",
        {"q": {"type": "string", "description": "Søgeord, fx lejemål eller kunder"}}, ["q"]),
    _fn("context_get", "Hent metadata om et artefakt, et datasæt eller en pipeline-kørsel: felter med type og "
        "klassifikation, definition, versioner, seneste kørsler og fejl (uden værdier).",
        {"kind": {"type": "string", "enum": ["artifact", "dataset", "run"]},
         "id": {"type": "integer", "description": "Id fra catalog_search eller konteksten"}}, ["kind", "id"]),
    _fn("propose_pipeline_change", "Foreslå en ny version af en pipeline som YAML. Brugeren ser forskellen i editoren og "
        "gemmer selv; serveren afgør, om det er en patch, minor eller major.",
        {"artifact_id": {"type": "integer"}, "yaml": {"type": "string", "description": "Hele pipelinen i YAML"},
         "summary": {"type": "string", "description": "Hvad ændringen gør, én sætning"}}, ["artifact_id", "yaml", "summary"]),
    _fn("propose_testcases", "Foreslå testcases til et MCP-endpoint. Skriv spørgsmål uden personoplysninger. Brugeren "
        "vælger, hvilke der gemmes.",
        {"endpoint": {"type": "string", "description": "Endpointets nøgle, fx ejendomme"},
         "cases": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "question": {"type": "string"}, "tool": {"type": "string"},
             "args": {"type": "object"}, "expect": {"type": "object"}}, "required": ["name"]}}},
        ["endpoint", "cases"]),
    _fn("propose_doc_text", "Foreslå en tekst til et API's dokumentation. Den udgives først, når en ejer har godkendt den.",
        {"artifact_id": {"type": "integer"}, "section": {"type": "string", "description": "Fx intro, operation:GET /kunder/{id}, migration, errors"},
         "text": {"type": "string", "description": "Teksten i Markdown"}}, ["artifact_id", "section", "text"]),
    _fn("propose_flow_steps", "Foreslå udløser og trin til et flow i Action-studie som JSON. Brugeren tager trinene ét ad gangen.",
        {"artifact_id": {"type": "integer", "description": "Flowets id, eller udelad for et nyt flow"},
         "definition": {"type": "string", "description": "Flowets definition som JSON (trigger og steps)"},
         "summary": {"type": "string"}}, ["definition", "summary"]),
]

ai.TOOLS["developer"] = TOOLS


async def search(conn, q: str, limit: int = 20) -> list[dict]:
    """catalog_search: datasets and artifacts whose name, key or description match, as metadata."""
    like = f"%{q.strip()[:80]}%"
    datasets = await db.fetch_all(conn, """
        select 'dataset' as kind, id, name, key as slug, classification, left(description, 200) as description
        from datasets where name ilike %s or key ilike %s or description ilike %s order by name limit %s""",
        (like, like, like, limit))
    artifacts = await db.fetch_all(conn, """
        select 'artifact' as kind, id, type, name, slug, status, left(description, 200) as description
        from dev_artifacts where type not in ('dataset', 'api_key') and (name ilike %s or slug ilike %s or description ilike %s)
        order by name limit %s""", (like, like, like, limit))
    return [dict(r) for r in datasets] + [dict(r) for r in artifacts]

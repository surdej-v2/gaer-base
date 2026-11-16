"""The API documentation's texts (specs/developer/AI.md, fase 4).

Per API and major there is a text for each section: 'intro' (Kom i gang), 'operation:<METHOD> <path>', 'migration' (from
the major before) and 'errors' (Fejl og grænser). Rules write every text from what is known — the definition, the
contract, the contract diff to the previous major, dev_api_log counts per status — so the documentation exists without
AI (AI-4). AI, when on, rewrites the rule texts in better Danish from the same facts and is told not to add any. A text
is a draft until an owner (A or R) of the API approves it; only approved texts are published, in the OpenAPI document
(apply()). The error section uses counts per status only: never keys, IP addresses, user ids or concrete paths.
"""

import json

from fastapi import HTTPException

from app import db, jobs
from app.devkit import apis, artifacts, assist, contracts, masking
from app.logs import RequestMeta, audit

ERRORS_DA = {
    400: "Forespørgslen er ugyldig. Tjek parametrene mod dokumentationen.",
    401: "Nøglen mangler eller er ugyldig. Send den som Authorization: Bearer <nøgle>.",
    403: "Nøglen dækker ikke operationen. Bed nøglens ejer om at tilføje den til nøglens scopes.",
    404: "Stien findes ikke i denne major.",
    405: "Metoden findes ikke på stien.",
    410: "Kaldet går til en major, der er lukket. Skift til den nyeste major.",
    422: "En parameter har en forkert type eller værdi.",
    429: "For mange kald. Vent det antal sekunder, Retry-After siger, før næste kald.",
}
MAX_TEXT = 8000


def op_section(o: dict) -> str:
    return f"operation:{apis.op_key(o)}"


def titles(definition: dict, has_migration: bool, prev_major: int | None) -> list[tuple[str, str]]:
    out = [("intro", "Kom i gang")] + [(op_section(o), apis.op_key(o)) for o in definition.get("operations") or []]
    if has_migration:
        out.append(("migration", f"Skift fra v{prev_major}"))
    return out + [("errors", "Fejl og grænser")]


# ── The rule texts ──────────────────────────────────────────────────────────

def _fields(o: dict) -> list[dict]:
    return (o.get("_definition") or {}).get("_output") or []


def _intro(a: dict, v: dict) -> str:
    d = v["definition"]
    ops = d.get("operations") or []
    masked = sorted({f["name"] for o in ops for f in _fields(o)
                     if masking.default_for({"classification": f.get("classification"), "personal_data": f.get("personal")}) != "show"})
    parts = [(d.get("description") or a.get("description") or a["name"]).strip().rstrip(".") + ".",
             f"Alle kald går til /gaer/v{v['major']}/{a['slug']} og kræver en API-nøgle i headeren Authorization (Bearer). "
             "En nøgle kan kun kalde de operationer, den har scope til.",
             f"API'et har {len(ops)} {'operation' if len(ops) == 1 else 'operationer'}: {', '.join(apis.op_key(o) for o in ops)}."]
    if masked:
        parts.append(f"Felterne {', '.join(masked)} maskeres efter deres klassifikation.")
    return " ".join(parts)


def _operation(o: dict) -> str:
    od = o.get("_definition") or {}
    params = od.get("params") or []
    parts = [(o.get("summary") or apis.op_key(o)).strip().rstrip(".") + "."]
    if params:
        parts.append("Parametre: " + ", ".join(f"{p['name']}{' (påkrævet)' if p.get('required') else ''}" for p in params) + ".")
    fields = _fields(o)
    if fields:
        parts.append("Svaret har " + ", ".join(f["name"] for f in fields) + " i data.")
    hidden = [f["name"] for f in fields if masking.default_for({"classification": f.get("classification"),
                                                                "personal_data": f.get("personal")}) != "show"]
    if hidden:
        parts.append(f"{', '.join(hidden)} maskeres.")
    if o["method"] == "GET":
        parts.append(f"Højst {od.get('max_limit') or 200} rækker pr. kald; brug limit og offset for flere.")
    if o["method"] != "GET" and o.get("level") in ("yellow", "red"):
        parts.append("Send en Idempotency-Key, så et kald, der sendes igen, kun udføres én gang.")
    return " ".join(parts)


async def _migration(conn, a: dict, v: dict) -> tuple[str | None, int | None, list[str]]:
    """From the published version of the major before (if any): the contract diff, its sunset and the keys still on it."""
    prev = await db.fetch_one(conn, """select m.major, m.sunset_at, ver.contract from dev_api_majors m
                                       left join dev_artifact_versions ver on ver.id = m.version_id
                                       where m.api_artifact_id = %s and m.major < %s order by m.major desc limit 1""",
                              (a["id"], v["major"]))
    if prev is None:
        return None, None, []
    diff = contracts.diff(prev["contract"], v["contract"])
    keys = await db.fetch_all(conn, """select distinct k.name from dev_api_log l join dev_api_keys k on k.key_id = l.key_id
                                       where l.api = %s and l.major = %s and l.ts > now() - interval '30 days' order by k.name""",
                              (a["slug"], prev["major"]))
    lines = [f"Skift stien fra /gaer/v{prev['major']}/{a['slug']} til /gaer/v{v['major']}/{a['slug']}. Nøglerne virker uændret."]
    lines += [c["text"] + "." for c in diff["changes"]]
    if prev["sunset_at"]:
        lines.append(f"v{prev['major']} svarer 410 fra {prev['sunset_at']:%d.%m.%Y}. Indtil da svarer begge versioner.")
    if keys:
        lines.append(f"{len(keys)} {'nøgle' if len(keys) == 1 else 'nøgler'} kaldte v{prev['major']} de seneste 30 dage.")
    return "\n".join(lines), prev["major"], [k["name"] for k in keys]


async def error_counts(conn, slug: str, days: int = 30) -> list[dict]:
    rows = await db.fetch_all(conn, """select status, count(*) as n from dev_api_log where api = %s and status >= 400
                                       and ts > now() - make_interval(days => %s) group by status order by count(*) desc""",
                              (slug, days))
    total = sum(int(r["n"]) for r in rows)
    return [{"status": r["status"], "count": int(r["n"]), "pct": round(int(r["n"]) * 100 / total) if total else 0} for r in rows]


def _errors(counts: list[dict]) -> str:
    lines = ["Fejl svarer med JSON {\"error\": \"…\"} og en HTTP-status."]
    shown = set()
    for c in counts[:5]:
        if c["status"] in ERRORS_DA:
            lines.append(f"{c['status']} ({c['pct']} % af fejlene de seneste 30 dage): {ERRORS_DA[c['status']]}")
            shown.add(c["status"])
    for status in (401, 403, 429):
        if status not in shown:
            lines.append(f"{status}: {ERRORS_DA[status]}")
    return "\n".join(lines)


async def rule_texts(conn, a: dict, v: dict) -> tuple[dict[str, str], dict]:
    texts = {"intro": _intro(a, v)}
    for o in v["definition"].get("operations") or []:
        texts[op_section(o)] = _operation(o)
    migration, prev_major, keys = await _migration(conn, a, v)
    if migration:
        texts["migration"] = migration
    counts = await error_counts(conn, a["slug"])
    texts["errors"] = _errors(counts)
    return texts, {"prevMajor": prev_major, "oldKeys": keys, "errorCounts": counts}


async def ai_texts(a: dict, texts: dict[str, str], user_id: int | None) -> dict[str, str]:
    """The rule texts rewritten by AI, same keys, no new facts. {} without AI or when the answer does not fit."""
    answer = await assist.complete(
        assist.SYSTEM + " Omskriv teksterne til en API-dokumentation, så de er klare og venlige for en udvikler hos en "
        "partner. Brug kun fakta fra teksterne; tilføj ingen. Bevar stier, statuskoder og feltnavne præcist. Svar som JSON "
        "med de samme nøgler og en tekst pr. nøgle.",
        json.dumps({"api": a["name"], "tekster": texts}, ensure_ascii=False), user_id=user_id, max_tokens=1800)
    parsed = assist._json(answer)
    if not isinstance(parsed, dict):
        return {}
    return {k: str(v).strip()[:MAX_TEXT] for k, v in parsed.items() if k in texts and isinstance(v, str) and v.strip()}


async def generate(conn, a: dict, v: dict, user_id: int | None) -> dict:
    """Writes drafts for the version's major: rule texts, rewritten by AI when it is on. A section whose text equals
    the approved one gets no draft. Returns {written, ai}."""
    texts, _ = await rule_texts(conn, a, v)
    better = await ai_texts(a, texts, user_id)
    written = 0
    for section, text in texts.items():
        draft, source = (better[section], "ai") if section in better else (text, "rule")
        row = await db.fetch_one(conn, """select approved_text, draft_source from dev_api_docs
                                          where api_artifact_id = %s and major = %s and section = %s""",
                                 (a["id"], v["major"], section))
        if row and (row["approved_text"] == draft or row["draft_source"] == "human"):
            continue   # nothing new, or a person's draft is waiting: do not overwrite it
        await conn.execute("""
            insert into dev_api_docs (api_artifact_id, major, section, draft_text, draft_source) values (%s, %s, %s, %s, %s)
            on conflict (api_artifact_id, major, section) do update
            set draft_text = excluded.draft_text, draft_source = excluded.draft_source, updated_at = now()""",
            (a["id"], v["major"], section, draft[:MAX_TEXT], source))
        written += 1
    return {"written": written, "ai": bool(better)}


@jobs.handler("developer.docs")
async def docs_job(payload: dict, job: dict) -> dict:
    """After a version of an API is published: new drafts for its major (counted on the publisher when AI writes)."""
    async with db.connection() as conn:
        a = await artifacts.get(conn, payload["artifact_id"])
        v = await artifacts.current(conn, a)
        if v is None or v["id"] != payload["version_id"]:
            return {"skipped": True}
        return await generate(conn, a, v, payload.get("user_id"))


# ── Reading, approving, publishing ──────────────────────────────────────────

async def sections(conn, a: dict, v: dict) -> dict:
    rows = {r["section"]: r for r in await db.fetch_all(conn, """
        select d.*, coalesce(u.display_name, split_part(u.email, '@', 1)) as approved_by_name from dev_api_docs d
        left join users u on u.id = d.approved_by where d.api_artifact_id = %s and d.major = %s""", (a["id"], v["major"]))}
    _, facts = await rule_texts(conn, a, v)
    out = []
    for section, title in titles(v["definition"], "migration" in rows or facts["prevMajor"] is not None, facts["prevMajor"]):
        r = rows.get(section) or {}
        out.append({"section": section, "title": title, "draft": r.get("draft_text"), "source": r.get("draft_source"),
                    "approved": r.get("approved_text"), "approvedAt": r.get("approved_at"),
                    "approvedBy": r.get("approved_by_name")})
    return {"sections": out, "pending": sum(1 for s in out if s["draft"]), **facts}


async def _row(conn, a: dict, major: int, section: str) -> dict:
    row = await db.fetch_one(conn, """select * from dev_api_docs where api_artifact_id = %s and major = %s and section = %s
                                      for update""", (a["id"], major, section))
    if row is None:
        raise HTTPException(404, "Afsnittet findes ikke")
    return row


async def approve(conn, a: dict, major: int, section: str, *, actor_id: int, meta: RequestMeta) -> None:
    if not await artifacts.is_owner(conn, a["id"], actor_id):
        raise HTTPException(403, "Kun API'ets ejere (A eller R) kan godkende dokumentationen")
    row = await _row(conn, a, major, section)
    if not row["draft_text"]:
        raise HTTPException(409, "Der er ingen kladde at godkende")
    await conn.execute("""update dev_api_docs set approved_text = draft_text, draft_text = null, draft_source = null,
                          approved_by = %s, approved_at = now(), updated_at = now()
                          where api_artifact_id = %s and major = %s and section = %s""", (actor_id, a["id"], major, section))
    await audit(conn, "dev.api.docs.approved", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=a["id"],
                major=major, section=section, source=row["draft_source"])


async def edit(conn, a: dict, major: int, section: str, text: str, *, actor_id: int, meta: RequestMeta) -> None:
    await conn.execute("""
        insert into dev_api_docs (api_artifact_id, major, section, draft_text, draft_source) values (%s, %s, %s, %s, 'human')
        on conflict (api_artifact_id, major, section) do update
        set draft_text = excluded.draft_text, draft_source = 'human', updated_at = now()""",
        (a["id"], major, section, text[:MAX_TEXT]))
    await audit(conn, "dev.api.docs.edited", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=a["id"],
                major=major, section=section)


async def reject(conn, a: dict, major: int, section: str, *, actor_id: int, meta: RequestMeta) -> None:
    row = await _row(conn, a, major, section)
    if not row["draft_text"]:
        raise HTTPException(409, "Der er ingen kladde at afvise")
    await conn.execute("""update dev_api_docs set draft_text = null, draft_source = null, updated_at = now()
                          where api_artifact_id = %s and major = %s and section = %s""", (a["id"], major, section))
    await audit(conn, "dev.api.docs.rejected", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=a["id"],
                major=major, section=section, source=row["draft_source"])


async def approved(conn, artifact_id: int, major: int) -> dict[str, str]:
    rows = await db.fetch_all(conn, """select section, approved_text from dev_api_docs where api_artifact_id = %s and major = %s
                                       and approved_text is not null""", (artifact_id, major))
    return {r["section"]: r["approved_text"] for r in rows}


def apply(spec: dict, docs: dict[str, str]) -> dict:
    """The approved texts in the OpenAPI document: the intro as info.description, an operation's text as its
    description, the guides for a new major and for errors as info.x-gaer-guides."""
    if not docs:
        return spec
    if docs.get("intro"):
        spec["info"]["description"] = docs["intro"]
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            key = f"operation:{method.upper()} {path}"
            # OpenAPI paths use {name}; the definition uses :name
            alt = "operation:" + method.upper() + " " + path.replace("{", ":").replace("}", "")
            text = docs.get(key) or docs.get(alt)
            if text:
                op["description"] = text
    guides = {k: docs[k] for k in ("migration", "errors") if docs.get(k)}
    if guides:
        spec["info"]["x-gaer-guides"] = guides
    return spec


def examples(spec: dict) -> dict[str, dict[str, str]]:
    """Calls in curl, Python and TypeScript per operation, made from the OpenAPI document (example values from the
    operation's first example, else <parameter>)."""
    base = spec["servers"][0]["url"]
    out = {}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            ex = {}
            for e in (((op.get("responses") or {}).get("200") or {}).get("content") or {}).get("application/json", {}).get("examples", {}).values():
                try:
                    ex = json.loads(e.get("summary") or "{}")
                except ValueError:
                    ex = {}
                break
            url_path = path
            query = []
            for p in op.get("parameters") or []:
                if p["in"] == "path":
                    url_path = url_path.replace("{" + p["name"] + "}", str(ex.get(p["name"], f"<{p['name']}>")))
                elif p["in"] == "query" and p["name"] in ex:
                    query.append(f"{p['name']}={ex[p['name']]}")
            url = base + url_path + ("?" + "&".join(query) if query else "")
            m = method.upper()
            curl = f'curl {"-X " + m + " " if m != "GET" else ""}"{url}" \\\n  -H "Authorization: Bearer $GAER_API_KEY"'
            py = ("import os, httpx\n\n"
                  f"r = httpx.{method}(\n    \"{url}\",\n    headers={{\"Authorization\": f\"Bearer {{os.environ['GAER_API_KEY']}}\"}},\n)\n"
                  "r.raise_for_status()\nrows = r.json()[\"data\"]")
            ts = (f"const res = await fetch(\"{url}\", {{\n  method: \"{m}\",\n"
                  "  headers: { Authorization: `Bearer ${process.env.GAER_API_KEY}` },\n});\n"
                  "const { data } = await res.json();")
            out[f"{m} {path}"] = {"curl": curl, "python": py, "typescript": ts}
    return out

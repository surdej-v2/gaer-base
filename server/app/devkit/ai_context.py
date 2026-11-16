"""The context the AI in Gær Developer gets (specs/developer/AI.md, AI-1).

The browser names what the user is looking at — {kind, id} — and the server builds the text from the database. Free
text from the browser never reaches the prompt. Only metadata goes in: names, types, classification, descriptions,
statuses, counts and timings. Never a value from a row; error texts lose anything in quotes; connections and API keys
only show their name and status (their definitions hold hosts, users and secret references).
"""

import json
import re
from dataclasses import dataclass, field

from fastapi import HTTPException

from app import db
from app.devkit import catalog

KINDS = ("overview", "artifact", "dataset", "run")
MAX_CHARS = 24_000
# Artifact types whose definition is code worth showing; the others show name, status and contract only.
CODE_TYPES = {"pipeline", "operation", "multisource", "mcp_endpoint", "api", "flow", "dataset"}

QUOTED = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|«[^»\n]*»")


def redact(text: str | None, limit: int = 400) -> str:
    """An error or phase text without the values a database error may quote ("invalid input … 'abc'")."""
    return QUOTED.sub("«…»", (text or "").strip())[:limit]


@dataclass
class Context:
    label: str
    text: str
    chips: list[str] = field(default_factory=list)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=1)


def _fields(fields: list[dict]) -> list[dict]:
    return [{"navn": f["name"], "type": f.get("data_type") or f.get("type"), "klassifikation": f.get("classification"),
             "persondata": bool(f.get("personal_data") or f.get("personal")),
             **({"beskrivelse": f["description"][:200]} if f.get("description") else {})} for f in fields]


def _contract(contract: dict | None) -> dict:
    """A contract (app/devkit/contracts.py) as metadata: fields with type and classification, params, items."""
    c = contract or {}
    out: dict = {}
    if c.get("fields"):
        out["felter"] = [{"navn": k, "type": v.get("type"), "klassifikation": v.get("classification"),
                          "persondata": bool(v.get("personal"))} for k, v in c["fields"].items()]
    if c.get("params"):
        out["parametre"] = {k: {"type": v.get("type"), "påkrævet": bool(v.get("required"))} for k, v in c["params"].items()}
    if c.get("items"):
        out["elementer"] = list(c["items"])
    return out


async def build(conn, kind: str, ref: str | None, user_id: int) -> Context:
    if kind == "overview":
        return await _overview(conn, user_id)
    if not ref or not str(ref).isdigit():
        raise HTTPException(400, "Mangler id")
    rid = int(ref)
    if kind == "artifact":
        return await _artifact(conn, rid)
    if kind == "dataset":
        return await _dataset(conn, rid)
    if kind == "run":
        return await _run(conn, rid)
    raise HTTPException(400, "Ukendt kontekst")


async def _overview(conn, user_id: int) -> Context:
    by_type = await db.fetch_all(conn, """
        select type, count(*) as n, count(*) filter (where status = 'review_required') as review
        from dev_artifacts group by type order by type""")
    waiting = await db.fetch_all(conn, """
        select a.type, a.name, v.major, v.minor, v.patch, v.change_type, ap.role
        from dev_artifact_approvals ap join dev_artifact_versions v on v.id = ap.version_id
        join dev_artifacts a on a.id = v.artifact_id
        where ap.user_id = %s and ap.decision = 'pending' and v.status = 'pending' order by v.submitted_at limit 10""",
        (user_id,))
    failed = await db.fetch_all(conn, """
        select distinct on (r.artifact_id) a.name, r.id as run_id, r.status, r.error, r.queued_at
        from dev_pipeline_runs r join dev_artifacts a on a.id = r.artifact_id
        order by r.artifact_id, r.id desc""")
    failed = [f for f in failed if f["status"] == "failed"][:10]
    text = _dump({
        "artefakter": [{"type": r["type"], "antal": r["n"], "kræver_review": r["review"]} for r in by_type],
        "venter_på_brugeren": [{"type": w["type"], "navn": w["name"], "version": f"{w['major']}.{w['minor']}.{w['patch']}",
                                "ændring": w["change_type"], "rolle": w["role"]} for w in waiting],
        "pipelines_med_fejlet_seneste_kørsel": [{"navn": f["name"], "kørsel": f["run_id"], "fejl": redact(f["error"], 200)}
                                               for f in failed],
    })
    return Context("Oversigt over Gær Developer", text, ["Oversigt"] + (["Venter på dig"] if waiting else []))


async def _artifact(conn, artifact_id: int) -> Context:
    a = await db.fetch_one(conn, """
        select a.id, a.type, a.slug, a.name, a.description, a.status,
               v.major, v.minor, v.patch, v.status as version_status, v.definition, v.contract
        from dev_artifacts a left join dev_artifact_versions v on v.id = a.current_version_id where a.id = %s""",
        (artifact_id,))
    if a is None:
        raise HTTPException(404, "Artefaktet findes ikke")
    draft = await db.fetch_one(conn, """
        select major, minor, patch, status, definition, contract from dev_artifact_versions
        where artifact_id = %s and status in ('draft', 'pending') order by id desc limit 1""", (artifact_id,))
    info: dict = {"type": a["type"], "slug": a["slug"], "navn": a["name"], "status": a["status"],
                  "beskrivelse": (a["description"] or "")[:1000]}
    if a["major"] is not None:
        info["udgivet_version"] = f"{a['major']}.{a['minor']}.{a['patch']}"
        info["kontrakt"] = _contract(a["contract"])
        if a["type"] in CODE_TYPES:
            info["definition"] = a["definition"]
    if draft:
        info["kladde"] = {"version": f"{draft['major']}.{draft['minor']}.{draft['patch']}", "status": draft["status"],
                          "kontrakt": _contract(draft["contract"])}
        if a["type"] in CODE_TYPES:
            info["kladde"]["definition"] = draft["definition"]
    chips = [a["name"]] + ([f"Kladde {info['kladde']['version']}"] if draft else [])
    if a["type"] == "pipeline":
        runs = await db.fetch_all(conn, """
            select id, status, trigger, queued_at, started_at, finished_at, rows, error from dev_pipeline_runs
            where artifact_id = %s order by id desc limit 5""", (artifact_id,))
        info["seneste_kørsler"] = [{"kørsel": r["id"], "status": r["status"], "udløser": r["trigger"], "rækker": r["rows"],
                                    "start": r["started_at"], "slut": r["finished_at"], "fejl": redact(r["error"], 200)}
                                   for r in runs]
        if runs and runs[0]["status"] == "failed":
            chips.append(f"Kørsel #{runs[0]['id']}")
    return Context(f"{a['type'].replace('_', '-').capitalize()} {a['name']}", _dump(info)[:MAX_CHARS], chips)


async def _dataset(conn, dataset_id: int) -> Context:
    d = await catalog.dataset(conn, dataset_id)
    lineage = await catalog.lineage(conn, dataset_id)
    info = {"navn": d["name"], "nøgle": d["key"], "kilde": d["source_name"], "beskrivelse": (d["description"] or "")[:1000],
            "klassifikation": d["classification"], "persondata": d["personal_data"], "udstillet_til_mcp": d["mcp_exposed"],
            "felter": _fields(await catalog.fields(conn, dataset_id)),
            "lineage": [{"felt": r["field"], "fra": r["source_label"], "trin": r["step"], "transformation": r["transform"]}
                        for r in lineage][:200]}
    return Context(f"Datasæt {d['name']}", _dump(info)[:MAX_CHARS], [d["name"], "Datakatalog"])


async def _run(conn, run_id: int) -> Context:
    r = await db.fetch_one(conn, """
        select r.id, r.status, r.trigger, r.run_date, r.queued_at, r.started_at, r.finished_at, r.rows, r.size_bytes,
               r.small_groups, r.error, a.id as artifact_id, a.name, a.slug
        from dev_pipeline_runs r join dev_artifacts a on a.id = r.artifact_id where r.id = %s""", (run_id,))
    if r is None:
        raise HTTPException(404, "Kørslen findes ikke")
    phases = await db.fetch_all(conn, """
        select phase, label, ms, detail from dev_pipeline_run_phases where run_id = %s order by started_at, id""", (run_id,))
    info = {"pipeline": r["name"], "slug": r["slug"], "kørsel": r["id"], "status": r["status"], "udløser": r["trigger"],
            "dato": r["run_date"], "start": r["started_at"], "slut": r["finished_at"], "rækker": r["rows"],
            "små_grupper": r["small_groups"], "fejl": redact(r["error"]),
            "faser": [{"fase": p["label"], "ms": p["ms"], "detalje": redact(p["detail"], 200)} for p in phases]}
    return Context(f"Kørsel #{r['id']} af {r['name']}", _dump(info)[:MAX_CHARS], [r["name"], f"Kørsel #{r['id']}"])

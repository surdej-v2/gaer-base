"""Suggestions in Gær Developer ("AI foreslår", specs/developer/AI.md).

Rules in the code find them, so they work without AI (AI-4); the AI explains and drafts when the user acts on one.
Each suggestion has a key that changes when the situation does (a new failed run, a new dataset without a
description), so a dismissed suggestion stays away until there is something new to say. Dismissals are per user in
dev_ai_dismissals (personal data: in the GDPR export, gone with the user, cleaned by retention).
"""

from app import db

MAX_KEY = 200


def _s(key: str, kind: str, where: str, title: str, body: str, label: str, path: str, ref: dict | None = None) -> dict:
    return {"key": key[:MAX_KEY], "kind": kind, "where": where, "title": title, "body": body,
            "action": {"label": label, "path": path}, "ref": ref}


async def _failed_pipelines(conn, artifact_id: int | None) -> list[dict]:
    rows = await db.fetch_all(conn, """
        with ranked as (
            select r.id, r.artifact_id, r.status, row_number() over (partition by r.artifact_id order by r.id desc) as n
            from dev_pipeline_runs r where r.status in ('succeeded', 'failed', 'deferred')
              and (%(a)s::bigint is null or r.artifact_id = %(a)s))
        select a.id as artifact_id, a.name, max(x.id) as run_id, array_agg(x.status order by x.n) as statuses
        from ranked x join dev_artifacts a on a.id = x.artifact_id
        where x.n <= 5
        group by a.id, a.name
        having bool_or(x.n = 1 and x.status = 'failed')
        order by max(x.id) desc limit 10""", {"a": artifact_id})
    out = []
    for r in rows:
        streak = next((i for i, s in enumerate(r["statuses"]) if s != "failed"), len(r["statuses"]))
        times = "to gange i træk" if streak == 2 else f"{streak} gange i træk" if streak > 2 else ""
        out.append(_s(f"pipeline_failed:{r['artifact_id']}:{r['run_id']}", "pipeline_failed", f"Pipeline · {r['name']}",
                      f"Kørsel #{r['run_id']} fejlede" + (f" ({times})" if times else ""),
                      "Se tid pr. fase og fejlen. AI kan forklare, hvad der gik galt, og foreslå en ændring.",
                      "Se kørslen", f"/developer/pipeline/koersler/{r['run_id']}", {"kind": "run", "id": r["run_id"]}))
    return out


async def _datasets_without_description(conn) -> list[dict]:
    r = await db.fetch_one(conn, "select count(*) as n, max(id) as last from datasets where btrim(description) = ''")
    if not r["n"]:
        return []
    n = r["n"]
    return [_s(f"dataset_no_description:{r['last']}:{n}", "dataset_no_description", "Datakatalog",
               "1 datasæt mangler en beskrivelse" if n == 1 else f"{n} datasæt mangler en beskrivelse",
               "Uden beskrivelse er datasættet svært at finde for andre og for AI-klienter. AI kan skrive et forslag ud fra "
               "felterne, som du godkender.", "Åbn kataloget", "/developer/katalog")]


async def _endpoints_without_testcases(conn, artifact_id: int | None) -> list[dict]:
    rows = await db.fetch_all(conn, """
        select a.id, a.slug, a.name from dev_artifacts a
        where a.type = 'mcp_endpoint' and a.status = 'active' and a.current_version_id is not null
          and (%(a)s::bigint is null or a.id = %(a)s)
          and not exists (select 1 from dev_mcp_testcases t where t.endpoint = a.slug)
        order by a.name limit 10""", {"a": artifact_id})
    return [_s(f"mcp_no_testcases:{r['slug']}", "mcp_no_testcases", f"MCP-studie · /mcp/{r['slug']}",
               "Ingen testcases endnu", "Testcases skal bestå, før en major udgives. AI kan foreslå nogle ud fra toolene.",
               "Åbn testbænken", f"/developer/mcp/testbaenk?endpoint={r['slug']}", {"kind": "artifact", "id": r["id"]})
            for r in rows]


def parse_scope(scope: str) -> int | None:
    """overview → None (everything); artifact:<id> → that artifact."""
    if scope == "overview":
        return None
    kind, _, ref = scope.partition(":")
    if kind == "artifact" and ref.isdigit():
        return int(ref)
    raise ValueError(scope)


async def for_user(conn, user_id: int, scope: str = "overview") -> list[dict]:
    artifact_id = parse_scope(scope)
    found = await _failed_pipelines(conn, artifact_id)
    found += await _endpoints_without_testcases(conn, artifact_id)
    if artifact_id is None:
        found += await _datasets_without_description(conn)
    if not found:
        return []
    gone = {r["key"] for r in await db.fetch_all(conn, "select key from dev_ai_dismissals where user_id = %s and key = any(%s)",
                                                 (user_id, [s["key"] for s in found]))}
    return [s for s in found if s["key"] not in gone]

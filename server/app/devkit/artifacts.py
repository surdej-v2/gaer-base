"""Artifacts (F-1 … F-6): everything the four studios define, with owners, versions, approval and review.

- An artifact has a type, a slug and a name; each version has a semantic version number, a status
  (draft → pending → published → superseded, or rejected/withdrawn), a declarative definition (JSON) and its contract.
- Owners follow RACI: exactly one A, at least one R, any number of C and I. The creator starts as A and R.
- The system decides the change type (app/devkit/contracts.py). Patch needs no approval; minor needs R and C;
  major (and the first version) A, R and C. The approval flow is opt-in per artifact (`approval_flow`); without it a
  version is published when it is submitted. Whoever submits counts as having approved in the roles they hold.
- Downclassifying a field (or removing its personal-data mark) is always a major and needs a reason (F-9, PL-14).
- When a major is submitted, the owners (A and R) of everything that uses the artifact become C on that version
  (F-6, PL-16). When it is published, everything that uses it is flagged "kræver review"; an AI impact analysis is
  written when AI is on (job developer.impact), and the flag goes when an owner of the flagged artifact approves.
- Informed get a message (in the app and by mail) about every published version.
- Every change is audited in the same transaction (table dev_artifacts, row = the artifact).

Types plug in through TYPES: how to read a definition's contract, what it depends on, and what happens on publish.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import config, db, jobs, webhooks
from app.devkit import contracts
from app.logs import RequestMeta, audit

TYPE_LABELS = {"connection": "Forbindelse", "dataset": "Datasæt", "pipeline": "Pipeline", "operation": "Operation",
               "multisource": "Multisource-kilde", "mcp_endpoint": "MCP-endpoint", "api": "API", "api_key": "API-nøgle"}
STATUS_LABELS = {"active": "Aktiv", "disabled": "Deaktiveret", "review_required": "Kræver review"}
VERSION_LABELS = {"draft": "Kladde", "pending": "Afventer godkendelse", "published": "Udgivet", "superseded": "Erstattet",
                  "rejected": "Afvist", "withdrawn": "Trukket tilbage"}
ROLE_LABELS = {"R": "Responsible", "A": "Accountable", "C": "Consulted", "I": "Informed"}
NEEDS = {"patch": (), "minor": ("R", "C"), "major": ("A", "R", "C"), "initial": ("A", "R", "C")}
SLUG_RE = r"^[a-z][a-z0-9_-]{0,62}$"
COLUMNS = """a.id, a.type, a.slug, a.name, a.description, a.status, a.approval_flow, a.current_version_id, a.created_by,
             a.created_at, a.updated_at"""
VERSION_COLUMNS = """v.id, v.artifact_id, v.major, v.minor, v.patch, v.change_type, v.status, v.definition, v.contract,
                     v.changes, v.note, v.reason, v.created_by, v.created_at, v.submitted_at, v.decided_at, v.published_at"""


@dataclass
class TypeSpec:
    contract: Callable[[dict], dict] = lambda d: contracts.empty()
    dependencies: Callable[[object, dict], Awaitable[list[tuple[int, str]]]] | None = None
    on_publish: Callable[[object, dict, dict, dict | None], Awaitable[None]] | None = None
    other: dict[str, str] = field(default_factory=lambda: {"description": "Beskrivelse"})


TYPES: dict[str, TypeSpec] = {t: TypeSpec() for t in TYPE_LABELS}


def register(type_: str, spec: TypeSpec) -> None:
    TYPES[type_] = spec


def version_str(v: dict | None) -> str | None:
    return f"{v['major']}.{v['minor']}.{v['patch']}" if v else None


# ── Reading ────────────────────────────────────────────────────────────────

async def get(conn, artifact_id: int, *, lock: bool = False) -> dict:
    a = await db.fetch_one(conn, f"select {COLUMNS} from dev_artifacts a where a.id = %s {'for update' if lock else ''}",
                           (artifact_id,))
    if a is None:
        raise HTTPException(404, "Artefaktet findes ikke")
    return a


async def by_slug(conn, type_: str, slug: str) -> dict | None:
    return await db.fetch_one(conn, f"select {COLUMNS} from dev_artifacts a where a.type = %s and a.slug = %s",
                              (type_, slug))


async def version(conn, version_id: int) -> dict:
    v = await db.fetch_one(conn, f"select {VERSION_COLUMNS} from dev_artifact_versions v where v.id = %s", (version_id,))
    if v is None:
        raise HTTPException(404, "Versionen findes ikke")
    return v


async def current(conn, artifact: dict) -> dict | None:
    if not artifact["current_version_id"]:
        return None
    return await version(conn, artifact["current_version_id"])


async def open_version(conn, artifact_id: int) -> dict | None:
    """The draft or pending version, if any (there is at most one)."""
    return await db.fetch_one(conn, f"""select {VERSION_COLUMNS} from dev_artifact_versions v
                                        where v.artifact_id = %s and v.status in ('draft', 'pending')""", (artifact_id,))


async def roles(conn, artifact_id: int) -> dict[int, list[str]]:
    rows = await db.fetch_all(conn, "select user_id, role from dev_artifact_roles where artifact_id = %s", (artifact_id,))
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(r["role"])
    return {k: sorted(v, key="RACI".index) for k, v in out.items()}


async def people(conn, ids) -> dict[int, dict]:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = await db.fetch_all(conn, "select id, display_name, email, phone from users where id = any(%s)", (ids,))
    return {r["id"]: {"id": r["id"], "name": r["display_name"] or (r["email"] or "").split("@")[0] or r["phone"] or f"#{r['id']}"}
            for r in rows}


def out(a: dict, cur: dict | None = None) -> dict:
    return {"id": a["id"], "type": a["type"], "typeLabel": TYPE_LABELS[a["type"]], "slug": a["slug"], "name": a["name"],
            "description": a["description"], "status": a["status"], "statusLabel": STATUS_LABELS[a["status"]],
            "approvalFlow": a["approval_flow"], "version": version_str(cur), "createdAt": a["created_at"],
            "updatedAt": a["updated_at"]}


def version_out(v: dict) -> dict:
    return {"id": v["id"], "version": version_str(v), "changeType": v["change_type"], "status": v["status"],
            "statusLabel": VERSION_LABELS[v["status"]], "changes": v["changes"], "note": v["note"], "reason": v["reason"],
            "createdBy": v["created_by"], "createdAt": v["created_at"], "submittedAt": v["submitted_at"],
            "publishedAt": v["published_at"], "decidedAt": v["decided_at"]}


async def detail(conn, artifact_id: int) -> dict:
    a = await get(conn, artifact_id)
    cur = await current(conn, a)
    draft = await open_version(conn, artifact_id)
    raci = await roles(conn, artifact_id)
    versions = await db.fetch_all(conn, f"""select {VERSION_COLUMNS} from dev_artifact_versions v where v.artifact_id = %s
                                            order by v.id desc limit 50""", (artifact_id,))
    approvals = await approvals_for(conn, draft["id"]) if draft else []
    reviews = await db.fetch_all(conn, """select r.id, r.status, r.cause_artifact_id, c.type as cause_type, c.slug as cause_slug,
                                          c.name as cause_name, r.impact, r.ai_analysis, r.created_at, r.decided_at, r.note
                                          from dev_reviews r join dev_artifacts c on c.id = r.cause_artifact_id
                                          where r.artifact_id = %s order by r.id desc limit 20""", (artifact_id,))
    used_by = await dependents(conn, artifact_id)
    uses = await db.fetch_all(conn, """select a.id, a.type, a.slug, a.name, d.detail from dev_artifact_dependencies d
                                       join dev_artifacts a on a.id = d.depends_on where d.version_id = %s order by a.type, a.slug""",
                              (a["current_version_id"] or 0,))
    names = await people(conn, list(raci) + [v["created_by"] for v in versions] + [x["user_id"] for x in approvals])
    return {**out(a, cur), "current": {**version_out(cur), "definition": cur["definition"], "contract": cur["contract"]} if cur else None,
            "draft": {**version_out(draft), "definition": draft["definition"], "contract": draft["contract"]} if draft else None,
            "approvals": [{**x, "name": names.get(x["user_id"], {}).get("name"), "roleLabel": ROLE_LABELS[x["role"]]}
                          for x in approvals],
            "raci": [{"userId": uid, "name": names.get(uid, {}).get("name"), "roles": rs} for uid, rs in raci.items()],
            "versions": [{**version_out(v), "author": names.get(v["created_by"], {}).get("name")} for v in versions],
            "reviews": reviews, "usedBy": used_by, "uses": uses}


async def approvals_for(conn, version_id: int) -> list[dict]:
    return await db.fetch_all(conn, """select id, user_id, role, decision, note, decided_at from dev_artifact_approvals
                                       where version_id = %s order by position(role in 'ARC'), user_id""", (version_id,))


async def dependents(conn, artifact_id: int) -> list[dict]:
    """Artifacts whose published version uses this one."""
    return await db.fetch_all(conn, """
        select a.id, a.type, a.slug, a.name, a.status, d.detail from dev_artifact_dependencies d
        join dev_artifacts a on a.current_version_id = d.version_id where d.depends_on = %s order by a.type, a.slug""",
        (artifact_id,))


# ── Owners ──────────────────────────────────────────────────────────────────

async def validate_raci(conn, assignments: dict[int, list[str]]) -> dict[int, list[str]]:
    clean: dict[int, list[str]] = {}
    for uid, rs in assignments.items():
        rs = sorted({r for r in rs if r in ROLE_LABELS}, key="RACI".index)
        if rs:
            clean[int(uid)] = rs
    a = [u for u, rs in clean.items() if "A" in rs]
    if len(a) != 1:
        raise HTTPException(422, "Der skal være præcis én Accountable (A)")
    if not any("R" in rs for rs in clean.values()):
        raise HTTPException(422, "Der skal være mindst én Responsible (R)")
    users = await db.fetch_all(conn, "select id, audience, blocked_at from users where id = any(%s)", (list(clean),))
    found = {u["id"]: u for u in users}
    for uid in clean:
        u = found.get(uid)
        if u is None or u["blocked_at"]:
            raise HTTPException(422, f"Bruger {uid} findes ikke eller er spærret")
        if u["audience"] != "internal":
            raise HTTPException(422, "Ejere skal være interne brugere")
    return clean


async def set_raci(conn, artifact: dict, assignments: dict[int, list[str]], *, actor_id: int, meta: RequestMeta) -> None:
    clean = await validate_raci(conn, assignments)
    before = await roles(conn, artifact["id"])
    await conn.execute("delete from dev_artifact_roles where artifact_id = %s", (artifact["id"],))
    for uid, rs in clean.items():
        for r in rs:
            await conn.execute("insert into dev_artifact_roles (artifact_id, user_id, role) values (%s, %s, %s)",
                               (artifact["id"], uid, r))
    if before != clean:
        await audit(conn, "dev.raci.changed", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                    before={"raci": {str(k): v for k, v in before.items()}}, after={"raci": {str(k): v for k, v in clean.items()}})


async def is_owner(conn, artifact_id: int, user_id: int, wanted=("A", "R")) -> bool:
    row = await db.fetch_one(conn, "select 1 from dev_artifact_roles where artifact_id = %s and user_id = %s and role = any(%s)",
                             (artifact_id, user_id, list(wanted)))
    return row is not None


# ── Creating and changing ──────────────────────────────────────────────────

async def create(conn, *, type_: str, slug: str, name: str, definition: dict, actor_id: int, meta: RequestMeta,
                 description: str = "", approval_flow: bool = False, raci: dict[int, list[str]] | None = None,
                 publish: bool = True) -> dict:
    """A new artifact with its first version. With publish=True the version is submitted at once (published unless the
    approval flow is on); otherwise it stays a draft."""
    import re

    if type_ not in TYPE_LABELS:
        raise HTTPException(422, "Ukendt artefakttype")
    if not re.match(SLUG_RE, slug):
        raise HTTPException(422, "Slug må kun have små bogstaver, tal, - og _ og skal starte med et bogstav")
    if await by_slug(conn, type_, slug):
        raise HTTPException(409, f"Der findes allerede en {TYPE_LABELS[type_].lower()} med navnet {slug}")
    row = await db.fetch_one(conn, """insert into dev_artifacts (type, slug, name, description, approval_flow, created_by)
                                      values (%s, %s, %s, %s, %s, %s) returning id""",
                             (type_, slug, name.strip(), description.strip(), approval_flow, actor_id))
    artifact = await get(conn, row["id"])
    await set_raci(conn, artifact, raci or {actor_id: ["A", "R"]}, actor_id=actor_id, meta=meta)
    await audit(conn, "dev.artifact.created", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                after={"type": type_, "slug": slug, "name": name, "approval_flow": approval_flow})
    await save_draft(conn, artifact, definition, actor_id=actor_id, meta=meta)
    if publish:
        await submit(conn, artifact, actor_id=actor_id, meta=meta)
    return await get(conn, artifact["id"])


async def save_draft(conn, artifact: dict, definition: dict, *, actor_id: int, meta: RequestMeta, note: str = "") -> dict:
    """Creates or replaces the draft. A pending version is withdrawn first (its approvals no longer apply)."""
    spec = TYPES[artifact["type"]]
    contract = contracts.normalize(spec.contract(definition))
    open_v = await open_version(conn, artifact["id"])
    if open_v and open_v["status"] == "pending":
        await conn.execute("update dev_artifact_versions set status = 'withdrawn', decided_at = now() where id = %s",
                           (open_v["id"],))
        await audit(conn, "dev.version.withdrawn", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                    version=version_str(open_v), reason="ny kladde")
        open_v = None
    cur = await current(conn, artifact)
    d = contracts.diff(cur["contract"] if cur else None, contract,
                       other_changes=contracts.describe_other(cur["definition"] if cur else None, definition, spec.other))
    change = "initial" if cur is None else d["type"]
    nxt = contracts.FIRST if cur is None else contracts.Version(cur["major"], cur["minor"], cur["patch"]).bump(change)
    if open_v:
        await conn.execute("""update dev_artifact_versions set definition = %s, contract = %s, changes = %s, change_type = %s,
                              major = %s, minor = %s, patch = %s, note = %s, created_by = %s, created_at = now()
                              where id = %s""",
                           (Jsonb(definition), Jsonb(contract), Jsonb(d["changes"]), change, nxt.major, nxt.minor, nxt.patch,
                            note, actor_id, open_v["id"]))
        vid = open_v["id"]
    else:
        row = await db.fetch_one(conn, """insert into dev_artifact_versions (artifact_id, major, minor, patch, change_type,
                                          status, definition, contract, changes, note, created_by)
                                          values (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s) returning id""",
                                 (artifact["id"], nxt.major, nxt.minor, nxt.patch, change, Jsonb(definition), Jsonb(contract),
                                  Jsonb(d["changes"]), note, actor_id))
        vid = row["id"]
    await conn.execute("update dev_artifacts set updated_at = now() where id = %s", (artifact["id"],))
    await audit(conn, "dev.version.saved", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                version=str(nxt), change_type=change)
    return {**(await version(conn, vid)), "downclassified": d["downclassified"]}


async def submit(conn, artifact: dict, *, actor_id: int, meta: RequestMeta, note: str = "", reason: str = "") -> dict:
    """Sends the draft for approval, or publishes it when no approval is needed. Returns the version."""
    v = await open_version(conn, artifact["id"])
    if v is None or v["status"] != "draft":
        raise HTTPException(409, "Der er ingen kladde at sende")
    cur = await current(conn, artifact)
    d = contracts.diff(cur["contract"] if cur else None, v["contract"])
    if d["downclassified"] and not (reason or v["reason"]).strip():
        raise HTTPException(422, "Nedklassificering kræver en begrundelse: " + ", ".join(d["downclassified"]))
    if not await may_submit(conn, artifact):
        raise HTTPException(409, "Gemte testcases skal bestå, før en major udgives")
    await conn.execute("""update dev_artifact_versions set status = 'pending', submitted_at = now(), note = coalesce(nullif(%s, ''), note),
                          reason = coalesce(nullif(%s, ''), reason) where id = %s""", (note, reason, v["id"]))
    await audit(conn, "dev.version.submitted", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                version=version_str(v), change_type=v["change_type"], reason=(reason or None))
    needed = NEEDS[v["change_type"]] if artifact["approval_flow"] else ()
    if not needed:
        return await publish(conn, artifact, v["id"], actor_id=actor_id, meta=meta)
    raci = await roles(conn, artifact["id"])
    approvers: set[tuple[int, str]] = {(uid, r) for uid, rs in raci.items() for r in rs if r in needed}
    if v["change_type"] == "major":
        # F-6 / PL-16: the owners of everything that uses this artifact are Consulted on a major.
        for dep in await dependents(conn, artifact["id"]):
            for uid, rs in (await roles(conn, dep["id"])).items():
                if {"A", "R"} & set(rs):
                    approvers.add((uid, "C"))
    for uid, role in sorted(approvers):
        decision = "approved" if uid == actor_id else "pending"
        await conn.execute("""insert into dev_artifact_approvals (version_id, user_id, role, decision, decided_at)
                              values (%s, %s, %s, %s, case when %s = 'approved' then now() end) on conflict do nothing""",
                           (v["id"], uid, role, decision, decision))
        if decision == "pending":
            await notify(conn, uid, "approval", f"Godkend {artifact['name']} {version_str(v)}",
                         f"{TYPE_LABELS[artifact['type']]} · {v['change_type']} · du er {ROLE_LABELS[role]}",
                         link=f"/developer/godkendelser/{v['id']}", artifact_id=artifact["id"])
    return await _maybe_publish(conn, artifact, v["id"], actor_id=actor_id, meta=meta)


async def may_submit(conn, artifact: dict) -> bool:
    """Hook for MCP-10: saved test cases must pass before a major. Replaced by app.devkit.testcases when loaded."""
    gate = GATES.get(artifact["type"])
    return True if gate is None else await gate(conn, artifact)


GATES: dict[str, Callable[[object, dict], Awaitable[bool]]] = {}


async def _maybe_publish(conn, artifact: dict, version_id: int, *, actor_id: int, meta: RequestMeta) -> dict:
    rows = await approvals_for(conn, version_id)
    if any(r["decision"] == "rejected" for r in rows):
        await conn.execute("update dev_artifact_versions set status = 'rejected', decided_at = now() where id = %s", (version_id,))
        return await version(conn, version_id)
    if all(r["decision"] == "approved" for r in rows):
        return await publish(conn, artifact, version_id, actor_id=actor_id, meta=meta)
    return await version(conn, version_id)


async def decide(conn, version_id: int, *, actor_id: int, approve: bool, note: str, meta: RequestMeta) -> dict:
    v = await version(conn, version_id)
    if v["status"] != "pending":
        raise HTTPException(409, "Versionen afventer ikke godkendelse")
    artifact = await get(conn, v["artifact_id"], lock=True)
    mine = [r for r in await approvals_for(conn, version_id) if r["user_id"] == actor_id and r["decision"] == "pending"]
    if not mine:
        raise HTTPException(403, "Du er ikke godkender på denne version")
    for r in mine:
        await conn.execute("update dev_artifact_approvals set decision = %s, note = %s, decided_at = now() where id = %s",
                           ("approved" if approve else "rejected", note.strip(), r["id"]))
    await audit(conn, "dev.version.approved" if approve else "dev.version.rejected", actor_id=actor_id, meta=meta,
                table="dev_artifacts", row_id=artifact["id"], version=version_str(v), roles=[r["role"] for r in mine],
                note=note.strip() or None)
    out_v = await _maybe_publish(conn, artifact, version_id, actor_id=actor_id, meta=meta)
    if out_v["status"] == "rejected" and v["created_by"]:
        await notify(conn, v["created_by"], "rejected", f"{artifact['name']} {version_str(v)} er afvist", note.strip(),
                     link=f"/developer/godkendelser/{version_id}", artifact_id=artifact["id"])
    return out_v


async def publish(conn, artifact: dict, version_id: int, *, actor_id: int | None, meta: RequestMeta) -> dict:
    v = await version(conn, version_id)
    prev = await current(conn, artifact)
    if prev:
        await conn.execute("update dev_artifact_versions set status = 'superseded' where id = %s", (prev["id"],))
    await conn.execute("""update dev_artifact_versions set status = 'published', published_at = now(),
                          decided_at = coalesce(decided_at, now()) where id = %s""", (version_id,))
    await conn.execute("update dev_artifacts set current_version_id = %s, updated_at = now() where id = %s",
                       (version_id, artifact["id"]))
    spec = TYPES[artifact["type"]]
    if spec.dependencies:
        for dep_id, detail in await spec.dependencies(conn, v["definition"]):
            if dep_id != artifact["id"]:
                await conn.execute("""insert into dev_artifact_dependencies (version_id, depends_on, detail) values (%s, %s, %s)
                                      on conflict do nothing""", (version_id, dep_id, detail[:200]))
    artifact = await get(conn, artifact["id"])
    if spec.on_publish:
        await spec.on_publish(conn, artifact, v, prev)
    await audit(conn, "dev.version.published", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                version=version_str(v), change_type=v["change_type"])
    await webhooks.emit(conn, "developer.version.published",
                        {"artifact_id": artifact["id"], "type": artifact["type"], "version": version_str(v),
                         "change_type": v["change_type"]})
    for uid, rs in (await roles(conn, artifact["id"])).items():
        if "I" in rs and uid != actor_id:
            await notify(conn, uid, "published", f"{artifact['name']} {version_str(v)} er udgivet",
                         "; ".join(c["text"] for c in (v["changes"] or [])[:5]),
                         link=f"/developer/artefakter/{artifact['id']}", artifact_id=artifact["id"])
    if v["change_type"] == "major":
        await flag_dependents(conn, artifact, v, actor_id=actor_id, meta=meta)
    return await version(conn, version_id)


async def flag_dependents(conn, artifact: dict, v: dict, *, actor_id: int | None, meta: RequestMeta) -> int:
    n = 0
    for dep in await dependents(conn, artifact["id"]):
        row = await db.fetch_one(conn, """insert into dev_reviews (artifact_id, cause_artifact_id, cause_version_id, impact)
                                          values (%s, %s, %s, %s) returning id""",
                                 (dep["id"], artifact["id"], v["id"],
                                  Jsonb({"changes": [c for c in (v["changes"] or []) if c["type"] == "major"],
                                         "uses": dep["detail"]})))
        await conn.execute("update dev_artifacts set status = 'review_required', updated_at = now() where id = %s and status <> 'disabled'",
                           (dep["id"],))
        await audit(conn, "dev.review.required", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=dep["id"],
                    cause=artifact["id"], version=version_str(v))
        await webhooks.emit(conn, "developer.review.required", {"artifact_id": dep["id"], "cause_artifact_id": artifact["id"],
                                                                "review_id": row["id"]})
        for uid, rs in (await roles(conn, dep["id"])).items():
            if {"A", "R"} & set(rs):
                await notify(conn, uid, "review", f"{dep['name']} kræver review",
                             f"{artifact['name']} har fået en major ({version_str(v)})",
                             link=f"/developer/artefakter/{dep['id']}", artifact_id=dep["id"])
        if config.settings.ai_api_key:
            await jobs.enqueue(conn, "developer.impact", {"review_id": row["id"]}, max_attempts=2)
        n += 1
    return n


async def approve_review(conn, review_id: int, *, actor_id: int, note: str, meta: RequestMeta) -> dict:
    r = await db.fetch_one(conn, "select * from dev_reviews where id = %s for update", (review_id,))
    if r is None:
        raise HTTPException(404, "Reviewet findes ikke")
    if r["status"] != "open":
        raise HTTPException(409, "Reviewet er allerede godkendt")
    if not await is_owner(conn, r["artifact_id"], actor_id):
        raise HTTPException(403, "Kun ejerne (A eller R) af artefaktet kan godkende reviewet")
    await conn.execute("update dev_reviews set status = 'approved', decided_by = %s, decided_at = now(), note = %s where id = %s",
                       (actor_id, note.strip(), review_id))
    left = await db.fetch_one(conn, "select count(*) as n from dev_reviews where artifact_id = %s and status = 'open'",
                              (r["artifact_id"],))
    if left["n"] == 0:
        await conn.execute("update dev_artifacts set status = 'active', updated_at = now() where id = %s and status = 'review_required'",
                           (r["artifact_id"],))
    await audit(conn, "dev.review.approved", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=r["artifact_id"],
                review_id=review_id, note=note.strip() or None)
    return {"open": left["n"]}


async def set_status(conn, artifact: dict, enabled: bool, *, actor_id: int, meta: RequestMeta) -> None:
    if enabled and artifact["status"] == "disabled":
        open_reviews = await db.fetch_one(conn, "select count(*) as n from dev_reviews where artifact_id = %s and status = 'open'",
                                          (artifact["id"],))
        status = "review_required" if open_reviews["n"] else "active"
    elif not enabled:
        status = "disabled"
    else:
        return
    await conn.execute("update dev_artifacts set status = %s, updated_at = now() where id = %s", (status, artifact["id"]))
    await audit(conn, "dev.artifact.status", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=artifact["id"],
                before={"status": artifact["status"]}, after={"status": status})


# ── Messages ────────────────────────────────────────────────────────────────

async def notify(conn, user_id: int, kind: str, title: str, body: str = "", *, link: str = "",
                 artifact_id: int | None = None) -> None:
    row = await db.fetch_one(conn, """insert into dev_notifications (user_id, kind, title, body, link, artifact_id)
                                      values (%s, %s, %s, %s, %s, %s) returning id""",
                             (user_id, kind, title[:200], body[:1000], link, artifact_id))
    await jobs.enqueue(conn, "developer.mail", {"notification_id": row["id"]}, max_attempts=3)


@jobs.handler("developer.mail")
async def send_mail_job(payload: dict, job: dict) -> dict:
    import html

    from app import mail, outbound_mail, settings_store

    async with db.connection() as conn:
        n = await db.fetch_one(conn, """select n.*, u.email from dev_notifications n join users u on u.id = n.user_id
                                        where n.id = %s""", (payload["notification_id"],))
        if n is None or not n["email"] or n["mailed_at"]:
            return {"skipped": True}
        app_name = await settings_store.get(conn, "app_name")
    link = config.settings.origin.rstrip("/") + (n["link"] or "/developer")
    text = "\n\n".join(x for x in [n["title"], n["body"], f"Åbn: {link}", f"— {app_name} Developer"] if x)
    body = (f"<p><strong>{html.escape(n['title'])}</strong></p>" + (f"<p>{html.escape(n['body'])}</p>" if n["body"] else "")
            + f'<p><a href="{html.escape(link)}">Åbn i {html.escape(app_name)} Developer</a></p>')
    message_id = await mail.send_mail(n["email"], n["title"], text, body, app_name)
    async with db.connection() as conn:
        await outbound_mail.record_sent(conn, message_id=message_id, recipient=n["email"], user_id=n["user_id"],
                                        purpose="developer", subject=n["title"])
        await conn.execute("update dev_notifications set mailed_at = now() where id = %s", (n["id"],))
    return {"sent": n["kind"]}


@jobs.handler("developer.impact")
async def impact_job(payload: dict, job: dict) -> dict:
    """F-6: the AI impact analysis of a major for one affected artifact. Only metadata and the definitions."""
    from app.devkit import assist

    async with db.connection() as conn:
        r = await db.fetch_one(conn, "select * from dev_reviews where id = %s", (payload["review_id"],))
        if r is None:
            return {"skipped": True}
        cause = await version(conn, r["cause_version_id"]) if r["cause_version_id"] else None
        affected = await get(conn, r["artifact_id"])
        aff_v = await current(conn, affected)
    text = await assist.impact(cause, aff_v, r["impact"])
    if text:
        async with db.connection() as conn:
            await conn.execute("update dev_reviews set ai_analysis = %s where id = %s", (text[:8000], r["id"]))
    return {"analysed": bool(text)}

"""Export and import between instances (F-22 … F-24, afsnit 10 "Nøglepar til eksportfiler").

- Every instance makes, the first time it is needed, a key pair for signing (Ed25519) and one for encryption (X25519).
  The private keys are encrypted with the instance's key provider (app/secrets_box.py) and are never exported; the
  public keys are on /.well-known/gaer-instance-keys and in /developer/eksport with their fingerprints. Keys have an id,
  so they can be rotated.
- An export is one signed JSON file: a manifest and the definitions of the chosen artifacts (connections, datasets,
  pipelines, operations, multisource sources, MCP endpoints, APIs) and their catalog entries. Internal ids become
  references by type and slug (`{"$ref": "operation/soeg_lejemaal"}`, `{"$dataset": "crm/lejere"}`). Secrets are only
  ever references ("gruppe/navn"). Tokens and snapshot data are never in it.
- An import is checked first: the signature must come from a known instance (added by an admin, who confirms the
  fingerprint by another channel), and every reference must exist here or in the file — otherwise it is blocked. Then
  each artifact becomes a new version that goes through the approval flow like any other change.
- Exporting secret values (encrypted to the receiver's X25519 key) is not built: the default — references only — is
  the only mode.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import HTTPException

from app import db, secrets_box
from app.devkit import artifacts

FORMAT = "gaer-export/1"
ORDER = ["connection", "dataset", "pipeline", "operation", "multisource", "mcp_endpoint", "api"]
RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def fingerprint(public_b64: str) -> str:
    h = hashlib.sha256(base64.b64decode(public_b64)).hexdigest()[:32]
    return ":".join(h[i:i + 4] for i in range(0, 32, 4))


async def instance_keys(conn) -> dict[str, dict]:
    """The current signing and encryption keys, created the first time."""
    rows = {r["kind"]: r for r in await db.fetch_all(conn, "select * from dev_instance_keys where retired_at is null")}
    for kind in ("sign", "encrypt"):
        if kind in rows:
            continue
        priv = Ed25519PrivateKey.generate() if kind == "sign" else X25519PrivateKey.generate()
        pub = _b64(priv.public_key().public_bytes(*RAW))
        raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        kid = f"{kind[0]}{hashlib.sha256(pub.encode()).hexdigest()[:10]}"
        await conn.execute("insert into dev_instance_keys (id, kind, public_key, private_enc) values (%s, %s, %s, %s)",
                           (kid, kind, pub, secrets_box.encrypt(_b64(raw), f"instance-key:{kid}")))
        rows[kind] = await db.fetch_one(conn, "select * from dev_instance_keys where id = %s", (kid,))
    return rows


async def public_keys(conn) -> list[dict]:
    keys = await instance_keys(conn)
    return [{"id": k["id"], "use": k["kind"], "alg": "Ed25519" if k["kind"] == "sign" else "X25519", "public_key": k["public_key"],
             "fingerprint": fingerprint(k["public_key"])} for k in keys.values()]


def canonical(doc: dict) -> bytes:
    return json.dumps({k: v for k, v in doc.items() if k != "signature"}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode()


# ── References ──────────────────────────────────────────────────────────────

async def _dataset_ref(conn, dataset_id: int) -> dict:
    d = await db.fetch_one(conn, """select d.key, coalesce(s.key, 'gaer') as source from datasets d left join data_sources s on s.id = d.source_id
                                    where d.id = %s""", (dataset_id,))
    return {"$dataset": f"{d['source']}/{d['key']}"} if d else {"$dataset": f"?/{dataset_id}"}


async def _artifact_ref(conn, artifact_id: int) -> dict:
    a = await db.fetch_one(conn, "select type, slug from dev_artifacts where id = %s", (artifact_id,))
    return {"$ref": f"{a['type']}/{a['slug']}"} if a else {"$ref": f"?/{artifact_id}"}


async def to_refs(conn, type_: str, definition: dict) -> dict:
    d = {k: v for k, v in definition.items() if not k.startswith("_") and k != "guide"}
    if type_ == "operation":
        d["source"] = {"dataset": await _dataset_ref(conn, d["source"]["dataset"])}
    elif type_ == "mcp_endpoint":
        d["tools"] = [{**{k: v for k, v in t.items() if not k.startswith("_") and k != "operation_slug"},
                       "operation": await _artifact_ref(conn, t["operation"])} for t in d.get("tools") or []]
    elif type_ == "multisource":
        d["steps"] = [{**{k: v for k, v in s.items() if not k.startswith("_")},
                       **({"operation": await _artifact_ref(conn, s["operation"])} if s.get("operation") else {})} for s in d.get("steps") or []]
    elif type_ == "api":
        d["operations"] = [{**{k: v for k, v in o.items() if not k.startswith("_") and k not in ("operation_slug", "kind")},
                            "operation": await _artifact_ref(conn, o["operation"])} for o in d.get("operations") or []]
    return d


async def resolve(conn, value, missing: list[str], incoming: set[str]):
    """Turns references back into this instance's ids. A reference that exists only in the file is resolved after the
    referenced artifact has been imported (`incoming`); one that exists nowhere is recorded as missing."""
    if isinstance(value, dict) and set(value) == {"$ref"}:
        type_, _, slug = value["$ref"].partition("/")
        a = await artifacts.by_slug(conn, type_, slug)
        if a:
            return a["id"]
        if value["$ref"] not in incoming:
            missing.append(f"Reference til {value['$ref']} mangler")
        return value
    if isinstance(value, dict) and set(value) == {"$dataset"}:
        source, _, key = value["$dataset"].partition("/")
        d = await db.fetch_one(conn, """select d.id from datasets d left join data_sources s on s.id = d.source_id
                                        where d.key = %s and coalesce(s.key, 'gaer') = %s""", (key, source))
        if d:
            return d["id"]
        missing.append(f"Datasættet {value['$dataset']} findes ikke her")
        return value
    if isinstance(value, dict):
        return {k: await resolve(conn, v, missing, incoming) for k, v in value.items()}
    if isinstance(value, list):
        return [await resolve(conn, v, missing, incoming) for v in value]
    return value


# ── Export ──────────────────────────────────────────────────────────────────

async def export(conn, artifact_ids: list[int], *, include_catalog: bool) -> dict:
    keys = await instance_keys(conn)
    items, catalog = [], []
    for aid in sorted(set(artifact_ids)):
        a = await artifacts.get(conn, aid)
        v = await artifacts.current(conn, a)
        if v is None:
            raise HTTPException(409, f"{a['slug']} har ingen udgivet version")
        definition = await to_refs(conn, a["type"], v["definition"])
        items.append({"type": a["type"], "slug": a["slug"], "name": a["name"], "description": a["description"],
                      "version": artifacts.version_str(v), "approval_flow": a["approval_flow"], "definition": definition})
        if include_catalog and a["type"] == "operation":
            ds = v["definition"]["source"]["dataset"]
            fields = await db.fetch_all(conn, """select name, label, description, classification, personal_data, sensitive, mask
                                                 from dataset_fields where dataset_id = %s order by position""", (ds,))
            ref = await _dataset_ref(conn, ds)
            if ref not in [c["dataset"] for c in catalog]:
                catalog.append({"dataset": ref, "fields": fields})
    items.sort(key=lambda x: ORDER.index(x["type"]) if x["type"] in ORDER else 99)
    doc = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "signer": keys["sign"]["id"],
           "signer_public_key": keys["sign"]["public_key"], "secrets": "references",
           "manifest": [{"type": i["type"], "slug": i["slug"], "version": i["version"],
                         "sha256": hashlib.sha256(json.dumps(i["definition"], sort_keys=True, default=str).encode()).hexdigest()}
                        for i in items], "artifacts": items, "catalog": catalog}
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(secrets_box.decrypt(keys["sign"]["private_enc"],
                                                                                     f"instance-key:{keys['sign']['id']}")))
    doc["signature"] = _b64(priv.sign(canonical(doc)))
    return doc


# ── Import ──────────────────────────────────────────────────────────────────

async def check(conn, doc: dict) -> dict:
    """What an import would create and change, and what blocks it (F-23)."""
    problems: list[str] = []
    if doc.get("format") != FORMAT:
        raise HTTPException(422, "Filen er ikke en eksport fra Gær Developer")
    known = await db.fetch_one(conn, "select name, sign_key from dev_known_instances where sign_key = %s", (doc.get("signer_public_key"),))
    signer = None
    if known is None:
        own = {k["public_key"] for k in (await instance_keys(conn)).values()}
        if doc.get("signer_public_key") in own:
            signer = "denne instans"
        else:
            problems.append("Afsenderen er ikke en kendt instans. Tilføj dens offentlige nøgle under Kendte instanser")
    else:
        signer = known["name"]
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(doc.get("signer_public_key") or "")).verify(
            base64.b64decode(doc.get("signature") or ""), canonical(doc))
    except (InvalidSignature, ValueError):
        problems.append("Signaturen passer ikke: filen er ændret eller ikke signeret af afsenderen")
    incoming = {f"{a['type']}/{a['slug']}" for a in doc.get("artifacts") or []}
    actions = []
    for a in doc.get("artifacts") or []:
        if a["type"] not in ORDER:
            problems.append(f"Ukendt type {a['type']}")
            continue
        missing: list[str] = []
        await resolve(conn, a["definition"], missing, incoming)
        problems += missing
        existing = await artifacts.by_slug(conn, a["type"], a["slug"])
        actions.append({"type": a["type"], "typeLabel": artifacts.TYPE_LABELS[a["type"]], "slug": a["slug"], "version": a["version"],
                        "action": "update" if existing else "create", "problems": missing})
    for c in doc.get("catalog") or []:
        missing: list[str] = []
        await resolve(conn, c["dataset"], missing, incoming)
        problems += missing
        actions.append({"type": "catalog", "typeLabel": "Datakatalog", "slug": c["dataset"]["$dataset"], "version": None,
                        "action": "update", "problems": missing})
    if doc.get("secrets") != "references":
        problems.append("Filen indeholder hemmelige værdier; import af dem er ikke bygget")
    return {"signer": signer, "actions": actions, "problems": sorted(set(problems)), "ok": not problems}


async def run_import(conn, doc: dict, *, actor_id: int, meta) -> dict:
    """Creates new versions through the approval flow. Raises 409 when the check finds problems."""
    from app.devkit import apis, catalog as dev_catalog, connections, mcp_endpoints, multisource, operations, pipelines

    result = await check(conn, doc)
    if not result["ok"]:
        raise HTTPException(409, "Importen er spærret: " + "; ".join(result["problems"]))
    done = []
    incoming = {f"{a['type']}/{a['slug']}" for a in doc["artifacts"]}
    for item in sorted(doc["artifacts"], key=lambda x: ORDER.index(x["type"])):
        missing: list[str] = []
        raw = await resolve(conn, item["definition"], missing, incoming)
        if missing:
            raise HTTPException(409, "; ".join(missing))
        t = item["type"]
        existing = await artifacts.by_slug(conn, t, item["slug"])
        if t == "operation":
            d = await operations.validate(conn, raw)
        elif t == "multisource":
            d = await multisource.validate(conn, raw)
        elif t == "mcp_endpoint":
            d = await mcp_endpoints.prepare(conn, existing, raw)
        elif t == "api":
            d = await apis.validate(conn, raw)
        elif t == "pipeline":
            d, _ = await pipelines.prepare(conn, pipelines.parse(raw))
        elif t == "connection":
            d = connections.parse(raw)
        else:
            d = raw
        if t == "connection" and existing is None:
            row = await db.fetch_one(conn, "insert into data_sources (key, name, kind, spec_yaml, hosts) values (%s, %s, %s, %s, %s) returning id",
                                     (item["slug"], item["name"], d["type"], json.dumps(d), [d["host"]] if d.get("host") else []))
        if existing is None:
            a = await artifacts.create(conn, type_=t, slug=item["slug"], name=item["name"], description=item.get("description") or "",
                                       definition=d, actor_id=actor_id, meta=meta, approval_flow=item.get("approval_flow", False))
            if t == "connection":
                await conn.execute("update data_sources set artifact_id = %s where id = %s", (a["id"], row["id"]))
        else:
            await artifacts.save_draft(conn, existing, d, actor_id=actor_id, meta=meta, note="Importeret")
            await artifacts.submit(conn, existing, actor_id=actor_id, meta=meta, note="Importeret")
            a = existing
        done.append({"type": t, "slug": item["slug"], "id": a["id"]})
    for c in doc.get("catalog") or []:
        missing: list[str] = []
        ds_id = await resolve(conn, c["dataset"], missing, incoming)
        d = await dev_catalog.dataset(conn, ds_id)
        a = await dev_catalog.ensure_artifact(conn, d, actor_id=actor_id, meta=meta)
        cur = await artifacts.current(conn, a)
        fields = dev_catalog.clean_fields(cur["definition"]["fields"], c["fields"])
        await artifacts.save_draft(conn, a, {**cur["definition"], "fields": fields}, actor_id=actor_id, meta=meta, note="Importeret")
        await artifacts.submit(conn, a, actor_id=actor_id, meta=meta, note="Importeret",
                               reason="Klassifikation importeret fra en kendt instans")
        done.append({"type": "catalog", "slug": c["dataset"]["$dataset"], "id": a["id"]})
    return {"imported": done, "signer": result["signer"]}

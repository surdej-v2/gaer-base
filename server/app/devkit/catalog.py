"""The data catalog in Gær Developer (F-5, F-7 … F-9): datasets and fields as versioned artifacts, and lineage.

The tables are the ones /admin/data already uses (datasets, dataset_fields, migration 0012) with the columns from 0032:
kind, personal data and sensitive marks, a chosen masking and lineage. A dataset gets an artifact of type `dataset`
the first time it is changed from /developer; its definition is the dataset's description and fields. Changes go
through the same flow as everything else: a description is a patch and is published at once, a new or stricter
classification is a minor, and a downclassification (or removing the personal-data mark) is a major with a reason.
Publishing writes the definition back to the catalog tables.
"""

import re

from fastapi import HTTPException

from app import db
from app.devkit import artifacts, contracts, masking
from app.logs import RequestMeta

FIELD_KEYS = ("name", "label", "description", "data_type", "classification", "personal_data", "sensitive", "mask")
KIND_LABELS = {"table": "Tabel", "api": "API-kilde", "snapshot": "Snapshot", "foreign": "Database (FDW)", "live": "Live-kilde"}


def contract(definition: dict) -> dict:
    return {"fields": {f["name"]: {"type": f.get("data_type", "text"), "classification": f.get("classification", "yellow"),
                                   "personal": bool(f.get("personal_data"))} for f in definition.get("fields", [])},
            "key": definition.get("key")}


async def fields(conn, dataset_id: int) -> list[dict]:
    return await db.fetch_all(conn, """
        select id, name, path, label, description, data_type, classification, position, personal_data, sensitive, mask, inherited
        from dataset_fields where dataset_id = %s order by position, id""", (dataset_id,))


async def dataset(conn, dataset_id: int) -> dict:
    d = await db.fetch_one(conn, """
        select d.*, coalesce(s.key, 'gaer') as source_key, coalesce(s.name, 'Tørgær (egen database)') as source_name,
               coalesce(s.kind, case when d.kind = 'snapshot' then 'snapshot' else 'gaer' end) as source_kind
        from datasets d left join data_sources s on s.id = d.source_id where d.id = %s""", (dataset_id,))
    if d is None:
        raise HTTPException(404, "Datasættet findes ikke")
    return d


async def definition_of(conn, d: dict) -> dict:
    return {"description": d["description"], "key": None,
            "fields": [{k: f[k] for k in FIELD_KEYS} for f in await fields(conn, d["id"])]}


def slug_for(d: dict) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", f"{d['source_key']}-{d['key']}".lower()).strip("-")
    base = base if re.match(r"^[a-z]", base) else f"ds-{base}"
    return base[:56]


async def ensure_artifact(conn, d: dict, *, actor_id: int, meta: RequestMeta) -> dict:
    """The dataset's artifact; created (with the catalog as it is now as version 1.0.0) the first time."""
    if d["artifact_id"]:
        return await artifacts.get(conn, d["artifact_id"])
    slug = slug_for(d)
    if await artifacts.by_slug(conn, "dataset", slug):
        slug = f"{slug[:48]}-{d['id']}"
    a = await artifacts.create(conn, type_="dataset", slug=slug, name=d["name"], description=d["description"],
                               definition=await definition_of(conn, d), actor_id=actor_id, meta=meta)
    await conn.execute("update datasets set artifact_id = %s where id = %s", (a["id"], d["id"]))
    return a


def clean_fields(current: list[dict], wanted: list[dict]) -> list[dict]:
    """The new field list: only fields that exist (the catalog follows the source; fields are not invented here)."""
    by_name = {f["name"]: f for f in current}
    out = []
    for w in wanted:
        f = by_name.get(w.get("name"))
        if f is None:
            raise HTTPException(422, f"Feltet {w.get('name')} findes ikke i datasættet")
        merged = {k: f[k] for k in FIELD_KEYS}
        for k in ("label", "description", "classification", "personal_data", "sensitive", "mask"):
            if k in w and w[k] is not None:
                merged[k] = w[k]
        if merged["classification"] not in contracts.RANK:
            raise HTTPException(422, f"{f['name']}: klassifikation skal være white, green, yellow eller red")
        if merged["mask"] not in (None, "") and merged["mask"] not in masking.STRICTNESS:
            raise HTTPException(422, f"{f['name']}: ukendt maskering")
        merged["mask"] = merged["mask"] or None
        out.append(merged)
    try:
        masking.policy_for(out)
    except masking.MaskingError as e:
        raise HTTPException(422, str(e))
    missing = [n for n in by_name if n not in {f["name"] for f in out}]
    return out + [{k: by_name[n][k] for k in FIELD_KEYS} for n in missing]


async def on_publish(conn, artifact: dict, v: dict, prev: dict | None) -> None:
    d = await db.fetch_one(conn, "select id from datasets where artifact_id = %s", (artifact["id"],))
    if d is None:
        return
    definition = v["definition"]
    for f in definition.get("fields", []):
        await conn.execute("""update dataset_fields set label = %s, description = %s, classification = %s, personal_data = %s,
                              sensitive = %s, mask = %s where dataset_id = %s and name = %s""",
                           (f.get("label") or "", f.get("description") or "", f["classification"], bool(f.get("personal_data")),
                            bool(f.get("sensitive")), f.get("mask"), d["id"], f["name"]))
    personal = any(f.get("personal_data") for f in definition.get("fields", []))
    await conn.execute("""update datasets set description = %s, version = version + 1, generation = generation + 1,
                          personal_data = personal_data or %s, updated_at = now() where id = %s""",
                       (definition.get("description") or "", personal, d["id"]))


artifacts.register("dataset", artifacts.TypeSpec(contract=contract, on_publish=on_publish,
                                                 other={"description": "Beskrivelse"}))


async def lineage(conn, dataset_id: int) -> list[dict]:
    return await db.fetch_all(conn, """
        select f.name as field, f.classification, f.personal_data, l.source_label, l.step, l.transform,
               sf.classification as source_classification, sf.personal_data as source_personal, sd.id as source_dataset_id
        from dataset_fields f join dataset_lineage l on l.field_id = f.id
        left join dataset_fields sf on sf.id = l.source_field_id left join datasets sd on sd.id = sf.dataset_id
        where f.dataset_id = %s order by f.position, l.id""", (dataset_id,))


async def all_fields(conn) -> list[dict]:
    """Every classified field in the catalog (for suggestions)."""
    return await db.fetch_all(conn, """select f.name, f.label, f.classification, f.personal_data, d.name as dataset
                                       from dataset_fields f join datasets d on d.id = f.dataset_id""")

"""Pipelines (PL-1 … PL-17): an ordered list of stages modelled on MongoDB's aggregation pipeline, translated to SQL
(one CTE per stage) and materialized as a snapshot in the schema `snapshots` of the analytics database.

    name: lejemaal_pr_ejendom
    schedule: dagligt 06:00          # or "hver time", "manuelt"; {type, earliest, latest} in JSON
    wait_for_load: true              # wait for ingest.load_status (afsnit 10), deferred after `latest` (08:00)
    key: ejendom_id                  # unique key: REFRESH MATERIALIZED VIEW CONCURRENTLY needs it
    stages:
      - source: lejemaal
      - match: {status: aktiv}
      - lookup: {from: ejendomme, local: ejendom_id, foreign: id, fields: [id, adresse]}
      - group: {by: ejendom_id, antal_lejemaal: count, aarlig_leje_i_alt: sum(aarlig_leje)}

Stages: source, match ($match), lookup ($lookup), unwind ($unwind), addFields/set, project, group, sort, limit,
unionWith, mask. Every value is a literal (psycopg.sql.Literal) and every name an identifier that must exist in the
stage before it, so a definition can never inject SQL. The compiler also knows, for every output column, the source
columns and stages it comes from (lineage) and its classification: the strictest of its sources (F-9, PL-14).
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import yaml
from fastapi import HTTPException
from psycopg import sql

from app import config, db
from app.devkit import connections, masking

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SLUG = re.compile(r"^[a-z][a-z0-9_]{0,55}$")
STAGES = ("source", "match", "lookup", "unwind", "addFields", "set", "project", "group", "sort", "limit", "unionWith", "mask")
STAGE_DA = {"source": "Kilde", "match": "Filtrer ($match)", "lookup": "Opslag ($lookup)", "unwind": "Fold ud ($unwind)",
            "addFields": "Tilføj felter ($addFields)", "set": "Tilføj felter ($set)", "project": "Projicer ($project)",
            "group": "Grupper ($group)", "sort": "Sorter ($sort)", "limit": "Begræns ($limit)", "unionWith": "Saml ($unionWith)",
            "mask": "Masker"}
MIN_GROUP = 5
TZ = ZoneInfo("Europe/Copenhagen")
OPS = {"$eq": "=", "$ne": "<>", "$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}
NUMERIC = {"integer", "bigint", "smallint", "numeric", "real", "double precision", "number"}


class PipelineError(HTTPException):
    def __init__(self, text: str):
        super().__init__(422, text)


# ── Parsing ─────────────────────────────────────────────────────────────────

def _schedule(raw) -> dict:
    if raw in (None, "", "manuelt", "manual"):
        return {"type": "manual"}
    if isinstance(raw, dict):
        t = raw.get("type", "daily")
        earliest, latest = str(raw.get("earliest", "06:00")), str(raw.get("latest", "08:00"))
    else:
        text = str(raw).strip().lower()
        if text in ("hver time", "hourly", "timevis"):
            return {"type": "hourly"}
        m = re.match(r"^(dagligt|daily)\s*(\d{1,2}:\d{2})?$", text)
        if not m:
            raise PipelineError("schedule skal være 'dagligt 06:00', 'hver time' eller 'manuelt'")
        t, earliest, latest = "daily", m.group(2) or "06:00", "08:00"
    if t not in ("daily", "hourly", "manual"):
        raise PipelineError("schedule.type skal være daily, hourly eller manual")
    for v in (earliest, latest):
        if not re.match(r"^\d{1,2}:\d{2}$", v):
            raise PipelineError("Tidspunkter skal skrives som 06:00")
    return {"type": t, "earliest": earliest.zfill(5), "latest": latest.zfill(5)}


def parse(raw) -> dict:
    """YAML text, JSON text or an object → a normalized definition. Raises PipelineError."""
    if isinstance(raw, str):
        try:
            raw = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            raise PipelineError("Ikke gyldig YAML" + (f" (linje {mark.line + 1})" if mark else ""))
    if not isinstance(raw, dict):
        raise PipelineError("En pipeline er et objekt med name, key og stages")
    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise PipelineError("stages skal være en liste med mindst ét trin")
    stages = []
    for i, s in enumerate(stages_raw, 1):
        if not isinstance(s, dict) or len(s) != 1:
            raise PipelineError(f"Trin {i} skal have præcis én type, fx '- match: {{status: aktiv}}'")
        (kind, body), = s.items()
        if kind not in STAGES:
            raise PipelineError(f"Trin {i}: ukendt trin {kind}. Brug {', '.join(STAGES)}")
        stages.append({kind: body})
    if "source" not in stages[0]:
        raise PipelineError("Første trin skal være source")
    try:
        min_group = int(raw.get("min_group_size") or MIN_GROUP)
        max_minutes = max(1, min(int(raw.get("max_minutes") or 15), 240))
    except (TypeError, ValueError):
        raise PipelineError("min_group_size og max_minutes skal være tal")
    if min_group < MIN_GROUP:
        raise PipelineError(f"Den mindste gruppestørrelse kan hæves, men ikke sænkes under {MIN_GROUP} (PL-15)")
    history = raw.get("history")
    if history is not None:
        try:
            history = {"keep_days": max(1, min(int((history or {}).get("keep_days", 30)), 3650))}
        except (TypeError, ValueError, AttributeError):
            raise PipelineError("history.keep_days skal være et tal")
    load_sources = [str(x) for x in (raw.get("load_sources") or [])][:20]
    return {"name": str(raw.get("name") or "")[:120], "description": str(raw.get("description") or "")[:2000],
            "schedule": _schedule(raw.get("schedule")), "wait_for_load": bool(raw.get("wait_for_load", False)),
            "load_sources": load_sources, "key": raw.get("key"), "max_minutes": max_minutes, "paused": bool(raw.get("paused", False)),
            "min_group_size": min_group, "history": history, "stages": stages}


def to_yaml(definition: dict) -> str:
    d = {k: v for k, v in definition.items() if not k.startswith("_")}
    s = d.get("schedule") or {}
    if s.get("type") == "daily" and s.get("latest", "08:00") == "08:00":
        d["schedule"] = f"dagligt {s.get('earliest', '06:00')}"
    elif s.get("type") == "hourly":
        d["schedule"] = "hver time"
    elif s.get("type") == "manual":
        d["schedule"] = "manuelt"
    for k in ("history", "load_sources", "description"):
        if not d.get(k):
            d.pop(k, None)
    if d.get("min_group_size") == MIN_GROUP:
        d.pop("min_group_size")
    if not d.get("paused"):
        d.pop("paused", None)
    order = ["name", "description", "schedule", "wait_for_load", "load_sources", "key", "max_minutes", "min_group_size",
             "paused", "history", "stages"]
    ordered = {k: d[k] for k in order if k in d}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)


# ── Compiling ───────────────────────────────────────────────────────────────

@dataclass
class Col:
    name: str
    type: str
    classification: str
    personal: bool
    sources: list = field(default_factory=list)     # [(field_id | None, "dataset.felt")]
    transform: str = ""
    mask: str | None = None

    def out(self) -> dict:
        return {"name": self.name, "type": self.type, "classification": self.classification, "personal": self.personal,
                "sources": [label for _, label in self.sources], "transform": self.transform, "mask": self.mask}


@dataclass
class Compiled:
    ctes: list = field(default_factory=list)          # sql.Composed, one per stage
    stage_cols: list = field(default_factory=list)    # [list[Col]] after each stage
    warnings: list = field(default_factory=list)
    datasets: list = field(default_factory=list)      # dataset ids read
    groups: list = field(default_factory=list)        # (stage index, before-stage index, by columns)
    key: str | None = None

    @property
    def columns(self) -> list[Col]:
        return self.stage_cols[-1]

    def query(self, upto: int | None = None) -> sql.Composed:
        n = len(self.ctes) if upto is None else upto + 1
        ctes = sql.SQL(", ").join(sql.SQL("{} as ({})").format(sql.Identifier(f"s{i + 1}"), c) for i, c in enumerate(self.ctes[:n]))
        return sql.SQL("with {} select * from {}").format(ctes, sql.Identifier(f"s{n}"))

    def output(self) -> list[dict]:
        return [{"name": c.name, "type": c.type, "classification": c.classification, "personal": c.personal} for c in self.columns]


async def _resolve(conn, ref) -> tuple[dict, dict | None, list[dict]]:
    """A dataset named in a stage: its row, its source (connection) and its fields."""
    if isinstance(ref, dict) and "dataset" in ref:
        rows = await db.fetch_all(conn, "select d.* from datasets d where d.id = %s", (int(ref["dataset"]),))
    else:
        name = str(ref)
        rows = await db.fetch_all(conn, """select d.* from datasets d left join data_sources s on s.id = d.source_id
                                           where d.key = %s or (s.key || '.' || d.key) = %s or d.key = 'snapshots.' || %s
                                           order by d.id""", (name, name, name))
        if len(rows) > 1:
            exact = [r for r in rows if r["kind"] != "snapshot" and r["source_id"] is None and r["key"] == name]
            rows = exact or rows
        if len(rows) > 1:
            raise PipelineError(f"{name} findes i flere kilder. Skriv forbindelse.tabel, fx {rows[0]['key']}")
    if not rows:
        raise PipelineError(f"Datasættet {ref} findes ikke i kataloget")
    d = rows[0]
    if d["kind"] in ("api", "live"):
        raise PipelineError(f"{d['name']} er en API-kilde. Pipelines læser databaser og snapshots; brug en multisource-kilde")
    source = await db.fetch_one(conn, "select id, key, kind, spec_yaml from data_sources where id = %s", (d["source_id"],)) \
        if d["source_id"] else None
    fields = await db.fetch_all(conn, """select id, name, data_type, classification, personal_data, mask from dataset_fields
                                         where dataset_id = %s order by position, id""", (d["id"],))
    return d, source, [f for f in fields if not connections.SECRETISH.search(f["name"])]


def _col(cols: list[Col], name, stage: str) -> Col:
    for c in cols:
        if c.name == name:
            return c
    raise PipelineError(f"{stage}: feltet {name} findes ikke her. Felter: {', '.join(c.name for c in cols)}")


def _merge(name: str, typ: str, parts: list[Col], transform: str) -> Col:
    return Col(name, typ, masking.strictest([p.classification for p in parts]), any(p.personal for p in parts),
               [s for p in parts for s in p.sources], transform)


def _cond(cols: list[Col], cond, stage: str) -> sql.Composable:
    if not isinstance(cond, dict) or not cond:
        raise PipelineError(f"{stage}: en betingelse er et objekt som {{felt: værdi}}")
    parts = []
    for k, v in cond.items():
        if k in ("$or", "$and"):
            if not isinstance(v, list) or not v:
                raise PipelineError(f"{stage}: {k} skal være en liste af betingelser")
            parts.append(sql.SQL("(") + sql.SQL(" or " if k == "$or" else " and ").join(_cond(cols, x, stage) for x in v) + sql.SQL(")"))
            continue
        col = sql.Identifier(_col(cols, k, stage).name)
        if isinstance(v, dict):
            for op, val in v.items():
                if op in OPS:
                    parts.append(sql.SQL("{} {} {}").format(col, sql.SQL(OPS[op]), sql.Literal(val)))
                elif op == "$in":
                    if not isinstance(val, list):
                        raise PipelineError(f"{stage}: $in skal være en liste")
                    parts.append(sql.SQL("{} = any({})").format(col, sql.Literal(val)))
                elif op == "$exists":
                    parts.append(sql.SQL("{} is not null" if val else "{} is null").format(col))
                elif op == "$like":
                    parts.append(sql.SQL("{}::text ilike {}").format(col, sql.Literal(str(val))))
                else:
                    raise PipelineError(f"{stage}: ukendt operator {op}")
        elif v is None:
            parts.append(sql.SQL("{} is null").format(col))
        else:
            parts.append(sql.SQL("{} = {}").format(col, sql.Literal(v)))
    return sql.SQL(" and ").join(parts)


EXPR_OPS = {"$add": "+", "$subtract": "-", "$multiply": "*", "$divide": "/"}


def _expr(cols: list[Col], e, stage: str) -> tuple[sql.Composable, list[Col], str]:
    """An expression → (SQL, the columns it reads, its type)."""
    if isinstance(e, str) and e.startswith("$"):
        c = _col(cols, e[1:], stage)
        return sql.Identifier(c.name), [c], c.type
    if isinstance(e, (int, float)) and not isinstance(e, bool):
        return sql.Literal(e), [], "numeric"
    if isinstance(e, (str, bool)) or e is None:
        return sql.Literal(e), [], "text" if isinstance(e, str) else "boolean"
    if isinstance(e, dict) and len(e) == 1:
        (op, args), = e.items()
        args = args if isinstance(args, list) else [args]
        parts = [_expr(cols, a, stage) for a in args]
        used = [c for p in parts for c in p[1]]
        if op in EXPR_OPS and len(parts) >= 2:
            if op == "$divide":
                return sql.SQL("({} / nullif({}, 0))").format(parts[0][0], parts[1][0]), used, "numeric"
            return sql.SQL("(") + sql.SQL(f" {EXPR_OPS[op]} ").join(p[0] for p in parts) + sql.SQL(")"), used, "numeric"
        if op == "$concat":
            return sql.SQL("concat({})").format(sql.SQL(", ").join(p[0] for p in parts)), used, "text"
        if op == "$round" and 1 <= len(parts) <= 2:
            places = parts[1][0] if len(parts) == 2 else sql.Literal(0)
            return sql.SQL("round(({})::numeric, {})").format(parts[0][0], places), used, "numeric"
        if op in ("$lower", "$upper") and len(parts) == 1:
            return sql.SQL(f"{op[1:]}(({{}})::text)").format(parts[0][0]), used, "text"
        if op == "$coalesce" and parts:
            return sql.SQL("coalesce({})").format(sql.SQL(", ").join(p[0] for p in parts)), used, parts[0][2]
        if op == "$year" and len(parts) == 1:
            return sql.SQL("extract(year from {})::int").format(parts[0][0]), used, "integer"
        if op == "$abs" and len(parts) == 1:
            return sql.SQL("abs({})").format(parts[0][0]), used, "numeric"
    raise PipelineError(f"{stage}: udtrykket {json.dumps(e, ensure_ascii=False)[:80]} forstås ikke. Brug $felt, tal, tekst, "
                        "$add, $subtract, $multiply, $divide, $concat, $round, $lower, $upper, $coalesce, $year, $abs")


def _select(cols: list[Col], prev: str, alias: str = "p") -> sql.Composable:
    return sql.SQL(", ").join(sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(c.name)) for c in cols)


AGG = re.compile(r"^(count|sum|avg|min|max|count_distinct)(?:\((\w+)\))?$")


async def compile_definition(conn, definition: dict) -> Compiled:
    out = Compiled(key=definition.get("key"))
    cols: list[Col] = []
    source_kind = None
    for i, stage in enumerate(definition["stages"]):
        (kind, body), = stage.items()
        label = f"Trin {i + 1} ({kind})"
        prev = sql.Identifier(f"s{i}")
        if kind == "source":
            d, src, fields = await _resolve(conn, body)
            schema, table = connections.table_ref(d, src)
            out.datasets.append(d["id"])
            source_kind = d["kind"]
            cols = [Col(f["name"], f["data_type"], f["classification"], f["personal_data"], [(f["id"], f"{d['key']}.{f['name']}")],
                        "", f["mask"]) for f in fields]
            if not cols:
                raise PipelineError(f"{d['name']} har ingen felter i kataloget")
            q = sql.SQL("select {} from {}.{}").format(sql.SQL(", ").join(sql.Identifier(c.name) for c in cols),
                                                         sql.Identifier(schema), sql.Identifier(table))
        elif kind == "match":
            q = sql.SQL("select * from {} where {}").format(prev, _cond(cols, body, label))
        elif kind == "lookup":
            if not isinstance(body, dict) or not {"from", "local", "foreign"} <= set(body):
                raise PipelineError(f"{label}: lookup skal have from, local og foreign")
            d, src, fields = await _resolve(conn, body["from"])
            schema, table = connections.table_ref(d, src)
            out.datasets.append(d["id"])
            local = _col(cols, body["local"], label)
            by_name = {f["name"]: f for f in fields}
            if body["foreign"] not in by_name:
                raise PipelineError(f"{label}: {body['foreign']} findes ikke i {d['name']}")
            wanted = body.get("fields") or [f["name"] for f in fields if f["name"] != body["foreign"]]
            prefix = str(body.get("as") or d["key"].split(".")[-1])
            new_cols, picks = [], []
            for name in wanted:
                f = by_name.get(name)
                if f is None:
                    raise PipelineError(f"{label}: {name} findes ikke i {d['name']}")
                out_name = name if not any(c.name == name for c in cols) else f"{prefix}_{name}"
                if not NAME.match(out_name):
                    raise PipelineError(f"{label}: {out_name} er ikke et gyldigt feltnavn")
                picks.append(sql.SQL("f.{} as {}").format(sql.Identifier(name), sql.Identifier(out_name)))
                new_cols.append(Col(out_name, f["data_type"], f["classification"], f["personal_data"],
                                    [(f["id"], f"{d['key']}.{name}")], "$lookup", f["mask"]))
            inner = sql.SQL("select * from {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            if body.get("where"):
                foreign_cols = [Col(f["name"], f["data_type"], f["classification"], f["personal_data"]) for f in fields]
                inner = inner + sql.SQL(" where ") + _cond(foreign_cols, body["where"], label)
            elif d["kind"] == "foreign" or d["kind"] != source_kind:
                out.warnings.append(f"{d['name']} er ikke indsnævret før opslaget. Hele datasættet hentes over forbindelsen. "
                                    "Tilføj et filter (where) før opslaget")
            q = sql.SQL("select {}, {} from {} p left join ({}) f on p.{} = f.{}").format(
                _select(cols, ""), sql.SQL(", ").join(picks) if picks else sql.SQL("null as _none"), prev, inner,
                sql.Identifier(local.name), sql.Identifier(body["foreign"]))
            cols = cols + new_cols
        elif kind == "unwind":
            path = body.get("path") if isinstance(body, dict) else body
            path = str(path).lstrip("$")
            c = _col(cols, path, label)
            fn = "jsonb_array_elements_text({}.{}::jsonb)" if c.type in ("json", "jsonb") else "unnest({}.{})"
            rest = [x for x in cols if x.name != path]
            q = sql.SQL("select {}{} x.value as {} from {} p cross join lateral " + fn + " as x(value)").format(
                _select(rest, ""), sql.SQL(", ") if rest else sql.SQL(""), sql.Identifier(path), prev,
                sql.Identifier("p"), sql.Identifier(path))
            cols = rest + [Col(path, "text", c.classification, c.personal, c.sources, "$unwind")]
        elif kind in ("addFields", "set"):
            if not isinstance(body, dict) or not body:
                raise PipelineError(f"{label}: {{navn: udtryk}}")
            adds, new_cols = [], []
            for name, e in body.items():
                if not NAME.match(str(name)):
                    raise PipelineError(f"{label}: {name} er ikke et gyldigt feltnavn")
                expr, used, typ = _expr(cols, e, label)
                adds.append(sql.SQL("{} as {}").format(expr, sql.Identifier(name)))
                new_cols.append(_merge(name, typ, used, f"${kind}") if used else Col(name, typ, "white", False, [], f"${kind}"))
            keep = [c for c in cols if c.name not in body]
            q = sql.SQL("select {}{}{} from {} p").format(_select(keep, ""), sql.SQL(", ") if keep else sql.SQL(""),
                                                          sql.SQL(", ").join(adds), prev)
            cols = keep + new_cols
        elif kind == "project":
            picks, new_cols = [], []
            spec = {n: 1 for n in body} if isinstance(body, list) else body
            if not isinstance(spec, dict) or not spec:
                raise PipelineError(f"{label}: en liste af felter eller {{felt: 1|0|\"$kilde\"}}")
            excluding = all(v == 0 for v in spec.values())
            if excluding:
                for c in cols:
                    if c.name not in spec:
                        picks.append(sql.Identifier(c.name))
                        new_cols.append(c)
            else:
                for name, v in spec.items():
                    if v == 0:
                        continue
                    src = _col(cols, v[1:] if isinstance(v, str) and v.startswith("$") else name, label)
                    if not NAME.match(str(name)):
                        raise PipelineError(f"{label}: {name} er ikke et gyldigt feltnavn")
                    picks.append(sql.SQL("{} as {}").format(sql.Identifier(src.name), sql.Identifier(name)))
                    new_cols.append(Col(name, src.type, src.classification, src.personal, src.sources,
                                        src.transform if src.name == name else f"$project {src.name}", src.mask))
            q = sql.SQL("select {} from {}").format(sql.SQL(", ").join(picks), prev)
            cols = new_cols
        elif kind == "group":
            if not isinstance(body, dict) or "by" not in body:
                raise PipelineError(f"{label}: group skal have by og mindst ét aggregat")
            by = body["by"] if isinstance(body["by"], list) else [body["by"]]
            by_cols = [_col(cols, b, label) for b in by]
            picks = [sql.Identifier(c.name) for c in by_cols]
            new_cols = [Col(c.name, c.type, c.classification, c.personal, c.sources, "$group", c.mask) for c in by_cols]
            for name, spec in body.items():
                if name == "by":
                    continue
                m = AGG.match(str(spec).replace(" ", ""))
                if not m or not NAME.match(str(name)):
                    raise PipelineError(f"{label}: {name}: {spec} — brug count, sum(felt), avg(felt), min(felt), max(felt), count_distinct(felt)")
                fn, arg = m.groups()
                if fn == "count" and not arg:
                    picks.append(sql.SQL("count(*) as {}").format(sql.Identifier(name)))
                    first = cols[0]
                    new_cols.append(Col(name, "bigint", "white", False, first.sources[:1], "count"))
                    continue
                if not arg:
                    raise PipelineError(f"{label}: {fn} skal have et felt, fx {fn}(aarlig_leje)")
                c = _col(cols, arg, label)
                if fn == "count_distinct":
                    picks.append(sql.SQL("count(distinct {}) as {}").format(sql.Identifier(c.name), sql.Identifier(name)))
                    new_cols.append(Col(name, "bigint", "white", False, c.sources, f"count_distinct({c.name})"))
                    continue
                if fn in ("sum", "avg") and c.type not in NUMERIC:
                    raise PipelineError(f"{label}: {fn} kræver et tal, men {c.name} er {c.type}")
                picks.append(sql.SQL("{}({}) as {}").format(sql.SQL(fn), sql.Identifier(c.name), sql.Identifier(name)))
                typ = "numeric" if fn in ("sum", "avg") else c.type
                new_cols.append(Col(name, typ, c.classification, c.personal, c.sources, f"{fn}({c.name})", c.mask))
            q = sql.SQL("select {} from {} group by {}").format(sql.SQL(", ").join(picks), prev,
                                                                sql.SQL(", ").join(sql.Identifier(c.name) for c in by_cols))
            out.groups.append((i, i - 1, [c.name for c in by_cols]))
            cols = new_cols
        elif kind == "sort":
            if not isinstance(body, dict) or not body:
                raise PipelineError(f"{label}: {{felt: 1}} eller {{felt: -1}}")
            order = sql.SQL(", ").join(sql.SQL("{} {} nulls last").format(sql.Identifier(_col(cols, k, label).name),
                                                                          sql.SQL("desc" if v == -1 else "asc")) for k, v in body.items())
            q = sql.SQL("select * from {} order by {}").format(prev, order)
        elif kind == "limit":
            try:
                n = int(body)
            except (TypeError, ValueError):
                raise PipelineError(f"{label}: limit skal være et tal")
            q = sql.SQL("select * from {} limit {}").format(prev, sql.Literal(max(0, n)))
        elif kind == "unionWith":
            ref = body.get("source") if isinstance(body, dict) else body
            d, src, fields = await _resolve(conn, ref)
            schema, table = connections.table_ref(d, src)
            out.datasets.append(d["id"])
            by_name = {f["name"]: f for f in fields}
            missing = [c.name for c in cols if c.name not in by_name]
            if missing:
                raise PipelineError(f"{label}: {d['name']} mangler {', '.join(missing)} (samme struktur kræves)")
            q = sql.SQL("select {} from {} union all select {} from {}.{}").format(
                sql.SQL(", ").join(sql.Identifier(c.name) for c in cols), prev,
                sql.SQL(", ").join(sql.Identifier(c.name) for c in cols), sql.Identifier(schema), sql.Identifier(table))
            cols = [Col(c.name, c.type, masking.strictest([c.classification, by_name[c.name]["classification"]]),
                        c.personal or by_name[c.name]["personal_data"],
                        c.sources + [(by_name[c.name]["id"], f"{d['key']}.{c.name}")], "$unionWith", c.mask) for c in cols]
        elif kind == "mask":
            if not isinstance(body, dict) or not body:
                raise PipelineError(f"{label}: {{felt: strategi}}")
            picks, new_cols = [], []
            for c in cols:
                strategy = body.get(c.name)
                if strategy is None:
                    picks.append(sql.Identifier(c.name))
                    new_cols.append(c)
                    continue
                if strategy not in masking.STRICTNESS:
                    raise PipelineError(f"{label}: ukendt maskering {strategy}")
                if strategy in ("remove", "aggregate"):
                    continue
                picks.append(sql.SQL("{} as {}").format(_mask_sql(c, strategy), sql.Identifier(c.name)))
                new_cols.append(Col(c.name, "text" if strategy not in ("show", "round") else c.type, c.classification,
                                    c.personal, c.sources, f"mask:{strategy}", strategy))
            for name in body:
                _col(cols, name, label)
            q = sql.SQL("select {} from {}").format(sql.SQL(", ").join(picks), prev)
            cols = new_cols
        else:  # pragma: no cover
            raise PipelineError(f"{label}: ukendt trin")
        names = [c.name for c in cols]
        if len(set(names)) != len(names):
            raise PipelineError(f"{label}: to felter hedder det samme")
        out.ctes.append(q)
        out.stage_cols.append(list(cols))
    if out.key and out.key not in {c.name for c in out.columns}:
        raise PipelineError(f"key: {out.key} er ikke et felt i resultatet")
    return out


def _mask_sql(c: Col, strategy: str) -> sql.Composable:
    col = sql.Identifier(c.name)
    if strategy == "show":
        return col
    if strategy == "mask":
        return sql.SQL("case when {} is null then null else {} end").format(col, sql.Literal(masking.MASK))
    if strategy == "hash":
        return sql.SQL("'h:' || left(encode(sha256(convert_to({} || {}::text, 'UTF8')), 'hex'), 16)").format(
            sql.Literal(config.settings.secret_key[:16]), col)
    if strategy == "partial":
        return sql.SQL("case when {} is null then null else left({}::text, 1) || {} end").format(col, col, sql.Literal("•••"))
    if strategy == "round" and c.type in NUMERIC:
        return sql.SQL("round(({})::numeric, -1)").format(col)
    if strategy == "interval" and c.type in NUMERIC:
        return sql.SQL("(floor(({0})::numeric / 1000) * 1000)::text || '-' || ((floor(({0})::numeric / 1000) + 1) * 1000)::text").format(col)
    return sql.SQL("case when {} is null then null else {} end").format(col, sql.Literal(masking.MASK))


def stage_policy(cols: list[Col]) -> dict[str, str]:
    """The masking for a preview of these columns: the catalog's own choice, else the classification's default."""
    return {c.name: (c.mask if c.mask and masking.STRICTNESS.get(c.mask, 0) >= masking.MINIMUM[masking.effective_class(
        {"classification": c.classification, "personal_data": c.personal})] else masking.default_for(
        {"classification": c.classification, "personal_data": c.personal})) for c in cols}


# ── Preview, small groups ───────────────────────────────────────────────────

async def _read(q: sql.Composable, params=None, *, timeout_ms: int, limit: int | None = None) -> list[dict]:
    async with db.analytics_connection() as conn:
        await conn.execute("set transaction read only")
        await conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(f"{timeout_ms}ms")))
        try:
            return await db.fetch_all(conn, q if limit is None else sql.SQL("select * from ({}) q limit {}").format(q, sql.Literal(limit)), params)
        except psycopg.errors.QueryCanceled:
            raise HTTPException(422, f"Forhåndsvisningen tog mere end {timeout_ms // 1000} sekunder og blev stoppet")
        except psycopg.Error as e:
            raise HTTPException(422, f"Databasen afviste forespørgslen: {str(e).splitlines()[0][:200]}")


async def small_groups(compiled: Compiled, min_size: int, *, timeout_ms: int) -> int:
    """PL-15: how many groups have fewer than min_size rows (summed over every group stage)."""
    total = 0
    for _, before, by in compiled.groups:
        inner = compiled.query(before)
        q = sql.SQL("select count(*) as n from (select 1 from ({}) x group by {} having count(*) < {}) g").format(
            inner, sql.SQL(", ").join(sql.Identifier(b) for b in by), sql.Literal(min_size))
        rows = await _read(q, timeout_ms=timeout_ms)
        total += rows[0]["n"] if rows else 0
    return total


def sensitive_output(compiled: Compiled) -> list[str]:
    return [c.name for c in compiled.columns if c.personal or c.classification == "red"]


async def preview(compiled: Compiled, stage: int | None = None) -> dict:
    """PL-4: sample rows after a stage, masked (F-10), and the SQL."""
    s = config.settings
    idx = len(compiled.ctes) - 1 if stage is None else max(0, min(stage, len(compiled.ctes) - 1))
    cols = compiled.stage_cols[idx]
    rows = await _read(compiled.query(idx), timeout_ms=s.pipeline_preview_timeout_ms, limit=s.pipeline_preview_rows)
    policy = stage_policy(cols)
    async with db.analytics_connection() as conn:
        text = compiled.query(idx).as_string(conn)
    return {"stage": idx, "columns": [c.out() | {"policy": policy[c.name]} for c in cols],
            "rows": masking.mask_rows([{k: _plain(v) for k, v in r.items()} for r in rows], policy), "sql": text}


def _plain(v):
    if isinstance(v, (bytes, memoryview)):
        return "<binær>"
    return v


# ── Saving: dependencies and the contract ──────────────────────────────────

async def prepare(conn, definition: dict) -> tuple[dict, Compiled]:
    """Compiles and stores what the artifact needs next to the definition: the output (contract) and the datasets."""
    compiled = await compile_definition(conn, definition)
    return {**definition, "_output": compiled.output(), "_datasets": sorted(set(compiled.datasets)),
            "_warnings": compiled.warnings}, compiled


def contract(definition: dict) -> dict:
    return {"fields": {c["name"]: {"type": c["type"], "classification": c["classification"], "personal": c["personal"]}
                       for c in definition.get("_output") or []}, "key": definition.get("key")}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    out = []
    for r in await db.fetch_all(conn, """select d.key, d.artifact_id, d.pipeline_artifact_id, s.artifact_id as source_artifact
                                         from datasets d left join data_sources s on s.id = d.source_id where d.id = any(%s)""",
                                (definition.get("_datasets") or [],)):
        for aid in (r["artifact_id"], r["pipeline_artifact_id"], r["source_artifact"]):
            if aid:
                out.append((aid, r["key"]))
    return out


async def check_cycles(conn, artifact_id: int | None, definition: dict) -> None:
    """PL-12: pipelines may depend on each other; a cycle is refused."""
    direct = {r["pipeline_artifact_id"] for r in await db.fetch_all(
        conn, "select pipeline_artifact_id from datasets where id = any(%s) and pipeline_artifact_id is not null",
        (definition.get("_datasets") or [],))}
    if artifact_id is None:
        return
    seen, stack = set(), list(direct)
    while stack:
        a = stack.pop()
        if a == artifact_id:
            raise PipelineError("Pipelinen afhænger af sig selv gennem andre pipelines (cirkulær afhængighed)")
        if a in seen:
            continue
        seen.add(a)
        rows = await db.fetch_all(conn, """select d.pipeline_artifact_id from dev_artifacts a
                                           join dev_artifact_dependencies dep on dep.version_id = a.current_version_id
                                           join datasets d on d.pipeline_artifact_id = dep.depends_on
                                           where a.id = %s""", (a,))
        stack += [r["pipeline_artifact_id"] for r in rows if r["pipeline_artifact_id"]]


async def gate(conn, artifact: dict, v: dict) -> str | None:
    """PL-15 at submit: personal or red output with groups below the minimum cannot be published."""
    compiled = await compile_definition(conn, v["definition"])
    if not compiled.groups or not sensitive_output(compiled):
        return None
    n = await small_groups(compiled, v["definition"].get("min_group_size", MIN_GROUP),
                           timeout_ms=config.settings.pipeline_preview_timeout_ms * 4)
    if n:
        return (f"{n} grupper har færre end {v['definition'].get('min_group_size', MIN_GROUP)} rækker, og resultatet indeholder "
                f"persondata eller røde felter ({', '.join(sensitive_output(compiled))}). Filtrér de små grupper fra, før versionen kan udgives")
    return None


# ── Time ────────────────────────────────────────────────────────────────────

def now_local() -> datetime:
    return datetime.now(TZ)


def at(day, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), TZ)


def next_run(definition: dict, last: datetime | None) -> datetime | None:
    s = definition.get("schedule") or {}
    now = now_local()
    if definition.get("paused") or s.get("type") == "manual":
        return None
    if s.get("type") == "hourly":
        return (last + timedelta(hours=1)) if last else now
    today = at(now.date(), s.get("earliest", "06:00"))
    if last and last.astimezone(TZ).date() >= now.date():
        return at(now.date() + timedelta(days=1), s.get("earliest", "06:00"))
    return today if now <= today else now

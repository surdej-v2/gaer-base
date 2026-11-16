"""Help in the pipeline editor (specs/developer/AI.md, fase 2). Everything here is rules on the compiled pipeline, so it
works without AI (AI-4); AI may offer a better completion, which is checked by compiling it.

- hints(): changes that give the same result faster. Today: a $match that only reads fields from before a $lookup can
  move above it — the lookup is a left join that never overwrites a column (pipelines.compile_definition prefixes
  clashes), so filtering first gives the same rows and the lookup reads fewer.
- describe(): what a stage does, in a sentence of Danish.
- completion(): the next thing a stage could hold, as grey text in the editor ("Tab tager forslaget").
"""

import copy
import json

from app.devkit import assist, pipelines

MOVABLE_OVER = ("lookup", "sort")
OPS_DA = {"$eq": "=", "$ne": "≠", "$gt": ">", "$gte": "≥", "$lt": "<", "$lte": "≤"}
AGG_DA = {"count": "antal", "sum": "summen af", "avg": "gennemsnittet af", "min": "mindste", "max": "største",
          "count_distinct": "antal forskellige"}


def _kind(stage: dict) -> str:
    return next(iter(stage))


def _body(stage: dict):
    return next(iter(stage.values()))


def match_fields(cond) -> set[str] | None:
    """The field names a $match reads, or None when its shape is not understood (then no hint is given)."""
    if not isinstance(cond, dict) or not cond:
        return None
    out: set[str] = set()
    for k, v in cond.items():
        if k in ("$or", "$and"):
            if not isinstance(v, list) or not v:
                return None
            for x in v:
                sub = match_fields(x)
                if sub is None:
                    return None
                out |= sub
        elif str(k).startswith("$"):
            return None
        else:
            out.add(str(k))
    return out


def hints(definition: dict, compiled: pipelines.Compiled) -> list[dict]:
    stages = definition["stages"]
    out = []
    for i, st in enumerate(stages):
        if _kind(st) != "match" or i < 2:
            continue
        fields = match_fields(_body(st))
        if not fields:
            continue
        j = i
        while j - 1 >= 1 and _kind(stages[j - 1]) in MOVABLE_OVER and fields <= {c.name for c in compiled.stage_cols[j - 2]}:
            j -= 1
        passed = [stages[k] for k in range(j, i)]
        lookups = [_body(s).get("from") for s in passed if _kind(s) == "lookup" and isinstance(_body(s), dict)]
        if not lookups:
            continue
        moved = stages[:j] + [st] + stages[j:i] + stages[i + 1:]
        names = ", ".join(sorted(fields))
        out.append({"key": f"match_before_lookup:{i}", "kind": "match_before_lookup", "from": i, "to": j,
                    "title": f"Flyt filtret (trin {i + 1}) op før trin {j + 1}",
                    "text": f"Filtret læser kun {names}, som findes før opslaget i {', '.join(str(x) for x in lookups)}. "
                            "Flyttet op giver det samme resultat, og opslaget læser færre rækker.",
                    "stages": moved})
    return out


def _cond_da(cond) -> str:
    parts = []
    for k, v in (cond or {}).items():
        if k in ("$or", "$and"):
            word = " eller " if k == "$or" else " og "
            parts.append("(" + word.join(_cond_da(x) for x in v if isinstance(x, dict)) + ")")
        elif isinstance(v, dict):
            for op, val in v.items():
                if op in OPS_DA:
                    parts.append(f"{k} {OPS_DA[op]} {val}")
                elif op == "$in":
                    parts.append(f"{k} er en af {', '.join(str(x) for x in val) if isinstance(val, list) else val}")
                elif op == "$exists":
                    parts.append(f"{k} {'har en værdi' if val else 'er tom'}")
                elif op == "$like":
                    parts.append(f"{k} ligner {val}")
        elif v is None:
            parts.append(f"{k} er tom")
        else:
            parts.append(f"{k} = {v}")
    return " og ".join(parts) or "betingelsen"


def describe(stage: dict) -> str:
    kind, body = _kind(stage), _body(stage)
    try:
        if kind == "source":
            return f"Læser datasættet {body.get('dataset') if isinstance(body, dict) else body}."
        if kind == "match":
            return f"Beholder de rækker, hvor {_cond_da(body)}."
        if kind == "lookup":
            fields = ", ".join(body.get("fields") or []) or "alle felter"
            return (f"Slår {fields} op i {body.get('from')}, hvor {body.get('local')} = {body.get('foreign')}. "
                    "Rækker uden et match beholdes.")
        if kind == "unwind":
            path = (body.get("path") if isinstance(body, dict) else body) or ""
            return f"Folder {str(path).lstrip('$')} ud til én række pr. værdi."
        if kind in ("addFields", "set"):
            return f"Tilføjer {', '.join(body)}."
        if kind == "project":
            spec = {n: 1 for n in body} if isinstance(body, list) else body
            if all(v == 0 for v in spec.values()):
                return f"Fjerner {', '.join(spec)}."
            return f"Beholder kun {', '.join(k for k, v in spec.items() if v != 0)}."
        if kind == "group":
            by = body["by"] if isinstance(body["by"], list) else [body["by"]]
            aggs = []
            for name, spec in body.items():
                if name == "by":
                    continue
                m = pipelines.AGG.match(str(spec).replace(" ", ""))
                if m:
                    fn, arg = m.groups()
                    aggs.append(f"{name} ({AGG_DA[fn]}{' ' + arg if arg else ' rækker'})")
            return f"Samler rækkerne pr. {', '.join(by)} og beregner {', '.join(aggs) or 'ingenting endnu'}."
        if kind == "sort":
            return "Sorterer efter " + ", ".join(f"{k} ({'faldende' if v == -1 else 'stigende'})" for k, v in body.items()) + "."
        if kind == "limit":
            return f"Beholder de første {body} rækker."
        if kind == "unionWith":
            return f"Lægger rækkerne fra {body.get('source') if isinstance(body, dict) else body} til."
        if kind == "mask":
            return f"Maskerer {', '.join(body) if isinstance(body, (list, dict)) else body}."
    except (AttributeError, KeyError, TypeError):
        pass
    return pipelines.STAGE_DA.get(kind, kind)


def _idlike(name: str) -> bool:
    return name == "id" or name.endswith("_id") or name.endswith("_nr")


def heuristic_completion(definition: dict, compiled: pipelines.Compiled, index: int) -> dict | None:
    """group: sum a numeric field that is not aggregated yet. project: keep a field that is left out."""
    if index < 1 or index >= len(definition["stages"]) or index > len(compiled.stage_cols) - 1:
        return None
    stage = definition["stages"][index]
    kind, body = _kind(stage), _body(stage)
    before = compiled.stage_cols[index - 1]
    if kind == "group" and isinstance(body, dict):
        used = {str(v) for k, v in body.items() if k != "by"} | {str(x) for x in (body["by"] if isinstance(body.get("by"), list) else [body.get("by")])}
        for c in before:
            if c.type in pipelines.NUMERIC and not _idlike(c.name) and not any(c.name in u for u in used):
                name = f"{c.name}_i_alt"
                if name not in body:
                    return {"key": name, "value": f"sum({c.name})", "why": f"{c.name} er et tal, der ikke er lagt sammen endnu"}
    if kind == "project":
        names = list(body) if isinstance(body, (list, dict)) else []
        if isinstance(body, dict) and body and all(v == 0 for v in body.values()):
            return None
        for c in before:
            if c.name not in names:
                return {"item": c.name, "why": f"{c.name} findes før trinnet, men kommer ikke med"} if isinstance(body, list) \
                    else {"key": c.name, "value": 1, "why": f"{c.name} findes før trinnet, men kommer ikke med"}
    return None


def apply_completion(stage: dict, completion: dict) -> dict:
    kind, body = _kind(stage), copy.deepcopy(_body(stage))
    if "item" in completion and isinstance(body, list):
        body.append(completion["item"])
    elif "key" in completion and isinstance(body, dict):
        body[completion["key"]] = completion["value"]
    return {kind: body}


async def ai_completion(conn, definition: dict, compiled: pipelines.Compiled, index: int, user_id: int) -> dict | None:
    """The AI's addition to a stage, kept only if the pipeline still compiles with it. None without AI."""
    stage = definition["stages"][index]
    kind = _kind(stage)
    if kind not in ("group", "project", "addFields", "set", "match", "sort"):
        return None
    before = [{"navn": c.name, "type": c.type} for c in compiled.stage_cols[index - 1]] if index > 0 else []
    text = await assist.complete(
        assist.SYSTEM + " Foreslå ÉN tilføjelse til et trin i en pipeline (MongoDB-lignende). Svar kun som JSON: "
        "{\"key\": \"navn\", \"value\": …, \"why\": \"…\"} for et objekt-trin eller {\"item\": \"felt\", \"why\": \"…\"} for et "
        "liste-trin. Aggregater skrives som count, sum(felt), avg(felt), min(felt), max(felt), count_distinct(felt).",
        json.dumps({"trin": stage, "felter_før_trinnet": before}, ensure_ascii=False), user_id=user_id, max_tokens=200)
    parsed = assist._json(text)
    if not isinstance(parsed, dict) or not ({"key", "value"} <= set(parsed) or "item" in parsed):
        return None
    completion = {k: parsed[k] for k in ("key", "value", "item") if k in parsed}
    completion["why"] = str(parsed.get("why") or "")[:200]
    trial = {**definition, "stages": [apply_completion(s, completion) if i == index else s
                                      for i, s in enumerate(definition["stages"])]}
    if trial["stages"][index] == stage:
        return None
    try:
        await pipelines.compile_definition(conn, trial)
    except pipelines.PipelineError:
        return None
    return {**completion, "ai": True}

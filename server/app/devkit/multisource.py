"""Multisource sources (DS-10 … DS-17): a declarative graph of steps that fetch live from several sources, in parallel
where they do not depend on each other, with pagination, foreach enrichment, batching, limits, retries and a cache per
step. Every step returns masked rows (operations mask; HTTP steps are masked by their dataset's fields), so masking is
applied before anything is merged, returned or cached (DS-17, F-10).

    {"description": "Lejemål med ejendom og lejer",
     "params": [{"name": "postnr", "type": "string"}],
     "key": "id", "partial": "mark",                                  # or "fail" (DS-15)
     "limits": {"max_items": 200, "deadline_ms": 15000, "timeout_ms": 5000, "retries": 2, "concurrency": 10},
     "steps": [
       {"id": "liste", "operation": 41, "args": {"postnr": "$param.postnr"}, "paginate": true, "ttl": 900},
       {"id": "ejendom", "operation": 42, "after": ["liste"], "join": {"local": "ejendom_id", "foreign": "id"}, "ttl": 0},
       {"id": "lejer", "foreach": "liste", "operation": 43, "args": {"id": "$item.lejer_id"}, "batch": {"param": "ids", "key": "id", "size": 20},
        "ttl": 300},
       {"id": "graph", "http": {"source": "graph", "dataset": "users"}, "paginate": {"type": "nextLink", "next": "@odata.nextLink"}}]}

Steps without `after`/`foreach` are roots: they run first, in parallel, and are merged on `key`. `after` + `join` merges a
step's rows into the result by local → foreign field; `foreach` calls the step once per element (with at most
`concurrency` calls at a time), or once per `batch.size` elements with an `in` parameter (`WHERE id = ANY(...)` in the
database, a batch call at the source). Pagination follows offset (operations), a cursor, or `nextLink` (Microsoft
Graph) up to `max_items`. Each call has a timeout, failures are retried with exponential backoff and jitter, and HTTP
429 waits for Retry-After; everything shares one deadline. `delegated: true` is the extension point for steps that run
with the caller's own token (Login-udbydere): it answers "ikke tilgængelig endnu", and its cache key already holds
the user (F-19).
"""

import asyncio
import json
import random
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import HTTPException

from app import data_sources, db, integrations, source_secrets
from app.devkit import artifacts, cache, masking, operations

DEFAULT_LIMITS = {"max_items": 200, "deadline_ms": 15000, "timeout_ms": 5000, "retries": 2, "concurrency": 10}
NOT_YET = "Delegeret adgang (brugerens eget token) er ikke tilgængelig endnu"

# Tests replace this with an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


class StepError(Exception):
    def __init__(self, message: str, retry_after: float | None = None, retryable: bool = True):
        super().__init__(message)
        self.message, self.retry_after, self.retryable = message, retry_after, retryable


# ── Validation ──────────────────────────────────────────────────────────────

async def validate(conn, definition: dict) -> dict:
    steps = definition.get("steps") or []
    if not steps:
        raise HTTPException(422, "En multisource-kilde skal have mindst ét trin")
    ids, out_steps = set(), []
    for s in steps:
        sid = str(s.get("id") or "")
        if not sid.isidentifier() or sid in ids:
            raise HTTPException(422, f"Trinnet {sid or '(uden id)'} skal have et unikt id")
        ids.add(sid)
    for s in steps:
        step = {"id": s["id"], "after": list(s.get("after") or []), "foreach": s.get("foreach"), "args": dict(s.get("args") or {}),
                "join": s.get("join"), "batch": s.get("batch"), "paginate": s.get("paginate") or False,
                "ttl": max(0, min(int(s.get("ttl", 0) or 0), 86_400)), "concurrency": s.get("concurrency"),
                "delegated": bool(s.get("delegated")), "fields": s.get("fields")}
        for dep in step["after"] + ([step["foreach"]] if step["foreach"] else []):
            if dep not in ids or dep == s["id"]:
                raise HTTPException(422, f"{s['id']}: afhænger af {dep}, som ikke findes")
        if s.get("operation"):
            op = await db.fetch_one(conn, f"select {artifacts.COLUMNS} from dev_artifacts a where a.id = %s and a.type = 'operation'",
                                    (int(s["operation"]),))
            if op is None:
                raise HTTPException(422, f"{s['id']}: operationen findes ikke")
            v = await artifacts.current(conn, op)
            if v is None:
                raise HTTPException(422, f"{s['id']}: operationen har ingen udgivet version")
            step["operation"] = op["id"]
            step["_output"] = v["definition"].get("_output") or []
            if step["batch"]:
                p = next((x for x in v["definition"].get("params") or [] if x["name"] == step["batch"].get("param")), None)
                if p is None or p["op"] != "in":
                    raise HTTPException(422, f"{s['id']}: batch kræver en parameter med op 'in' på operationen")
        elif s.get("http"):
            h = s["http"]
            src = await db.fetch_one(conn, "select id, key, spec_yaml from data_sources where key = %s and kind = 'rest'", (h.get("source"),))
            if src is None:
                raise HTTPException(422, f"{s['id']}: API-kilden {h.get('source')} findes ikke")
            try:
                ds = data_sources.dataset_spec(data_sources.parse_spec(src["spec_yaml"]), h.get("dataset"))
            except data_sources.SpecError as e:
                raise HTTPException(422, f"{s['id']}: {e}")
            d = await db.fetch_one(conn, "select id from datasets where source_id = %s and key = %s", (src["id"], ds["key"]))
            fields = await db.fetch_all(conn, "select name, data_type, classification, personal_data, mask from dataset_fields where dataset_id = %s",
                                        (d["id"],)) if d else []
            step["http"] = {"source": src["key"], "dataset": ds["key"]}
            step["_output"] = [{"name": f["name"], "type": f["data_type"], "classification": f["classification"],
                                "personal": f["personal_data"]} for f in fields]
            pg = step["paginate"]
            if pg and not (isinstance(pg, dict) and pg.get("type") in ("cursor", "nextLink", "offset")):
                raise HTTPException(422, f"{s['id']}: paginate skal have type cursor, nextLink eller offset")
        elif step["delegated"]:
            step["_output"] = []
        else:
            raise HTTPException(422, f"{s['id']}: et trin skal have operation, http eller delegated")
        if step["join"] and not (isinstance(step["join"], dict) and step["join"].get("local") and step["join"].get("foreign")):
            raise HTTPException(422, f"{s['id']}: join skal have local og foreign")
        out_steps.append(step)
    levels(out_steps)
    limits = {**DEFAULT_LIMITS, **{k: int(v) for k, v in (definition.get("limits") or {}).items() if k in DEFAULT_LIMITS}}
    limits["max_items"] = max(1, min(limits["max_items"], 5000))
    limits["concurrency"] = max(1, min(limits["concurrency"], 50))
    partial = definition.get("partial", "mark")
    if partial not in ("mark", "fail"):
        raise HTTPException(422, "partial skal være mark eller fail")
    fields = {}
    for st in out_steps:
        for o in st["_output"]:
            name = o["name"] if o["name"] not in fields else f"{st['id']}_{o['name']}"
            fields[name] = o
    return {"description": str(definition.get("description") or "")[:2000], "params": list(definition.get("params") or []),
            "key": definition.get("key") or "id", "partial": partial, "limits": limits, "swr": int(definition.get("swr") or 0),
            "steps": out_steps, "_output": [{"name": k, **{x: v[x] for x in ("type", "classification", "personal")}} for k, v in fields.items()]}


def levels(steps: list[dict]) -> list[list[dict]]:
    """The graph level by level (DS-11): steps on one level run in parallel. A cycle is refused."""
    by_id = {s["id"]: s for s in steps}
    deps = {s["id"]: set(s["after"]) | ({s["foreach"]} if s["foreach"] else set()) for s in steps}
    done, out = set(), []
    while len(done) < len(steps):
        ready = [by_id[i] for i, d in deps.items() if i not in done and d <= done]
        if not ready:
            raise HTTPException(422, "Trinnene afhænger af hinanden i ring")
        out.append(ready)
        done |= {s["id"] for s in ready}
    return out


def contract(definition: dict) -> dict:
    return {"fields": {o["name"]: {"type": o["type"], "classification": o["classification"], "personal": o["personal"]}
                       for o in definition.get("_output") or []},
            "params": {p["name"]: {"type": p.get("type", "string"), "required": bool(p.get("required"))} for p in definition.get("params") or []},
            "key": definition.get("key")}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    out = [(s["operation"], s["id"]) for s in definition.get("steps") or [] if s.get("operation")]
    for s in definition.get("steps") or []:
        if s.get("http"):
            src = await db.fetch_one(conn, "select artifact_id from data_sources where key = %s", (s["http"]["source"],))
            if src and src["artifact_id"]:
                out.append((src["artifact_id"], s["id"]))
    return out


artifacts.register("multisource", artifacts.TypeSpec(contract=contract, dependencies=dependencies,
                                                     other={"description": "Beskrivelse", "limits": "Grænser", "steps": "Trin",
                                                            "partial": "Delvise fejl", "swr": "Stale-while-revalidate"}))


# ── Running ─────────────────────────────────────────────────────────────────

def _resolve(value, params: dict, item: dict | None):
    if isinstance(value, str) and value.startswith("$param."):
        return params.get(value[7:])
    if isinstance(value, str) and value.startswith("$item."):
        return (item or {}).get(value[6:])
    return value


class _Run:
    def __init__(self, definition: dict, params: dict, principal, user_id: int | None, artifact_id: int, version: str):
        self.d, self.params, self.principal, self.user_id = definition, params, principal, user_id
        self.artifact_id, self.version = artifact_id, version
        self.limits = definition["limits"]
        self.deadline = time.monotonic() + self.limits["deadline_ms"] / 1000
        self.stats: dict[str, dict] = {}
        self.partial = False

    def left(self) -> float:
        return self.deadline - time.monotonic()

    async def _retry(self, fn):
        attempt = 0
        while True:
            if self.left() <= 0:
                raise StepError("Den samlede deadline er nået", retryable=False)
            try:
                return await asyncio.wait_for(fn(), timeout=min(self.limits["timeout_ms"] / 1000, self.left()))
            except asyncio.TimeoutError:
                err = StepError("Kaldet tog for lang tid")
            except StepError as e:
                err = e
            attempt += 1
            if not err.retryable or attempt > self.limits["retries"]:
                raise err
            wait = err.retry_after if err.retry_after is not None else (0.2 * 2 ** (attempt - 1)) * (0.5 + random.random())
            if wait >= self.left():
                raise StepError(f"{err.message} (ingen tid til at prøve igen)", retryable=False)
            await asyncio.sleep(wait)

    async def _cached(self, step: dict, args: dict, fetch) -> list[dict]:
        st = self.stats[step["id"]]
        if step["delegated"]:
            raise StepError(NOT_YET, retryable=False)
        key = None
        if step["ttl"]:
            gen = await cache.generation(f"artifact:{self.artifact_id}")
            key = cache.make_key(artifact_id=self.artifact_id, version=self.version, step=step["id"], params=args, gen=gen,
                                 user_id=self.user_id if step["delegated"] else None)
            value, status, _ = await cache.get(key, ttl_s=step["ttl"], max_stale_s=self.d.get("swr", 0))
            if value is not None:
                st["cache"] = status if st["cache"] in (None, status) else "delvis"
                return value
        rows = await self._retry(fetch)
        st["calls"] += 1
        st["cache"] = "miss" if st["cache"] in (None, "miss") else "delvis"
        if key:
            await cache.put(key, rows, ttl_s=step["ttl"], max_stale_s=self.d.get("swr", 0))
        return rows

    async def _operation(self, step: dict, args: dict, *, paginate: bool) -> list[dict]:
        async with db.connection() as conn:
            op = await artifacts.get(conn, step["operation"])
            v = await artifacts.current(conn, op)
        rows, offset = [], 0
        while True:
            async def call(offset=offset):
                try:
                    return await operations.execute(op, v, {**{k: x for k, x in args.items() if x is not None}, "offset": offset,
                                                            "limit": v["definition"]["max_limit"] if paginate else None},
                                                    principal=self.principal, use_cache=False)
                except HTTPException as e:
                    raise StepError(str(e.detail), retryable=e.status_code >= 424)
            out = await self._cached(step, {**args, "_offset": offset}, call)
            rows += out["rows"] if isinstance(out, dict) else out
            if not paginate or not (isinstance(out, dict) and out.get("hasMore")) or len(rows) >= self.limits["max_items"]:
                return rows[: self.limits["max_items"]]
            offset += len(out["rows"])

    async def _http(self, step: dict, args: dict) -> list[dict]:
        h = step["http"]
        async with db.connection() as conn:
            src = await db.fetch_one(conn, "select id, key, spec_yaml from data_sources where key = %s", (h["source"],))
            spec = data_sources.parse_spec(src["spec_yaml"])
            ds = data_sources.dataset_spec(spec, h["dataset"])
            secrets, _ = await source_secrets.resolve(conn, spec, source_id=src["id"])
        policy = masking.policy_for([{"name": o["name"], "classification": o["classification"], "personal_data": o["personal"]}
                                     for o in step["_output"]]) if step["_output"] else {f["name"]: masking.default_for(f) for f in ds["fields"]}
        try:
            headers = await data_sources.build_headers(spec, secrets)
        except data_sources.FetchError as e:
            raise StepError(e.message, retryable=False)
        pg = step["paginate"] if isinstance(step["paginate"], dict) else None
        query = {k: v for k, v in args.items() if v is not None and k in ds["params"]}
        url = spec["base_url"] + ds["path"] + (("?" + urlencode(query)) if query else "")
        rows: list[dict] = []
        while url:
            if urlsplit(url).hostname not in spec["hosts"]:
                raise StepError("nextLink peger uden for kildens værter", retryable=False)

            async def call(url=url):
                await data_sources.check_host(urlsplit(url).hostname)
                start = time.monotonic()
                async with httpx.AsyncClient(transport=transport, timeout=spec["limits"]["timeout"], follow_redirects=False) as client:
                    try:
                        r = await client.get(url, headers=headers)
                    except httpx.HTTPError:
                        raise StepError("Kunne ikke forbinde til kilden")
                await integrations.record_source_call(spec["key"], "GET", ds["path"], (time.monotonic() - start) * 1000, r.status_code)
                if r.status_code == 429:
                    try:
                        after = float(r.headers.get("retry-after", "1"))
                    except ValueError:
                        after = 1.0
                    self.stats[step["id"]]["throttled"] += 1
                    raise StepError("Kilden bad om at vente (429)", retry_after=min(after, 30))
                if r.status_code >= 500:
                    raise StepError(f"Kilden svarede {r.status_code}")
                if r.status_code >= 300:
                    raise StepError(f"Kilden svarede {r.status_code}", retryable=False)
                return r.json()

            payload = await self._cached(step, {**args, "_url": url}, call)
            page = data_sources.extract_rows(payload, ds["rows"])
            rows += masking.mask_rows([{f["name"]: data_sources.pick(x, f["path"]) for f in ds["fields"]} for x in page], policy)
            url = None
            if pg and len(rows) < self.limits["max_items"] and page:
                if pg["type"] == "nextLink":
                    url = payload.get(pg.get("next", "@odata.nextLink")) if isinstance(payload, dict) else None
                elif pg["type"] == "cursor":
                    cursor = payload.get(pg.get("next", "next_cursor")) if isinstance(payload, dict) else None
                    if cursor:
                        url = spec["base_url"] + ds["path"] + "?" + urlencode({**query, pg.get("param", "cursor"): cursor})
                elif pg["type"] == "offset":
                    url = spec["base_url"] + ds["path"] + "?" + urlencode({**query, pg.get("param", "offset"): len(rows)})
        return rows[: self.limits["max_items"]]

    async def fetch(self, step: dict, args: dict, *, paginate: bool = False) -> list[dict]:
        if step.get("operation"):
            return await self._operation(step, args, paginate=paginate)
        if step.get("http"):
            return await self._http(step, args)
        raise StepError(NOT_YET, retryable=False)


def _merge(base: dict, extra: dict, step_id: str) -> None:
    for k, v in extra.items():
        base[k if k not in base else f"{step_id}_{k}"] = v


async def execute(artifact: dict, version: dict, params: dict | None, *, principal=None, user_id: int | None = None) -> dict:
    d = version["definition"]
    params = {p["name"]: (params or {}).get(p["name"]) for p in d["params"]}
    missing = [p["name"] for p in d["params"] if p.get("required") and params.get(p["name"]) in (None, "")]
    if missing:
        raise HTTPException(422, f"Mangler {', '.join(missing)}")
    run = _Run(d, params, principal, user_id, artifact["id"], artifacts.version_str(version) or f"draft{version['id']}")
    for s in d["steps"]:
        run.stats[s["id"]] = {"id": s["id"], "rows": 0, "ms": 0, "calls": 0, "cache": None, "errors": [], "throttled": 0, "sample": []}
    results: dict[str, list[dict]] = {}
    merged: list[dict] = []
    key = d["key"]
    started = time.monotonic()

    async def run_step(step: dict) -> None:
        t = time.monotonic()
        st = run.stats[step["id"]]
        args = {k: _resolve(v, params, None) for k, v in step["args"].items()}
        try:
            if step["foreach"]:
                items = results.get(step["foreach"], [])[: d["limits"]["max_items"]]
                sem = asyncio.Semaphore(int(step.get("concurrency") or d["limits"]["concurrency"]))
                if step["batch"]:
                    b = step["batch"]
                    src_field = str(step["args"].get(b["param"], "")).replace("$item.", "") or b.get("key", "id")
                    groups = [items[i:i + int(b.get("size", 20))] for i in range(0, len(items), int(b.get("size", 20)))]

                    async def one_batch(group):
                        async with sem:
                            ids = [str(x.get(src_field)) for x in group if x.get(src_field) is not None]
                            try:
                                rows = await run.fetch(step, {**args, b["param"]: ids})
                            except StepError as e:
                                if d["partial"] == "fail":
                                    raise
                                run.partial = True
                                st["errors"].append(e.message)
                                for x in group:
                                    x.setdefault("_fejl", {})[step["id"]] = e.message
                                return
                            by = {str(r.get(b.get("key", "id"))): r for r in rows}
                            for x in group:
                                hit = by.get(str(x.get(src_field)))
                                if hit:
                                    _merge(x, {k: v for k, v in hit.items() if k != b.get("key", "id")}, step["id"])
                    await asyncio.gather(*(one_batch(g) for g in groups))
                else:
                    async def one(x):
                        async with sem:
                            a = {k: _resolve(v, params, x) for k, v in step["args"].items()}
                            try:
                                rows = await run.fetch(step, a)
                            except StepError as e:
                                if d["partial"] == "fail":
                                    raise
                                run.partial = True
                                st["errors"].append(e.message)
                                x.setdefault("_fejl", {})[step["id"]] = e.message
                                return
                            if len(rows) == 1:
                                _merge(x, rows[0], step["id"])
                            elif rows:
                                x[step["id"]] = rows
                    await asyncio.gather(*(one(x) for x in items))
                st["rows"] = len(items)
                st["sample"] = items[:3]
            else:
                rows = await run.fetch(step, args, paginate=bool(step["paginate"]))
                results[step["id"]] = rows
                st["rows"] = len(rows)
                st["sample"] = rows[:3]
        except StepError as e:
            st["errors"].append(e.message)
            if d["partial"] == "fail" or not step["foreach"] and not step["after"] and not merged and step is roots[0]:
                raise HTTPException(424, f"Trinnet {step['id']} fejlede: {e.message}")
            run.partial = True
        finally:
            st["ms"] = int((time.monotonic() - t) * 1000)

    lv = levels(d["steps"])
    roots = lv[0]
    try:
        for level in lv:
            await asyncio.gather(*(run_step(s) for s in level))
            for s in level:
                if s["foreach"]:
                    continue
                rows = results.get(s["id"], [])
                if s is roots[0]:
                    merged = rows
                    results[s["id"]] = merged          # foreach steps on the base enrich the merged rows
                    continue
                local, foreign = (s["join"] or {}).get("local", key), (s["join"] or {}).get("foreign", key)
                by = {}
                for r in rows:
                    by.setdefault(str(r.get(foreign)), r)
                for m in merged:
                    hit = by.get(str(m.get(local)))
                    if hit:
                        _merge(m, {k: v for k, v in hit.items() if k != foreign}, s["id"])
    except HTTPException:
        raise
    except StepError as e:
        raise HTTPException(424, e.message)
    return {"rows": merged[: d["limits"]["max_items"]], "count": min(len(merged), d["limits"]["max_items"]), "partial": run.partial,
            "steps": list(run.stats.values()), "ms": int((time.monotonic() - started) * 1000),
            "version": artifacts.version_str(version)}

"""Flows (Action-studie): the definition of a flow — a trigger and a list of steps — and its rules.

A flow is an artifact of type `flow` (versions, RACI, approval like the other studios). The definition is JSON:

    {"trigger": {"type": "schedule", "cron": "0 7 * * 1-5", "tz": "Europe/Copenhagen"},
     "steps": [{"id": "hent", "action": "http", "with": {"method": "GET", "url": "https://…", "source": "api-kilde"}},
               {"id": "hvis", "action": "if", "when": "{{ steps.hent.body.count > 0 }}", "then": [...], "else": [...]},
               {"id": "loop", "action": "foreach", "items": "{{ steps.hent.body.items }}", "steps": [...]},
               {"id": "mail", "action": "mail", "with": {"to": ["{{ item.email }}"], "subject": "…", "text": "…"}}],
     "on_error": [...],                                    steps that run when the flow fails (error in `error`)
     "settings": {"timeout_s": 3600, "concurrency": 1, "keep_outputs": true}}

Triggers: manual (Kør nu / API, with declared inputs), schedule (cron + time zone), webhook (an incoming endpoint with
handler `flow`), event (webhooks.EVENTS names or event kinds from app.events), pipeline (a pipeline run finished).
Every value in `with` may hold `{{ expressions }}` (app/devkit/expr.py). The contract is the trigger's inputs: a new
required input is a major, a new optional one a minor; everything else is a patch. The engine is app/devkit/flow_runs.py.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app import db, events, webhooks
from app.devkit import artifacts, contracts, expr

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_STEPS = 200
TRIGGERS = {"manual": "Manuelt / API", "schedule": "Tidsplan", "webhook": "Indgående webhook", "event": "Gær-hændelse",
            "pipeline": "Pipeline-kørsel færdig"}
CONTROL = ("if", "switch", "foreach", "delay", "terminate", "set", "compose")
SETTINGS_DEFAULT = {"timeout_s": 3600, "concurrency": 1, "keep_outputs": True}
CRON_PRESETS = [("0 * * * *", "Hver time"), ("*/15 * * * *", "Hvert 15. minut"), ("0 7 * * *", "Hver dag kl. 07:00"),
                ("0 8 * * 1-5", "Hverdage kl. 08:00"), ("0 6 * * 1", "Mandag kl. 06:00"), ("0 3 1 * *", "Den 1. i måneden kl. 03:00")]


class FlowError(Exception):
    pass


# ── The catalog of actions (the editor builds its forms from this) ───────────

def _f(name: str, label: str, type_: str = "expr", *, required: bool = False, help: str = "", options=None, default=None) -> dict:
    out = {"name": name, "label": label, "type": type_, "required": required, "help": help}
    if options:
        out["options"] = options
    if default is not None:
        out["default"] = default
    return out


ACTIONS: dict[str, dict] = {
    "http": {"label": "HTTP-kald", "group": "Kald", "description": "Kald et API. Brug en API-kilde fra Forbind API som auth, så ingen nøgler står i flowet.",
             "output": "status, ok, headers, body", "fields": [
                 _f("method", "Metode", "select", options=["GET", "POST", "PUT", "PATCH", "DELETE"], default="GET"),
                 _f("url", "Adresse", required=True, help="Fuld https-adresse, eller en sti når en API-kilde er valgt"),
                 _f("source", "API-kilde (auth)", "source", help="Kildens nøgle fra Forbind API; dens auth sættes på kaldet"),
                 _f("query", "Query-parametre", "json"), _f("headers", "Headers", "json", help="Aldrig nøgler her — brug en API-kilde"),
                 _f("body", "Body", "json", help="JSON eller tekst"), _f("timeout_s", "Timeout (s)", "number", default=30),
                 _f("fail_on_error", "Fejl ved 4xx/5xx", "bool", default=True)]},
    "operation": {"label": "Kør operation", "group": "Data", "description": "Kør en udgivet operation fra Datastudie. Svaret er maskeret som altid.",
                  "output": "rows, count, hasMore, columns", "fields": [
                      _f("operation", "Operation", "operation", required=True), _f("args", "Argumenter", "json")]},
    "pipeline": {"label": "Start pipeline", "group": "Data", "description": "Start en pipeline-kørsel, og vent eventuelt på den.",
                 "output": "run_id, status", "fields": [
                     _f("pipeline", "Pipeline", "pipeline", required=True), _f("wait", "Vent på kørslen", "bool", default=True)]},
    "mail": {"label": "Send e-mail", "group": "Besked", "description": "Send en e-mail gennem instansens mailudbyder.",
             "output": "sent, to", "fields": [
                 _f("to", "Til", required=True, help="E-mails eller bruger-id'er, som liste eller adskilt af komma"),
                 _f("subject", "Emne", required=True), _f("text", "Tekst", "text", required=True), _f("html", "HTML (valgfrit)", "text")]},
    "message": {"label": "Send Besked", "group": "Besked", "description": "Send en besked gennem Beskeder: app-indbakken, og SMS eller e-mail efter brugerens egne regler.",
                "output": "broadcast_id, total, sms, mail", "fields": [
                    _f("users", "Brugere", required=True, help="Bruger-id'er eller e-mails"), _f("title", "Titel", required=True),
                    _f("body", "Tekst", "text", required=True),
                    _f("channels", "Kanaler", "multiselect", options=["app", "mail", "sms"], default=["app", "mail"]),
                    _f("urgent", "Haster (ignorerer stilletid)", "bool", default=False)]},
    "ai": {"label": "AI-prompt", "group": "Kald", "description": "Send en prompt til instansens AI. Kun når AI er slået til.",
           "output": "text, json", "fields": [
               _f("system", "Systemprompt", "text"), _f("prompt", "Prompt", "text", required=True),
               _f("json", "Svar som JSON", "bool", default=False), _f("max_tokens", "Maks. tokens", "number", default=900)]},
    "event": {"label": "Udsend hændelse", "group": "Kald", "description": "Udsend webhook-hændelsen flow.event til de webhooks, der abonnerer — kun id'er, aldrig persondata.",
              "output": "name, delivered", "fields": [_f("name", "Navn", required=True), _f("data", "Data", "json")]},
    "flow": {"label": "Kald et andet flow", "group": "Kald", "description": "Start et andet flow med et input, og vent eventuelt på dets resultat.",
             "output": "run_id, status, output", "fields": [
                 _f("flow", "Flow", "flow", required=True), _f("input", "Input", "json"), _f("wait", "Vent på resultatet", "bool", default=True)]},
    "approval": {"label": "Godkendelse", "group": "Mennesker", "description": "Flowet venter, til en af godkenderne har svaret på /flow-godkendelse (de får en mail med linket).",
                 "output": "approved, decided_by, comment, expired", "fields": [
                     _f("approvers", "Godkendere", required=True, help="Bruger-id'er eller e-mails på brugere"),
                     _f("title", "Titel", required=True), _f("message", "Besked", "text"), _f("data", "Vis disse data", "json"),
                     _f("timeout_hours", "Udløber efter (timer)", "number", default=72)]},
    "file": {"label": "Skriv fil", "group": "Data", "description": "Gem en fil i filtræet (/shared/… eller /users/<id>/…).",
             "output": "file_id, path, size", "fields": [
                 _f("path", "Mappe", required=True, help="fx /shared/rapporter/"), _f("filename", "Filnavn", required=True),
                 _f("content", "Indhold", "text", required=True), _f("content_type", "Type", default="text/plain"),
                 _f("overwrite", "Overskriv", "bool", default=True)]},
    "set": {"label": "Sæt variabel", "group": "Kontrol", "description": "Gem en værdi i vars.<navn> til senere trin.", "output": "værdien",
            "fields": [_f("name", "Navn", "string", required=True), _f("value", "Værdi", "json", required=True)]},
    "compose": {"label": "Sammensæt", "group": "Kontrol", "description": "Byg en værdi (tekst, objekt eller liste) som trinnets output.",
                "output": "værdien", "fields": [_f("value", "Værdi", "json", required=True)]},
    "if": {"label": "Hvis", "group": "Kontrol", "description": "Kør én af to grene efter en betingelse.", "output": "branch", "fields": []},
    "switch": {"label": "Vælg", "group": "Kontrol", "description": "Kør den gren, hvis nøgle matcher værdien; ellers standard.", "output": "case", "fields": []},
    "foreach": {"label": "For hver", "group": "Kontrol", "description": "Kør trinene for hvert element i en liste (item, index).", "output": "count, results", "fields": []},
    "delay": {"label": "Vent", "group": "Kontrol", "description": "Vent et antal sekunder eller til et tidspunkt. Flowet sover imens og fylder ikke.",
              "output": "until", "fields": [_f("seconds", "Sekunder", "number"), _f("until", "Til tidspunkt (ISO 8601)")]},
    "terminate": {"label": "Afslut", "group": "Kontrol", "description": "Stop flowet her med en status.", "output": "—",
                  "fields": [_f("status", "Status", "select", options=["succeeded", "failed"], default="succeeded"), _f("message", "Besked")]},
}


def catalog() -> dict:
    return {"actions": [{"key": k, **v} for k, v in ACTIONS.items()],
            "triggers": [{"key": k, "label": v} for k, v in TRIGGERS.items()],
            "functions": [{"signature": s, "description": d} for s, d in expr.REFERENCE],
            "events": [{"key": k, "description": v} for k, v in webhooks.EVENTS.items()],
            "cron_presets": [{"cron": c, "label": l} for c, l in CRON_PRESETS], "settings": SETTINGS_DEFAULT}


# ── Cron ────────────────────────────────────────────────────────────────────

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise FlowError(f"Ugyldigt trin i cron-feltet {text!r}")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise FlowError(f"Ugyldigt interval i cron-feltet {text!r}")
            a, b = int(a_s), int(b_s)
        elif part.isdigit():
            a = b = int(part)
        else:
            raise FlowError(f"Ugyldigt cron-felt {text!r}")
        if a < lo or b > hi or a > b:
            raise FlowError(f"Cron-feltet {text!r} ligger uden for {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(text: str) -> list[set[int]]:
    """Five fields: minute hour day-of-month month day-of-week (0 and 7 are Sunday)."""
    parts = (text or "").split()
    if len(parts) != 5:
        raise FlowError("Tidsplanen skal have fem felter: minut time dag måned ugedag")
    fields = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES)]
    if 7 in fields[4]:
        fields[4] = (fields[4] - {7}) | {0}
    return fields


def tz_of(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Copenhagen")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise FlowError(f"Ukendt tidszone {name!r}")


def next_run(cron: str, tz: str, after: datetime | None = None) -> datetime | None:
    """The first time matching the cron expression after `after` (within a year), in UTC."""
    minutes, hours, days, months, dows = parse_cron(cron)
    zone = tz_of(tz)
    t = ((after or datetime.now(timezone.utc)).astimezone(zone) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    end = t + timedelta(days=366)
    dom_any = days == set(range(1, 32))
    dow_any = dows == set(range(0, 7))
    while t < end:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        dow = (t.weekday() + 1) % 7   # Monday=0 → cron Sunday=0
        day_ok = (t.day in days) if dow_any else (dow in dows) if dom_any else (t.day in days or dow in dows)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute not in minutes:
            t += timedelta(minutes=1)
            continue
        return t.astimezone(timezone.utc)
    return None


# ── Validation ──────────────────────────────────────────────────────────────

def _bad(msg: str) -> HTTPException:
    return HTTPException(422, msg)


def _check_exprs(value, where: str) -> None:
    if isinstance(value, str) and "{{" in value:
        try:
            expr.check(value)
        except expr.ExprError as e:
            raise _bad(f"Udtryk i {where}: {e}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_exprs(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_exprs(v, f"{where}[{i}]")


def _int(value, lo: int, hi: int, what: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise _bad(f"{what} skal være et tal")
    if not lo <= n <= hi:
        raise _bad(f"{what} skal være mellem {lo} og {hi}")
    return n


class _Walk:
    def __init__(self):
        self.ids: set[str] = set()
        self.count = 0

    def steps(self, raw, where: str) -> list[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise _bad(f"{where} skal være en liste af trin")
        return [self.step(s, f"{where}[{i}]") for i, s in enumerate(raw)]

    def step(self, raw, where: str) -> dict:
        if not isinstance(raw, dict):
            raise _bad(f"{where} skal være et trin (objekt)")
        self.count += 1
        if self.count > MAX_STEPS:
            raise _bad(f"Et flow kan højst have {MAX_STEPS} trin")
        action = str(raw.get("action") or "")
        if action not in ACTIONS:
            raise _bad(f"{where}: ukendt handling {action!r}")
        sid = str(raw.get("id") or "").strip()
        if not ID_RE.match(sid):
            raise _bad(f"{where}: id'et {sid!r} skal være små bogstaver, tal og _ (højst 40 tegn)")
        if sid in self.ids or sid in ("trigger", "vars", "run", "item", "index", "error"):
            raise _bad(f"{where}: id'et {sid!r} er brugt før (eller reserveret)")
        self.ids.add(sid)
        step: dict = {"id": sid, "action": action, "name": str(raw.get("name") or "").strip()[:80]}
        if raw.get("when") not in (None, ""):
            step["when"] = str(raw["when"])
            _check_exprs(step["when"], f"{sid}.when")
        step["on_error"] = raw.get("on_error") or "stop"
        if step["on_error"] not in ("stop", "continue"):
            raise _bad(f"{sid}: on_error skal være stop eller continue")
        retry = raw.get("retry") or {}
        if not isinstance(retry, dict):
            raise _bad(f"{sid}: retry skal være et objekt med count og delay_s")
        step["retry"] = {"count": _int(retry.get("count"), 0, 10, f"{sid}: retry.count", 0),
                         "delay_s": _int(retry.get("delay_s"), 0, 3600, f"{sid}: retry.delay_s", 10)}
        step["timeout_s"] = _int(raw.get("timeout_s"), 1, 600, f"{sid}: timeout_s", 30)
        with_ = raw.get("with") or {}
        if not isinstance(with_, dict):
            raise _bad(f"{sid}: with skal være et objekt")
        step["with"] = with_
        _check_exprs(with_, sid)
        for f in ACTIONS[action]["fields"]:
            if f["required"] and with_.get(f["name"]) in (None, "", [], {}):
                raise _bad(f"{sid} ({ACTIONS[action]['label']}): {f['label'].lower()} mangler")
        if action == "if":
            if not raw.get("when"):
                raise _bad(f"{sid}: Hvis-trinnet mangler sin betingelse (when)")
            step["then"] = self.steps(raw.get("then"), f"{sid}.then")
            step["else"] = self.steps(raw.get("else"), f"{sid}.else")
        elif action == "switch":
            step["value"] = str(raw.get("value") or "")
            if not step["value"]:
                raise _bad(f"{sid}: Vælg-trinnet mangler værdien (value)")
            _check_exprs(step["value"], f"{sid}.value")
            cases = raw.get("cases") or {}
            if not isinstance(cases, dict) or not cases:
                raise _bad(f"{sid}: Vælg-trinnet skal have mindst én gren (cases)")
            step["cases"] = {str(k)[:80]: self.steps(v, f"{sid}.cases.{k}") for k, v in cases.items()}
            step["default"] = self.steps(raw.get("default"), f"{sid}.default")
        elif action == "foreach":
            step["items"] = str(raw.get("items") or "")
            if not step["items"]:
                raise _bad(f"{sid}: For hver-trinnet mangler listen (items)")
            _check_exprs(step["items"], f"{sid}.items")
            step["steps"] = self.steps(raw.get("steps"), f"{sid}.steps")
            if not step["steps"]:
                raise _bad(f"{sid}: For hver-trinnet har ingen trin")
            step["concurrency"] = _int(raw.get("concurrency"), 1, 10, f"{sid}: concurrency", 1)
            step["max_items"] = _int(raw.get("max_items"), 1, 10000, f"{sid}: max_items", 1000)
        elif action == "delay":
            if not (with_.get("seconds") or with_.get("until")):
                raise _bad(f"{sid}: Vent-trinnet skal have seconds eller until")
        elif action == "http":
            method = str(with_.get("method") or "GET").upper()
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                raise _bad(f"{sid}: ukendt metode {method}")
            with_["method"] = method
        elif action == "set" and not ID_RE.match(str(with_.get("name") or "")):
            raise _bad(f"{sid}: variablens navn skal være små bogstaver, tal og _")
        return step


def validate(raw: dict) -> dict:
    """The definition cleaned, with Danish 422s for anything wrong. References (operations, pipelines, flows) are
    checked against the database in `resolve()`."""
    if not isinstance(raw, dict):
        raise _bad("Definitionen skal være et objekt")
    trig = raw.get("trigger") or {}
    if not isinstance(trig, dict) or trig.get("type") not in TRIGGERS:
        raise _bad("Udløseren skal have en type: " + ", ".join(TRIGGERS))
    trigger: dict = {"type": trig["type"]}
    inputs = trig.get("inputs") or {}
    if not isinstance(inputs, dict) or len(inputs) > 50:
        raise _bad("Udløserens inputs skal være et objekt med højst 50 felter")
    trigger["inputs"] = {}
    for name, spec in inputs.items():
        if not ID_RE.match(str(name)):
            raise _bad(f"Input-navnet {name!r} skal være små bogstaver, tal og _")
        spec = spec if isinstance(spec, dict) else {}
        t = str(spec.get("type") or "string")
        if t not in ("string", "number", "boolean", "object", "list"):
            raise _bad(f"Input {name}: ukendt type {t}")
        trigger["inputs"][name] = {"type": t, "required": bool(spec.get("required")), "description": str(spec.get("description") or "")[:200]}
    if trigger["type"] == "schedule":
        trigger["cron"] = str(trig.get("cron") or "").strip()
        trigger["tz"] = str(trig.get("tz") or "Europe/Copenhagen").strip()
        try:
            parse_cron(trigger["cron"])
            tz_of(trigger["tz"])
        except FlowError as e:
            raise _bad(str(e))
    elif trigger["type"] == "webhook":
        trigger["endpoint_key"] = str(trig.get("endpoint_key") or "").strip()[:80]
    elif trigger["type"] == "event":
        names = trig.get("events") or []
        if not isinstance(names, list) or not names or len(names) > 20:
            raise _bad("Vælg mellem 1 og 20 hændelser")
        clean = []
        for n in names:
            n = str(n).strip()
            if n not in webhooks.EVENTS and not events.KIND.match(n):
                raise _bad(f"Ukendt hændelse {n!r}")
            if n not in clean:
                clean.append(n)
        trigger["events"] = clean
    elif trigger["type"] == "pipeline":
        trigger["pipeline"] = trig.get("pipeline")
        if trigger["pipeline"] in (None, ""):
            raise _bad("Vælg den pipeline, der udløser flowet")
        on = trig.get("on") or ["succeeded"]
        if not isinstance(on, list) or not set(on) <= {"succeeded", "failed"} or not on:
            raise _bad("on skal være succeeded og/eller failed")
        trigger["on"] = sorted(set(on))
    walk = _Walk()
    steps = walk.steps(raw.get("steps") or [], "steps")
    on_error = walk.steps(raw.get("on_error") or [], "on_error")
    s = raw.get("settings") or {}
    if not isinstance(s, dict):
        raise _bad("settings skal være et objekt")
    settings = {"timeout_s": _int(s.get("timeout_s"), 10, 86400, "timeout_s", SETTINGS_DEFAULT["timeout_s"]),
                "concurrency": _int(s.get("concurrency"), 1, 20, "concurrency", SETTINGS_DEFAULT["concurrency"]),
                "keep_outputs": bool(s.get("keep_outputs", True))}
    return {"trigger": trigger, "steps": steps, "on_error": on_error, "settings": settings,
            "description": str(raw.get("description") or "")[:2000]}


def _refs(steps: list[dict], out: list[tuple[str, str, object]]) -> None:
    for s in steps:
        w = s.get("with") or {}
        if s["action"] in ("operation", "pipeline", "flow"):
            out.append((s["id"], s["action"], w.get(s["action"])))
        for key in ("then", "else", "steps", "default"):
            _refs(s.get(key) or [], out)
        for branch in (s.get("cases") or {}).values():
            _refs(branch, out)


async def find_artifact(conn, type_: str, ref) -> dict | None:
    """An operation, pipeline or flow by id or slug (None for an expression decided at run time, or when it does not exist)."""
    if isinstance(ref, str) and "{{" in ref:
        return None   # decided at run time
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        a = await db.fetch_one(conn, f"select {artifacts.COLUMNS} from dev_artifacts a where a.id = %s and a.type = %s", (int(ref), type_))
    else:
        a = await artifacts.by_slug(conn, type_, str(ref))
    return a


async def resolve(conn, definition: dict) -> dict:
    """Checks that the operations, pipelines and flows a definition names exist (422 otherwise)."""
    refs: list[tuple[str, str, object]] = []
    _refs(definition["steps"], refs)
    _refs(definition["on_error"], refs)
    for sid, kind, ref in refs:
        if ref in (None, ""):
            continue
        if await find_artifact(conn, kind, ref) is None and not (isinstance(ref, str) and "{{" in ref):
            raise _bad(f"{sid}: {artifacts.TYPE_LABELS[kind].lower()}en {ref!r} findes ikke")
    trig = definition["trigger"]
    if trig["type"] == "pipeline":
        a = await find_artifact(conn, "pipeline", trig["pipeline"])
        if a is None:
            raise _bad(f"Pipelinen {trig['pipeline']!r} findes ikke")
        trig["pipeline"] = a["id"]
    return definition


# ── The artifact type ───────────────────────────────────────────────────────

def contract(definition: dict) -> dict:
    inputs = (definition.get("trigger") or {}).get("inputs") or {}
    return {"params": {k: {"type": v.get("type", "string"), "required": bool(v.get("required"))} for k, v in inputs.items()},
            "items": [], "fields": {}, "key": None}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    refs: list[tuple[str, str, object]] = []
    _refs(definition.get("steps") or [], refs)
    _refs(definition.get("on_error") or [], refs)
    out: list[tuple[int, str]] = []
    for sid, kind, ref in refs:
        a = await find_artifact(conn, kind, ref) if ref not in (None, "") else None
        if a:
            out.append((a["id"], f"{sid}: {ACTIONS[kind]['label'].lower()}"))
    trig = definition.get("trigger") or {}
    if trig.get("type") == "pipeline" and trig.get("pipeline"):
        a = await find_artifact(conn, "pipeline", trig["pipeline"])
        if a:
            out.append((a["id"], "udløser"))
    return out


async def gate(conn, artifact: dict, v: dict) -> str | None:
    """What stops a version from being submitted: a webhook trigger without its endpoint."""
    trig = v["definition"].get("trigger") or {}
    if trig.get("type") == "webhook" and not trig.get("endpoint_key"):
        return "Udløseren mangler sit indgående endpoint: opret det under Udløser, før flowet udgives"
    if not v["definition"].get("steps"):
        return "Flowet har ingen trin endnu"
    return None


def trigger_text(trigger: dict) -> str:
    t = trigger.get("type")
    if t == "schedule":
        preset = next((l for c, l in CRON_PRESETS if c == trigger.get("cron")), None)
        return preset or f"Cron {trigger.get('cron')} ({trigger.get('tz')})"
    if t == "event":
        return "Hændelse: " + ", ".join(trigger.get("events") or [])
    if t == "pipeline":
        return "Når en pipeline er " + " eller ".join({"succeeded": "kørt", "failed": "fejlet"}[x] for x in trigger.get("on") or ["succeeded"])
    return TRIGGERS.get(t, t or "")


def count_steps(steps: list[dict]) -> int:
    n = 0
    for s in steps:
        n += 1
        for key in ("then", "else", "steps", "default"):
            n += count_steps(s.get(key) or [])
        for branch in (s.get("cases") or {}).values():
            n += count_steps(branch)
    return n


artifacts.TYPE_LABELS.setdefault("flow", "Flow")
artifacts.TYPES.setdefault("flow", artifacts.TypeSpec())

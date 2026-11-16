"""Help in Action-studie (specs/developer/AI.md, fase 5): a draft flow from a description, and a risk check.

- risks(): rules on a validated definition, no AI (AI-4): an approval whose answer no later step reads (the flow goes on
  even when it is rejected — with a fix that puts the rest behind "Hvis godkendt"), a single approver, a long deadline,
  data from an operation with red or personal fields sent on by mail, Besked, HTTP, AI, an event or a file, a key
  written into an HTTP step instead of an API source, and mail or Besked inside a "For hver" (one per item).
- draft(): the trigger and steps for a description. AI writes it when it is on, and it is kept only if it validates;
  otherwise keywords give a skeleton with [placeholders] the user fills in. Nothing is saved: the user takes the steps
  one by one and creates the flow (specs/developer/AI.md, AI-2).
"""

import copy
import json
import re

from fastapi import HTTPException

from app import db
from app.devkit import artifacts, assist, flows

SENDS = ("mail", "message", "http", "ai", "event", "file")
SECRET_HEADER = re.compile(r"^(authorization|x-api-key|api-key|apikey|x-auth-token|cookie)$", re.I)
SECRET_QUERY = re.compile(r"[?&](token|key|apikey|api_key|access_token|password)=[^&{]+", re.I)


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _step_text(s: dict) -> str:
    """Everything a step reads (its condition, inputs and branches), as one string to look for references in."""
    return _text({k: s.get(k) for k in ("when", "with", "items", "value", "then", "else", "steps", "cases", "default")})


def _walk(steps: list[dict], loop: dict | None = None, out: list | None = None) -> list[tuple[dict, dict | None]]:
    """Every step in order with the For hver it is inside (or None)."""
    out = [] if out is None else out
    for s in steps:
        out.append((s, loop))
        inner = s if s["action"] == "foreach" else loop
        for key in ("then", "else", "steps", "default"):
            _walk(s.get(key) or [], inner, out)
        for branch in (s.get("cases") or {}).values():
            _walk(branch, inner, out)
    return out


async def _sensitive_operations(conn, all_steps: list[tuple[dict, dict | None]]) -> dict[str, list[str]]:
    """Operation steps whose operation returns red or personal fields → those field names."""
    out = {}
    for s, _ in all_steps:
        if s["action"] != "operation":
            continue
        a = await flows.find_artifact(conn, "operation", (s.get("with") or {}).get("operation"))
        v = await artifacts.current(conn, a) if a else None
        fields = ((v or {}).get("contract") or {}).get("fields") or {}
        names = sorted(k for k, f in fields.items() if f.get("classification") == "red" or f.get("personal"))
        if names:
            out[s["id"]] = names
    return out


def _guard(definition: dict, approval_id: str) -> dict | None:
    """The fix for an unused approval: the steps after it (in the same list) go behind "Hvis godkendt"; a rejection
    ends the flow. None when the approval is not found at the top level or nothing follows it."""
    d = copy.deepcopy(definition)
    steps = d["steps"]
    i = next((k for k, s in enumerate(steps) if s["id"] == approval_id), None)
    if i is None or i == len(steps) - 1:
        return None
    rest = steps[i + 1:]
    taken = {s["id"] for s, _ in _walk(d["steps"])}
    gid = f"{approval_id}_svar"[:40]
    while gid in taken:
        gid = (gid + "_x")[:40]
    d["steps"] = steps[:i + 1] + [{"id": gid, "action": "if", "name": "Hvis godkendt", "when": f"{{{{ steps.{approval_id}.approved }}}}",
                                    "then": rest, "else": [{"id": f"{gid}_stop"[:40], "action": "terminate",
                                                            "with": {"status": "succeeded", "message": "Afvist eller udløbet"}}]}]
    return d


async def risks(conn, definition: dict) -> list[dict]:
    all_steps = _walk(definition["steps"]) + _walk(definition.get("on_error") or [])
    order = [s["id"] for s, _ in all_steps]
    out = []
    for idx, (s, loop) in enumerate(all_steps):
        w = s.get("with") or {}
        if s["action"] == "approval":
            later = " ".join(_step_text(x) for x, _ in all_steps[idx + 1:])
            if f"steps.{s['id']}." not in later and idx + 1 < len(all_steps):
                fix = _guard(definition, s["id"])
                out.append({"key": f"approval_unused:{s['id']}", "level": "warn", "step": s["id"],
                            "title": "Godkendelsen bliver ikke brugt",
                            "text": f"Intet trin efter {s['id']} læser steps.{s['id']}.approved, så flowet fortsætter, også "
                                    "hvis godkendelsen afvises eller udløber.",
                            **({"fixLabel": "Fortsæt kun, hvis godkendt", "fix": fix} if fix else {})})
            approvers = w.get("approvers")
            listed = approvers if isinstance(approvers, list) else [x for x in str(approvers or "").split(",") if x.strip()]
            if len(listed) == 1 and "{{" not in _text(approvers):
                out.append({"key": f"single_approver:{s['id']}", "level": "info", "step": s["id"], "title": "Kun én godkender",
                            "text": "Er personen væk, venter flowet til fristen. Tilføj en godkender mere."})
            hours = w.get("timeout_hours", 72)
            if isinstance(hours, (int, float)) and hours > 168:
                out.append({"key": f"long_timeout:{s['id']}", "level": "info", "step": s["id"], "title": "Lang frist",
                            "text": f"Godkendelsen kan vente {int(hours)} timer. Kørslen står stille imens."})
        if s["action"] == "http":
            headers = w.get("headers") if isinstance(w.get("headers"), dict) else {}
            literal = [k for k, v in headers.items() if SECRET_HEADER.match(str(k)) and "{{" not in str(v)]
            if literal or SECRET_QUERY.search(str(w.get("url") or "")):
                out.append({"key": f"secret:{s['id']}", "level": "warn", "step": s["id"], "title": "En nøgle står i flowet",
                            "text": "Brug en API-kilde fra Forbind API som auth, så nøglen ligger i hemmelighederne og "
                                    "ikke i definitionen, versionerne og eksporten."})
        if s["action"] in ("mail", "message") and loop is not None:
            out.append({"key": f"bulk:{s['id']}", "level": "info", "step": s["id"], "title": "Én pr. element",
                        "text": f"{s['id']} ligger i For hver ({loop['id']}) og sendes én gang pr. element, højst "
                                f"{loop.get('max_items', 1000)}."})
    sensitive = await _sensitive_operations(conn, all_steps)
    for s, loop in all_steps:
        if s["action"] not in SENDS:
            continue
        text = _text(s.get("with") or {})
        hits = [sid for sid in sensitive if f"steps.{sid}." in text or f"steps.{sid} " in text or f"steps.{sid}}}" in text]
        if loop is not None and "item" in text:
            hits += [sid for sid in sensitive if f"steps.{sid}" in str(loop.get("items")) and sid not in hits]
        for sid in hits:
            where = {"mail": "en e-mail", "message": "en Besked", "http": "et HTTP-kald", "ai": "AI",
                     "event": "en hændelse", "file": "en fil"}[s["action"]]
            out.append({"key": f"personal:{s['id']}:{sid}", "level": "warn", "step": s["id"], "title": "Persondata sendes videre",
                        "text": f"{s['id']} sender data fra {sid} til {where}. Operationen har {', '.join(sensitive[sid])} "
                                "(rød eller persondata). Tag kun de felter med, modtageren skal bruge."})
    return sorted(out, key=lambda r: (r["level"] != "warn", order.index(r["step"]) if r["step"] in order else 0))


# ── A draft from a description ──────────────────────────────────────────────

def _cron(text: str) -> str | None:
    t = text.lower()
    if "hver time" in t:
        return "0 * * * *"
    m = re.search(r"kl\.?\s*(\d{1,2})(?:[.:](\d{2}))?", t)
    hour, minute = (int(m.group(1)) % 24, int(m.group(2) or 0)) if m else (7, 0)
    if "hverdag" in t:
        return f"{minute} {hour} * * 1-5"
    if re.search(r"\b(hver dag|dagligt|hver morgen|hver aften)\b", t) or m:
        return f"{minute} {hour} * * *"
    if re.search(r"\b(hver mandag|ugentligt|hver uge)\b", t):
        return f"{minute} {hour} * * 1"
    return None


def heuristic(description: str) -> tuple[dict, list[str]]:
    """A skeleton from keywords: the trigger, and the steps in the order the text mentions them, with [placeholders]."""
    t = description.lower()
    notes = []
    cron = _cron(description)
    if cron:
        trigger = {"type": "schedule", "cron": cron, "tz": "Europe/Copenhagen", "inputs": {}}
    elif "webhook" in t:
        trigger = {"type": "webhook", "inputs": {}}
        notes.append("Opret flowets indgående endpoint under Udløser, før det kan udgives.")
    elif re.search(r"\bny(e)? bruger|bruger (bliver )?oprettet", t):
        trigger = {"type": "event", "events": ["user.created"], "inputs": {}}
    else:
        trigger = {"type": "manual", "inputs": {}}
    found = []
    for word, kind in (("godkend", "approval"), ("e-mail", "mail"), ("mail", "mail"), ("besked", "message"), ("sms", "message"),
                       ("fil", "file"), ("rapport", "file")):
        i = t.find(word)
        if i >= 0 and kind not in [k for _, k in found]:
            found.append((i, kind))
    steps = []
    for _, kind in sorted(found):
        if kind == "approval":
            steps.append({"id": "godkend", "action": "approval", "name": "Godkendelse",
                          "with": {"approvers": ["[godkender]"], "title": "[Hvad skal godkendes]", "timeout_hours": 72}})
        elif kind == "mail":
            steps.append({"id": "mail", "action": "mail", "name": "Send e-mail",
                          "with": {"to": ["[modtager]"], "subject": "[emne]", "text": description[:500]}})
        elif kind == "message":
            steps.append({"id": "besked", "action": "message", "name": "Send Besked",
                          "with": {"users": ["[bruger]"], "title": "[titel]", "body": description[:500]}})
        elif kind == "file":
            steps.append({"id": "fil", "action": "file", "name": "Skriv fil",
                          "with": {"path": "/shared/rapporter/", "filename": "[filnavn].txt", "content": "[indhold]"}})
    if any(s["action"] == "approval" for s in steps) and steps[-1]["action"] != "approval":
        i = next(k for k, s in enumerate(steps) if s["action"] == "approval")
        rest = steps[i + 1:]
        steps = steps[:i + 1] + [{"id": "hvis_godkendt", "action": "if", "name": "Hvis godkendt",
                                  "when": "{{ steps.godkend.approved }}", "then": rest, "else": []}]
    if not steps:
        notes.append("Beskrivelsen nævner ingen trin, jeg kender. Tilføj dem i designeren.")
    elif "[" in _text(steps):
        notes.append("Udfyld felterne i [klammer], før flowet køres.")
    return {"trigger": trigger, "steps": steps, "on_error": [], "settings": dict(flows.SETTINGS_DEFAULT),
            "description": description[:2000]}, notes


async def _catalog_text(conn) -> str:
    ops = await db.fetch_all(conn, "select slug, name from dev_artifacts where type = 'operation' and current_version_id is not null order by name limit 40")
    actions = {k: {"felter": [f["name"] + ("*" if f["required"] else "") for f in v["fields"]], "output": v["output"]}
               for k, v in flows.ACTIONS.items()}
    return json.dumps({"udløsere": list(flows.TRIGGERS), "handlinger": actions,
                       "operationer": [{"slug": o["slug"], "navn": o["name"]} for o in ops]}, ensure_ascii=False)


async def draft(conn, description: str, user_id: int) -> dict:
    answer = await assist.complete(
        assist.SYSTEM + " Lav et flow til Action-studie ud fra beskrivelsen. Svar kun som JSON: {\"trigger\": {\"type\": …}, "
        "\"steps\": [{\"id\": \"små_bogstaver\", \"action\": …, \"name\": \"…\", \"with\": {…}}]}. Brug kun handlinger og felter fra "
        "kataloget (* = påkrævet), operationer fra listen, og {{ udtryk }} som {{ trigger.data.email }} eller "
        "{{ steps.<id>.approved }}. En godkendelse skal følges af et if-trin på steps.<id>.approved. Skriv aldrig nøgler i flowet; "
        "brug et http-trin med source. Ukendte værdier skrives som [pladsholder].",
        f"Beskrivelse: {description}\n\nKatalog: {await _catalog_text(conn)}", user_id=user_id, max_tokens=1500)
    parsed = assist._json(answer)
    if isinstance(parsed, dict) and parsed.get("steps"):
        try:
            clean = await flows.resolve(conn, flows.validate({**parsed, "description": description[:2000]}))
            notes = ["Udfyld felterne i [klammer], før flowet køres."] if "[" in _text(clean["steps"]) else []
            return {"definition": clean, "ai": True, "notes": notes, "risks": await risks(conn, clean)}
        except HTTPException:
            pass   # the AI's flow did not validate: the keywords' skeleton instead
    skeleton, notes = heuristic(description)
    clean = await flows.resolve(conn, flows.validate(skeleton))
    return {"definition": clean, "ai": False, "notes": notes, "risks": await risks(conn, clean)}

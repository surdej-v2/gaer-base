"""Test cases for an MCP endpoint, proposed and explained (specs/developer/AI.md, fase 3).

Proposals are rules on what is known without looking at data (AI-1, AI-4): the endpoint's tools and their parameters,
how often each tool was called (mcp_log: counts and argument *names* only — the logged values are masked anyway), the
existing test cases and the test accounts. Three kinds:

- untested:  a tool without a test case gets a direct call (its first example, or no arguments when none are required).
- empty:     an unknown value for a text parameter must give an empty answer, not an error ("kanttilfælde").
- protected: a yellow or red tool called as an external test account must not show personal data in plain text.

AI (when on) only writes natural questions for AI test cases and variants of a question, and explains a failed test
case from the change in tool descriptions between the published version and the draft. Nothing is saved here: the
user picks proposals and saves them with the test bench's own endpoint (which audits).
"""

import json

from app import db
from app.devkit import artifacts, assist

EDGE_VALUE = "findes-ikke-test"
MAX_PROPOSALS = 8


def _example_args(tool: dict) -> dict | None:
    """Arguments from the tool's first example, {} when nothing is required, else None (the user fills them in)."""
    for ex in tool.get("examples") or []:
        if isinstance(ex, dict):
            args = ex.get("args") if isinstance(ex.get("args"), dict) else ex
            if isinstance(args, dict):
                return {k: v for k, v in args.items() if k in (tool.get("inputSchema") or {}).get("properties", {})}
    return {} if not (tool.get("inputSchema") or {}).get("required") else None


async def calls_by_tool(conn, key: str, days: int = 30) -> dict[str, int]:
    rows = await db.fetch_all(conn, """select tool, count(*) as n from mcp_log where server = %s and tool is not null
                                       and method = 'tools/call' and ts > now() - make_interval(days => %s) group by tool""",
                              (key, days))
    return {r["tool"]: int(r["n"]) for r in rows}


def coverage(tools: list[dict], cases: list[dict], calls: dict[str, int]) -> list[dict]:
    out = []
    for t in tools:
        mine = [c for c in cases if c["tool"] == t["name"] or t["name"] in ((c["expect"] or {}).get("tools") or [])]
        last = [c["last"] for c in mine if c.get("last")]
        out.append({"tool": t["name"], "testcases": len(mine), "calls30d": calls.get(t["name"], 0),
                    "passed": all(x["passed"] for x in last) if last else None})
    return out


def proposals(tools: list[dict], cases: list[dict], calls: dict[str, int], accounts: list[dict]) -> dict:
    tested = {c["tool"] for c in cases if c["tool"]} | {t for c in cases for t in ((c["expect"] or {}).get("tools") or [])}
    out: list[dict] = []
    notes: list[str] = []
    readable = [t for t in tools if t.get("mode", "read") == "read"]
    for t in sorted(readable, key=lambda t: -calls.get(t["name"], 0)):
        if t["name"] in tested:
            continue
        args = _example_args(t)
        n = calls.get(t["name"], 0)
        out.append({"key": f"untested:{t['name']}", "why": "tool uden test", "seen": n,
                    "testcase": {"kind": "direct", "name": f"{t['name']}: et typisk kald", "tool": t["name"],
                                 "args": args if args is not None else {p: "" for p in t["inputSchema"].get("required", [])},
                                 "expect": {"min_rows": 1} if args is not None else {}},
                    "needsArgs": args is None})
    for t in readable:
        props = (t.get("inputSchema") or {}).get("properties", {})
        text_param = next((p for p, s in props.items() if s.get("type") == "string"), None)
        base = _example_args(t)
        if not text_param or base is None:
            continue
        if any(c["tool"] == t["name"] and "max_rows" in (c["expect"] or {}) for c in cases):
            continue
        out.append({"key": f"empty:{t['name']}:{text_param}", "why": "kanttilfælde", "seen": calls.get(t["name"], 0),
                    "testcase": {"kind": "direct", "name": f"{t['name']}: ukendt {text_param} giver et tomt svar",
                                 "tool": t["name"], "args": {**base, text_param: EDGE_VALUE}, "expect": {"max_rows": 0}},
                    "needsArgs": False})
    external = [a for a in accounts if a.get("audience") == "external"]
    sensitive = [t for t in readable if t.get("classification") in ("yellow", "red")]
    if sensitive and not external:
        notes.append("Opret en ekstern testkonto under /admin/impersonation for at teste, hvad eksterne brugere ser.")
    for t in sensitive:
        if not external:
            break
        acc = external[0]
        if any(c["tool"] == t["name"] and c.get("as_user_id") == acc["id"] for c in cases):
            continue
        args = _example_args(t)
        out.append({"key": f"protected:{t['name']}:{acc['id']}", "why": "adgang", "seen": calls.get(t["name"], 0),
                    "testcase": {"kind": "direct", "name": f"{t['name']} som {acc['display_name']}: personfelter er skjult",
                                 "tool": t["name"], "args": args or {}, "expect": {"protected": True}, "as_user_id": acc["id"]},
                    "needsArgs": args is None})
    return {"items": out[:MAX_PROPOSALS], "notes": notes}


async def ai_questions(tools: list[dict], user_id: int) -> list[dict]:
    """Natural questions an AI client might get, one per tool, as AI test cases. [] without AI."""
    meta = [{"tool": t["name"], "beskrivelse": (t.get("description") or "")[:300],
             "parametre": list((t.get("inputSchema") or {}).get("properties", {}))} for t in tools[:15]]
    text = await assist.complete(
        assist.SYSTEM + " Skriv ét kort spørgsmål pr. tool, som en bruger kunne stille en AI-klient, og som skal få klienten "
        "til at vælge netop det tool. Ingen personnavne, CPR, telefonnumre eller e-mails. Svar som JSON "
        "[{\"tool\": \"…\", \"question\": \"…\"}].", json.dumps(meta, ensure_ascii=False), user_id=user_id, max_tokens=600)
    parsed = assist._json(text)
    names = {t["name"] for t in tools}
    out = []
    for p in parsed if isinstance(parsed, list) else []:
        if isinstance(p, dict) and p.get("tool") in names and str(p.get("question") or "").strip():
            q = str(p["question"]).strip()[:300]
            out.append({"key": f"ai:{p['tool']}:{len(out)}", "why": "AI-spørgsmål", "seen": 0, "needsArgs": False,
                        "testcase": {"kind": "ai", "name": q[:120], "question": q, "expect": {"tools": [p["tool"]]}}})
    return out


async def variants(question: str, n: int, user_id: int) -> list[str]:
    """"Lav varianter": the same question asked in other words. [] without AI."""
    text = await assist.complete(
        assist.SYSTEM + f" Omskriv spørgsmålet på {n} forskellige måder med samme betydning, som forskellige brugere ville "
        "skrive det. Ingen personoplysninger. Svar som JSON-liste af strenge.", question[:1000], user_id=user_id, max_tokens=400)
    parsed = assist._json(text)
    return [str(x).strip()[:300] for x in parsed if str(x).strip() and str(x).strip() != question.strip()][:n] \
        if isinstance(parsed, list) else []


def _tool_descriptions(definition: dict | None) -> dict[str, str]:
    return {t.get("name"): str(t.get("description") or "") for t in ((definition or {}).get("tools") or []) if t.get("name")}


async def explain(conn, key: str, tc: dict, user_id: int) -> dict:
    """Why a test case fails: its problems, and how the tools' descriptions differ between the published endpoint and
    the draft (a changed description is the usual reason an AI client picks another tool)."""
    last = await db.fetch_one(conn, """select passed, detail, ran_at from dev_mcp_test_runs where testcase_id = %s
                                       order by id desc limit 1""", (tc["id"],))
    problems = ((last or {}).get("detail") or {}).get("problems") or []
    changes, path = [], None
    a = await artifacts.by_slug(conn, "mcp_endpoint", key)
    if a is not None:
        path = f"/developer/datastudie/endpoints/{a['id']}"
        cur = await artifacts.current(conn, a)
        draft = await artifacts.open_version(conn, a["id"])
        before, after = _tool_descriptions(cur["definition"] if cur else None), _tool_descriptions(draft["definition"] if draft else None)
        if draft:
            for name in sorted(set(before) | set(after)):
                if before.get(name) != after.get(name):
                    changes.append({"tool": name, "before": before.get(name), "after": after.get(name)})
    involved = {tc.get("tool")} | set((tc.get("expect") or {}).get("tools") or []) | \
        {s.get("tool") for s in (((last or {}).get("detail") or {}).get("steps") or []) if isinstance(s, dict)}
    changes.sort(key=lambda c: c["tool"] not in involved)
    if last is None:
        text = "Testcasen er ikke kørt endnu."
    elif last["passed"]:
        text = "Testcasen bestod seneste gang."
    else:
        text = "; ".join(problems) or "Testcasen fejlede."
        if changes:
            text += ". " + " ".join(f"Beskrivelsen af {c['tool']} er ændret i kladden." for c in changes[:3])
    ai_text = None
    if last is not None and not last["passed"]:
        ai_text = await assist.complete(
            assist.SYSTEM + " Forklar kort, hvorfor testcasen for et MCP-endpoint fejler, og hvad der skal rettes. Peg på en "
            "ændret toolbeskrivelse, hvis den er årsagen, og foreslå en bedre beskrivelse.",
            json.dumps({"testcase": {"type": tc["kind"], "spørgsmål": tc.get("question"), "tool": tc.get("tool"),
                                     "forventning": tc.get("expect")},
                        "problemer": problems, "ændrede_beskrivelser": changes[:5]}, ensure_ascii=False, default=str),
            user_id=user_id, max_tokens=400)
    return {"passed": None if last is None else last["passed"], "problems": problems, "changes": changes[:5],
            "text": ai_text or text, "ai": bool(ai_text), "endpointPath": path}

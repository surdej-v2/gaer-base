"""Optional AI chat through an OpenAI-compatible endpoint — by default OVHcloud AI Endpoints,
so prompts are processed in Europe. Off unless AI_API_KEY is set.

The server is stateless: the client sends the conversation each time. Tools are *client tools*:
the model asks for e.g. insert_text, the browser runs it (in Word/Outlook via the Office shell)
and sends the result back in the next request. Prompts and answers are never stored; only token
usage is, for the monthly limit and budgets per user (ai_usage, app/consumption.py).
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app import config, db, integrations, route_manifest, settings_store

Surface = Literal["app", "extension", "word", "outlook", "document", "mobile", "developer"]

# Tests replace this with an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    return bool(config.settings.ai_api_key)


# ── Tools per surface ──────────────────────────────────────────────────────

def _fn(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


TEXT = {"text": {"type": "string", "description": "Teksten"}}
SAVE_CLIP = _fn("save_clip", "Gem en tekst som klip, så brugeren kan finde den igen.",
                {"title": {"type": "string"}, "body": {"type": "string"}}, ["title", "body"])

# Files in the user's home (~/) and the common /shared/ folder (app/filetree.py). The browser runs them against
# /api/files with the user's own session, so the same access rules apply as in the app.
PATH = {"path": {"type": "string", "description": "Sti, fx ~/noter.md eller /shared/projekt/brief.md"}}
FILE_TOOLS = [
    _fn("list_files", "Vis mapper og filer i en mappe. Brugerens egne filer ligger i ~/, fælles projektfiler i /shared/.",
        {"path": {"type": "string", "description": "Mappe, fx ~/ eller /shared/projekt/"}}),
    _fn("read_file", "Læs en tekstfil (Markdown, CSV, JSON …) fra ~/ eller /shared/.", PATH, ["path"]),
    _fn("save_file", "Gem tekst som en fil. Brug /shared/<projekt>/ til projektfiler, som andre skal kunne se, og ~/ til "
        "brugerens egne. En eksisterende fil med samme navn erstattes.",
        {**PATH, "content": {"type": "string", "description": "Hele teksten"}}, ["path", "content"]),
]

TOOLS: dict[str, list[dict]] = {
    "word": [
        _fn("get_selection", "Hent den tekst, brugeren har markeret i dokumentet."),
        _fn("insert_text", "Erstat markeringen i dokumentet (eller indsæt ved markøren) med tekst.", TEXT, ["text"]),
        _fn("insert_paragraph", "Tilføj et afsnit i starten eller slutningen af dokumentet.",
            {**TEXT, "location": {"type": "string", "enum": ["Start", "End"]}}, ["text"]),
        _fn("add_comment", "Tilføj en kommentar til markeringen.", TEXT, ["text"]),
        SAVE_CLIP, *FILE_TOOLS,
    ],
    "outlook": [
        _fn("insert_text", "Indsæt tekst ved markøren i den mail, brugeren skriver.", TEXT, ["text"]),
        _fn("set_subject", "Sæt emnet på den mail, brugeren skriver.", {"subject": {"type": "string"}}, ["subject"]),
        _fn("open_reply", "Åbn et svar på den viste mail med teksten som udkast.",
            {**TEXT, "all": {"type": "boolean", "description": "Svar alle"}}, ["text"]),
        SAVE_CLIP, *FILE_TOOLS,
    ],
    "document": [_fn("insert_text", "Indsæt tekst i den markerede celle eller tekstboks.", TEXT, ["text"]), SAVE_CLIP,
                 *FILE_TOOLS],
    "extension": [SAVE_CLIP, *FILE_TOOLS],
    "mobile": [SAVE_CLIP, *FILE_TOOLS],
    "app": [SAVE_CLIP, *FILE_TOOLS],
    "developer": [],  # filled by app/devkit/ai_tools.py: reading tools and proposals for the studios' editors
}

SURFACE_TEXT = {
    "word": "Brugeren arbejder i et Word-dokument.",
    "outlook": "Brugeren arbejder i Outlook.",
    "document": "Brugeren arbejder i Excel eller PowerPoint.",
    "extension": "Brugeren bruger browserudvidelsen og ser en webside.",
    "mobile": "Brugeren bruger mobil-appen.",
    "app": "Brugeren bruger web-appen.",
    "developer": ("Brugeren arbejder i Gær Developer (/developer): datasæt, pipelines, MCP-endpoints, API'er og flows. "
                  "Du ser kun metadata (navne, typer, klassifikation) og aldrig værdier. Du gemmer aldrig selv: "
                  "foreslå ændringer med propose-værktøjerne, så brugeren kan se dem i editoren og gemme."),
}


# ── Request model ──────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    id: str = Field(max_length=100)
    name: str = Field(max_length=64)
    arguments: str = Field(max_length=50_000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(default="", max_length=50_000)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=10)
    tool_call_id: str | None = Field(default=None, max_length=100)


class ChatIn(BaseModel):
    surface: Surface = "app"
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    context: str | None = Field(default=None, max_length=200_000)
    context_label: str | None = Field(default=None, max_length=200)
    page: str | None = Field(default=None, max_length=300)  # the path the user is on, e.g. /klip


# ── The signed-in experience ───────────────────────────────────────────────
# The model is told who is signed in, which page they are on and which pages they can open, so it can
# answer "where do I …" with a link. Only the display name and role are sent — never e-mail or phone —
# and the page is matched against the route manifest, so free text from the client never reaches the prompt.

@dataclass(frozen=True)
class SignedIn:
    name: str | None
    role: str                                  # "user" | "admin"
    disabled_features: frozenset[str] = field(default_factory=frozenset)
    impersonated: bool = False                 # an admin is viewing the app as this test account


ROLE_TEXT = {"user": "bruger", "admin": "administrator"}
PAGE_RE = re.compile(r"^/[A-Za-z0-9/_\-.~]{0,200}$")
NOT_A_DESTINATION = {"/login", "/forbind", "/godkend", "/go"}  # flows, not pages to send someone to


def _clean_name(name: str | None) -> str | None:
    name = " ".join((name or "").split())[:80]
    return name or None


def current_page(path: str | None) -> tuple[str, str] | None:
    """(path, title) for a path the app answers, or None."""
    path = (path or "").split("?", 1)[0].split("#", 1)[0]
    if not PAGE_RE.match(path):
        return None
    route = route_manifest.route_for(path)
    if not route or route["app"] not in ("app", "admin"):
        return None
    return path, route["title"]


def pages_for(who: SignedIn, surface: Surface) -> list[tuple[str, str]]:
    """The app's pages this user can open, as (path, title). The mobile app lists its own /m pages."""
    mobile = surface == "mobile"
    if surface == "developer":
        return [(r["path"], r["title"]) for r in route_manifest.ROUTES
                if r["path"].startswith("/developer") and ":" not in r["path"]]
    out = []
    for r in route_manifest.ROUTES:
        path = r["path"]
        if r["app"] != "app" or r["access"] not in ("user", "public") or ":" in path or path in NOT_A_DESTINATION:
            continue
        if r.get("feature") in who.disabled_features:
            continue
        if mobile != (path == "/m" or path.startswith("/m/")) or path == "/m/login":
            continue
        if not mobile and r.get("chrome") == "own":
            continue
        out.append((path, r["title"]))
    if who.role == "admin" and not mobile:
        out.append(("/admin", "Administration"))
    out.append(("/w/", "Den offentlige side"))
    return out


def signed_in_text(who: SignedIn, page: str | None, surface: Surface) -> str:
    name = _clean_name(who.name)
    role = ROLE_TEXT.get(who.role, who.role)
    parts = [f"Brugeren er logget ind som {name} ({role})." if name else f"Brugeren er logget ind ({role}) uden et navn på profilen."]
    if who.impersonated:
        parts.append("En administrator ser appen som denne testkonto.")
    here = current_page(page)
    if here:
        parts.append(f"Brugeren står på siden «{here[1]}» ({here[0]}).")
    links = ", ".join(f"{title} ({path})" for path, title in pages_for(who, surface))
    parts.append(f"Sider brugeren kan åbne: {links}. Henvis til dem som Markdown-links med stien, fx [Klip](/klip). "
                 "Du ser ikke skærmen; du ved kun det, der står her og i konteksten.")
    return " ".join(parts)


def build_messages(body: ChatIn, app_name: str, who: SignedIn | None = None) -> list[dict]:
    s = config.settings
    system = (f"Du er en hjælpsom assistent i {app_name}. {SURFACE_TEXT[body.surface]} "
              "Svar kort og præcist på dansk, medmindre brugeren skriver på et andet sprog. "
              "Brug kun værktøjerne, når brugeren beder om at ændre noget eller gemme noget. "
              "Opfind ikke fakta; sig det, hvis konteksten ikke indeholder svaret. "
              "Brugeren har egne filer i ~/ og fælles projektfiler i /shared/; læs og gem dem med fil-værktøjerne.")
    if who:
        system += " " + signed_in_text(who, body.page, body.surface)
    out = [{"role": "system", "content": system}]
    if body.context:
        label = body.context_label or "Kontekst"
        out.append({"role": "system", "content": f"{label}:\n\n{body.context[:s.ai_max_context_chars]}"})
    for m in body.messages:
        if m.role == "assistant" and m.tool_calls:
            out.append({"role": "assistant", "content": m.content or None, "tool_calls": [
                {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                for t in m.tool_calls]})
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


# ── Limits and usage ───────────────────────────────────────────────────────

async def limit_for(conn, user_id: int) -> int:
    row = await db.fetch_one(conn, "select ai_monthly_tokens from users where id = %s", (user_id,))
    if row and row["ai_monthly_tokens"] is not None:
        return row["ai_monthly_tokens"]
    return int(await settings_store.get(conn, "ai_default_monthly_tokens"))


async def used_this_month(conn, user_id: int) -> int:
    row = await db.fetch_one(conn, """
        select coalesce(sum(prompt_tokens + completion_tokens), 0) as n from ai_usage
        where user_id = %s and ts >= date_trunc('month', now())""", (user_id,))
    return int(row["n"])


async def record_usage(user_id: int, surface: str, prompt: int, completion: int, estimated: bool) -> None:
    from app import consumption
    async with db.connection() as conn:
        await conn.execute("""
            insert into ai_usage (user_id, surface, model, prompt_tokens, completion_tokens, estimated)
            values (%s, %s, %s, %s, %s, %s)""",
            (user_id, surface, config.settings.ai_model, prompt, completion, estimated))
        # Warnings and caps of the user's budget: stored once per month and sent as webhooks (app/consumption.py).
        await consumption.after_usage(conn, user_id)
    from app import events
    await events.emit("ai.tokens", ref=surface, value=prompt + completion)


# ── Streaming ──────────────────────────────────────────────────────────────

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_chat(body: ChatIn, user_id: int, app_name: str, who: SignedIn | None = None) -> AsyncIterator[str]:
    """Calls the provider and re-emits its stream as our own events:
    delta {text} · tool_calls {tool_calls:[{id,name,arguments}]} · done {usage} · error {message}."""
    s = config.settings
    messages = build_messages(body, app_name, who)
    payload = {"model": s.ai_model, "messages": messages, "stream": True,
               "stream_options": {"include_usage": True}, "max_tokens": s.ai_max_output_tokens, "temperature": 0.3}
    if TOOLS.get(body.surface):
        payload["tools"] = TOOLS[body.surface]

    text_parts: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict | None = None
    try:
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(120, connect=10),
                                     event_hooks=integrations.hooks("ai")) as client:
            async with client.stream("POST", f"{s.ai_base_url.rstrip('/')}/chat/completions", json=payload,
                                     headers={"Authorization": f"Bearer {s.ai_api_key}"}) as r:
                if r.status_code >= 300:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    yield sse("error", {"message": f"AI-tjenesten svarede {r.status_code}", "detail": detail})
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield sse("delta", {"text": delta["content"]})
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
    except httpx.HTTPError as e:
        yield sse("error", {"message": "AI-tjenesten kunne ikke nås", "detail": type(e).__name__})
        return

    if calls:
        valid = {t["function"]["name"] for t in TOOLS.get(body.surface, [])}
        tool_calls = [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items()) if c["name"] in valid]
        if tool_calls:
            yield sse("tool_calls", {"tool_calls": tool_calls})

    estimated = usage is None
    prompt = int(usage.get("prompt_tokens", 0)) if usage else sum(len(str(m.get("content") or "")) for m in messages) // 4
    completion = int(usage.get("completion_tokens", 0)) if usage else \
        (len("".join(text_parts)) + sum(len(c["arguments"]) for c in calls.values())) // 4
    await record_usage(user_id, body.surface, prompt, completion, estimated)
    yield sse("done", {"usage": {"prompt_tokens": prompt, "completion_tokens": completion, "estimated": estimated}})

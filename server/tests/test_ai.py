import dataclasses
import json

import httpx
import pytest

from app import ai, config
from tests.helpers import full_user, otp_login

sent: list[dict] = []


def sse_lines(chunks: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def provider(chunks: list[dict], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     "body": json.loads(request.content)})
        if status != 200:
            return httpx.Response(status, text="overloaded")
        return httpx.Response(200, content=sse_lines(chunks), headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


@pytest.fixture
def ai_on(monkeypatch):
    sent.clear()
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_api_key="test-key"))

    def use(chunks, status=200):
        monkeypatch.setattr(ai, "transport", provider(chunks, status))
    return use


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_off_without_key(client):
    await otp_login(client, "12345678")
    assert (await client.get("/api/ai/status")).json() == {"enabled": False}
    r = await client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "hej"}]})
    assert r.status_code == 404


async def test_streams_text_and_records_usage_without_storing_text(client, ai_on, owner_conn):
    ai_on([{"choices": [{"delta": {"content": "Hej "}}]}, {"choices": [{"delta": {"content": "Mette"}}]},
           {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 7}}])
    await otp_login(client, "12345678")
    r = await client.post("/api/ai/chat", json={"surface": "extension", "context": "Sidetekst", "context_label": "Websiden",
                                                "messages": [{"role": "user", "content": "Opsummér"}]})
    ev = events(r.text)
    assert [e for e, _ in ev] == ["delta", "delta", "done"]
    assert "".join(d["text"] for e, d in ev if e == "delta") == "Hej Mette"

    body = sent[0]["body"]
    assert sent[0]["url"].startswith("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions")
    assert sent[0]["auth"] == "Bearer test-key"
    assert body["messages"][1] == {"role": "system", "content": "Websiden:\n\nSidetekst"}
    assert [t["function"]["name"] for t in body["tools"]] == ["save_clip"]

    row = owner_conn.execute("select surface, prompt_tokens, completion_tokens, estimated from ai_usage").fetchone()
    assert row == ("extension", 120, 7, False)
    assert (await client.get("/api/ai/status")).json() == {"enabled": True, "used": 127, "limit": 200000}


async def test_word_tool_calls_are_assembled(client, ai_on):
    ai_on([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "insert_text", "arguments": '{"te'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'xt": "Kære Mette"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "rm_rf", "arguments": "{}"}}]}}]},
    ])
    await otp_login(client, "12345678")
    r = await client.post("/api/ai/chat", json={"surface": "word", "messages": [
        {"role": "user", "content": "Skriv en hilsen"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c0", "name": "get_selection", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "c0", "content": "Hej"}]})
    ev = dict(events(r.text))
    assert ev["tool_calls"] == {"tool_calls": [{"id": "c1", "name": "insert_text", "arguments": '{"text": "Kære Mette"}'}]}
    assert ev["done"]["usage"]["estimated"] is True
    msgs = sent[0]["body"]["messages"]
    assert msgs[-2]["tool_calls"][0]["function"]["name"] == "get_selection"
    assert msgs[-1] == {"role": "tool", "tool_call_id": "c0", "content": "Hej"}


async def test_monthly_limit(make_client, ai_on):
    ai_on([{"choices": [{"delta": {"content": "ok"}}]}, {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 50}}])
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    assert (await admin.put(f"/api/admin/ai/users/{user_id}", json={"monthly_tokens": 80})).status_code == 200

    assert (await user.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "a"}]})).status_code == 200
    r = await user.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "b"}]})
    assert r.status_code == 429

    await admin.put(f"/api/admin/ai/users/{user_id}", json={"monthly_tokens": 0})
    assert (await user.get("/api/ai/status")).json()["enabled"] is False

    overview = (await admin.get("/api/admin/ai")).json()
    assert overview["enabled"] and overview["model"] == "Meta-Llama-3_3-70B-Instruct"
    assert {u["id"]: u["used"] for u in overview["users"]}[user_id] == 100


async def test_provider_error_is_reported(client, ai_on, owner_conn):
    ai_on([], status=503)
    await otp_login(client, "12345678")
    r = await client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "hej"}]})
    assert events(r.text)[0][0] == "error"
    assert owner_conn.execute("select count(*) from ai_usage").fetchone()[0] == 0

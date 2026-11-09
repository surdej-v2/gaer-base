"""Viden (specs/knowledge): upload → extract → chunk → index, agents with versions and access, grounded answers."""

import dataclasses
import io
import json
import zipfile

import httpx
import pytest

from app import ai, config, db, jobs
from app.knowledge import chunk as chunker
from app.knowledge import extract as ex
from app.storage import get_storage
from tests.helpers import full_user, otp_login

HANDBOOK = """# Ferie

Medarbejdere optjener 25 feriedage om året. Op til fem ubrugte feriedage kan overføres til første kvartal
året efter. Overførte dage, der ikke er afholdt inden 31. marts, bortfalder.

# Sygdom

Giv din leder besked før arbejdstid på første sygedag. Lægeerklæring kræves efter tre sygedage.
"""


def docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    """A minimal Word document: (style, text) pairs."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p>{f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""}<w:r><w:t>{text}</w:t></w:r></w:p>'
        for style, text in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


async def _admin_and_user(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    return admin, user


async def _space_with_handbook(admin) -> int:
    r = await admin.post("/api/admin/knowledge/spaces", json={"name": "Personalehåndbog"})
    assert r.status_code == 200, r.text
    space_id = r.json()["id"]
    archive = zip_bytes({
        "politikker/ferie.md": HANDBOOK.encode(),
        "politikker/rejser.docx": docx_bytes([("Heading1", "Rejser"), ("", "Dagpenge udbetales efter statens takster.")]),
        "media/video.mp4": b"\x00" * 10,
        "__MACOSX/._ferie.md": b"junk",
    })
    r = await admin.post(f"/api/admin/knowledge/spaces/{space_id}/upload",
                         files=[("files", ("haandbog.zip", archive, "application/zip"))])
    assert r.status_code == 200, r.text
    assert [d["path"] for d in r.json()["added"]] == ["politikker/ferie.md", "politikker/rejser.docx"]
    assert r.json()["skipped"] == ["media/video.mp4"]
    await jobs.run_pending()
    return space_id


# ── Units ──────────────────────────────────────────────────────────────────

def test_extract_formats():
    md = ex.extract(docx_bytes([("Heading2", "Overskrift"), ("", "Brødtekst")]), "a.docx").markdown
    assert md == "## Overskrift\n\nBrødtekst"
    assert ex.extract(b"<h1>Titel</h1><script>x()</script><p>Tekst</p>", "a.html").markdown == "# Titel\n\nTekst"
    assert ex.extract("navn,by\nAnna,Aarhus\n".encode(), "a.csv").markdown == "navn | by\nAnna | Aarhus"
    with pytest.raises(ex.UnsupportedFormat):
        ex.extract(b"x", "a.exe")
    with pytest.raises(ex.BrokenFile):
        ex.extract(b"not a zip", "a.docx")
    with pytest.raises(ex.BrokenFile):
        ex.extract(b"%PDF-1.4 garbage", "a.pdf")


def test_chunker_sections_pages_and_overlap():
    md = "<!-- page 1 -->\n\n# A\n\n" + "\n\n".join(f"Afsnit {i} " + "ord " * 120 for i in range(6)) \
         + "\n\n<!-- page 2 -->\n\n# B\n\nKort tekst."
    chunks = chunker.chunk_markdown(md, max_tokens=200, overlap_tokens=20)
    assert {c.section for c in chunks} == {"A", "B"}
    assert chunks[0].page == 1 and chunks[-1].page == 2 and chunks[-1].content == "Kort tekst."
    assert all(c.tokens <= 220 for c in chunks)
    a = [c for c in chunks if c.section == "A"]
    assert len(a) > 1 and a[1].content.split()[0] != "Afsnit"  # starts with the overlap from the previous chunk
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # a single huge block is cut hard
    assert len(chunker.chunk_markdown("x" * 10_000, max_tokens=500)) == 5


# ── API ────────────────────────────────────────────────────────────────────

async def test_admin_only(make_client):
    _, user = await _admin_and_user(make_client)
    assert (await user.get("/api/admin/knowledge/overview")).status_code == 403


async def test_upload_index_publish_and_chat(make_client, owner_conn):
    admin, user = await _admin_and_user(make_client)
    space_id = await _space_with_handbook(admin)

    space = (await admin.get(f"/api/admin/knowledge/spaces/{space_id}")).json()
    assert [(d["filename"], d["status"]) for d in space["documents"]] == [("ferie.md", "indexed"), ("rejser.docx", "indexed")]
    ferie = space["documents"][0]
    assert ferie["chunk_count"] == 2 and not ferie["embedded"]
    doc = (await admin.get(f"/api/admin/knowledge/documents/{ferie['id']}")).json()
    assert doc["markdown"].startswith("# Ferie") and [c["section"] for c in doc["chunks"]] == ["Ferie", "Sygdom"]
    log = (await admin.get(f"/api/admin/knowledge/spaces/{space_id}/log")).json()
    assert {"queued", "extract", "chunk", "done"} <= {e["stage"] for e in log}

    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "Personale-assistent", "space_ids": [space_id]})).json()
    assert agent["slug"] == "personale-assistent"
    r = await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={
        "welcome": "Hej! Spørg om ferie og sygdom.", "suggested_prompts": ["Kan jeg overføre ferie?", " "]})
    assert r.json()["suggested_prompts"] == ["Kan jeg overføre ferie?"]

    # Without AI the answer is the best excerpts, with citations.
    preview = (await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/preview",
                                json={"question": "Kan jeg overføre ubrugte feriedage?"})).json()
    assert preview["method"] == "fulltext" and "31. marts" in preview["answer"]
    assert preview["citations"][0]["filename"] == "ferie.md" and preview["citations"][0]["section"] == "Ferie"

    # Not published yet: users cannot see it.
    assert (await user.get(f"/api/knowledge/agents/{agent['slug']}")).status_code == 404
    assert (await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={"note": "Første"})).json() == {"version": "1.0"}
    detail = (await admin.get(f"/api/admin/knowledge/agents/{agent['id']}")).json()
    assert detail["status"] == "published" and not detail["unpublished_changes"]

    profile = (await user.get(f"/api/knowledge/agents/{agent['slug']}")).json()
    assert profile["version"] == "1.0" and profile["welcome"].startswith("Hej") and not profile["can_manage"]
    assert [a["slug"] for a in (await user.get("/api/knowledge/agents")).json()] == ["personale-assistent"]

    r = await user.post(f"/api/knowledge/agents/{agent['slug']}/ask", json={"question": "Hvor mange sygedage før lægeerklæring?"})
    assert r.status_code == 200, r.text
    conv_id = r.json()["conversation_id"]
    assert r.json()["message"]["citations"][0]["section"] == "Sygdom"
    r = await user.post(f"/api/knowledge/agents/{agent['slug']}/ask",
                        json={"question": "Hvad med dagpenge på rejser?", "conversation_id": conv_id})
    assert r.json()["message"]["citations"][0]["filename"] == "rejser.docx"
    conv = (await user.get(f"/api/knowledge/conversations/{conv_id}")).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "agent", "user", "agent"]
    assert [c["title"] for c in (await user.get(f"/api/knowledge/agents/{agent['slug']}/conversations")).json()] \
        == ["Hvor mange sygedage før lægeerklæring?"]

    # A cited original can be opened by the user, but only documents in the agent's spaces.
    cited = conv["messages"][1]["citations"][0]["document_id"]
    assert (await user.get(f"/api/knowledge/agents/{agent['slug']}/documents/{cited}/link")).status_code == 200
    other_space = (await admin.post("/api/admin/knowledge/spaces", json={"name": "Hemmeligt"})).json()["id"]
    up = await admin.post(f"/api/admin/knowledge/spaces/{other_space}/upload", files=[("files", ("x.txt", b"hemmelig tekst", "text/plain"))])
    secret = up.json()["added"][0]["id"]
    assert (await user.get(f"/api/knowledge/agents/{agent['slug']}/documents/{secret}/link")).status_code == 404

    # Another user cannot read the conversation.
    stranger = make_client()
    await otp_login(stranger, "33333333")
    assert (await stranger.get(f"/api/knowledge/conversations/{conv_id}")).status_code == 404
    assert (await stranger.post(f"/api/knowledge/agents/{agent['slug']}/ask",
                                json={"question": "x", "conversation_id": conv_id})).status_code == 404

    # Users chat with the published snapshot, not the working copy.
    await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"name": "Nyt navn"})
    assert (await user.get(f"/api/knowledge/agents/{agent['slug']}")).json()["name"] == "Personale-assistent"
    assert (await admin.get(f"/api/admin/knowledge/agents/{agent['id']}")).json()["unpublished_changes"]

    actions = [r[0] for r in owner_conn.execute(
        "select action from audit_log where table_name like 'knowledge%%' order by id").fetchall()]
    assert actions.count("file.upload") == 3 and "knowledge.publish" in actions and actions.count("create") >= 4


async def test_versions_and_promote(make_client):
    admin, _ = await _admin_and_user(make_client)
    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "Agent"})).json()
    await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"instructions": "Version et"})
    await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={})
    await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"instructions": "Version to"})
    assert (await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={"kind": "major"})).json()["version"] == "2.0"
    detail = (await admin.get(f"/api/admin/knowledge/agents/{agent['id']}")).json()
    first = next(v for v in detail["versions"] if (v["major"], v["minor"]) == (1, 0))
    r = await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/versions/{first['id']}/promote")
    assert r.json() == {"version": "2.1"}
    detail = (await admin.get(f"/api/admin/knowledge/agents/{agent['id']}")).json()
    assert detail["instructions"] == "Version et" and detail["versions"][0]["note"] == "Gendannet fra v1.0"
    # the slug is unique
    again = (await admin.post("/api/admin/knowledge/agents", json={"name": "Agent"})).json()
    assert again["slug"] == "agent-2"
    r = await admin.patch(f"/api/admin/knowledge/agents/{again['id']}", json={"slug": "agent"})
    assert r.status_code == 409


async def test_members_only_access(make_client, owner_conn):
    admin, user = await _admin_and_user(make_client)
    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "Lukket"})).json()
    await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"access": "members"})
    await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={})
    assert (await user.get("/api/knowledge/agents/lukket")).status_code == 404
    assert (await admin.get("/api/knowledge/agents/lukket")).status_code == 200  # admins can always use it

    assert (await admin.put(f"/api/admin/knowledge/agents/{agent['id']}/members",
                            json={"email": "ukendt@example.com"})).status_code == 404
    owner_conn.execute("update users set email = 'bruger@example.com' where phone = '+4522222222'")
    r = await admin.put(f"/api/admin/knowledge/agents/{agent['id']}/members", json={"email": "Bruger@example.com"})
    assert r.status_code == 200, r.text
    assert (await user.get("/api/knowledge/agents/lukket")).status_code == 200
    assert (await admin.delete(f"/api/admin/knowledge/agents/{agent['id']}/members/{r.json()['user_id']}")).status_code == 200
    assert (await user.get("/api/knowledge/agents/lukket")).status_code == 404


async def test_bin_restore_and_purge(make_client):
    admin, _ = await _admin_and_user(make_client)
    space_id = await _space_with_handbook(admin)
    assert len(get_storage().objects) == 2
    assert (await admin.delete(f"/api/admin/knowledge/spaces/{space_id}")).status_code == 200
    overview = (await admin.get("/api/admin/knowledge/overview")).json()
    assert overview["spaces"] == [] and [s["id"] for s in overview["bin"]] == [space_id]
    # an agent on a binned space finds nothing
    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "A"})).json()
    assert (await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"space_ids": [space_id]})).status_code == 400

    assert (await admin.post(f"/api/admin/knowledge/spaces/{space_id}/restore")).status_code == 200
    assert (await admin.delete(f"/api/admin/knowledge/spaces/{space_id}/purge")).status_code == 404  # not in the bin
    await admin.delete(f"/api/admin/knowledge/spaces/{space_id}")
    assert (await admin.delete(f"/api/admin/knowledge/spaces/{space_id}/purge")).json() == {"ok": True, "files": 2}
    assert get_storage().objects == {}


async def test_broken_and_retry(make_client):
    admin, _ = await _admin_and_user(make_client)
    space_id = (await admin.post("/api/admin/knowledge/spaces", json={"name": "R"})).json()["id"]
    await admin.post(f"/api/admin/knowledge/spaces/{space_id}/upload",
                     files=[("files", ("ødelagt.pdf", b"%PDF-1.4 nonsense", "application/pdf"))])
    await jobs.run_pending()
    doc = (await admin.get(f"/api/admin/knowledge/spaces/{space_id}")).json()["documents"][0]
    assert doc["status"] == "failed" and "PDF" in doc["error"]
    r = await admin.post(f"/api/admin/knowledge/spaces/{space_id}/upload", files=[("files", ("a.exe", b"x", "x"))])
    assert r.status_code == 400


async def test_ai_answers_with_embeddings(make_client, monkeypatch, owner_conn):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append({"path": request.url.path, "body": body})
        if request.url.path.endswith("/embeddings"):
            def vec(text):  # "ferie" texts point one way, everything else another
                return [1.0, 0.1] if "ferie" in text.lower() else [0.1, 1.0]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": vec(t)} for i, t in enumerate(body["input"])]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ja, op til fem dage [1]."}}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 9}})

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_api_key="test-key"))
    monkeypatch.setattr(ai, "transport", httpx.MockTransport(handler))
    admin, user = await _admin_and_user(make_client)
    space_id = await _space_with_handbook(admin)
    docs = (await admin.get(f"/api/admin/knowledge/spaces/{space_id}")).json()["documents"]
    assert all(d["embedded"] for d in docs)

    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "AI", "space_ids": [space_id]})).json()
    await admin.patch(f"/api/admin/knowledge/agents/{agent['id']}", json={"instructions": "Svar som en venlig HR-medarbejder."})
    await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={})
    r = await user.post("/api/knowledge/agents/ai/ask", json={"question": "Kan jeg gemme ferie?"})
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "vector" and r.json()["message"]["text"] == "Ja, op til fem dage [1]."
    assert [c["n"] for c in r.json()["message"]["citations"]] == [1]
    chat = [s for s in sent if s["path"].endswith("/chat/completions")][-1]["body"]
    system = chat["messages"][0]["content"]
    assert system.startswith("Svar som en venlig HR-medarbejder.") and "Brug KUN de nummererede uddrag" in system
    assert "Ferie" in chat["messages"][-1]["content"].split("\n")[2]  # the best excerpt is the vacation chunk
    usage = owner_conn.execute("select surface, prompt_tokens, completion_tokens from ai_usage").fetchall()
    assert usage == [("knowledge", 120, 9)]


async def test_gdpr_export_and_erasure(make_client):
    admin, user = await _admin_and_user(make_client)
    space_id = await _space_with_handbook(admin)
    agent = (await admin.post("/api/admin/knowledge/agents", json={"name": "G", "space_ids": [space_id]})).json()
    await admin.post(f"/api/admin/knowledge/agents/{agent['id']}/publish", json={})
    await user.post("/api/knowledge/agents/g/ask", json={"question": "ferie"})
    from app.gdpr import build_user_export
    async with db.connection() as conn:
        me = await db.fetch_one(conn, "select id from users where phone = '+4522222222'")
        export = await build_user_export(conn, me["id"])
    [conv] = export["knowledge_conversations"]
    assert conv["agent"] == "G" and [m["role"] for m in conv["messages"]] == ["user", "agent"]
    async with db.connection() as conn:
        await conn.execute("delete from users where id = %s", (me["id"],))
        left = await db.fetch_one(conn, "select count(*)::int as n from knowledge_messages")
    assert left["n"] == 0

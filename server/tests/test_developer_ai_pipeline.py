"""AI in Gær Developer, fase 2 (specs/developer/AI.md): the pipeline editor's help — a $match that can move above a
$lookup (same rows, checked by preview), what each stage does, the grey completion (rules, and AI checked by
compiling), and the change note from the contract diff."""

from app.devkit import pipeline_hints
from tests.test_developer_ai import ai_on, reply, sent  # noqa: F401  (ai_on is a fixture)
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"

LATE_FILTER = {"name": "lejemaal_pr_ejendom", "key": "ejendom_id", "stages": [
    {"source": "lejemaal"},
    {"lookup": {"from": "ejendomme", "local": "ejendom_id", "foreign": "id", "fields": ["adresse"]}},
    {"match": {"status": "aktiv"}},
    {"group": {"by": "ejendom_id", "antal": "count"}},
]}


async def test_a_late_filter_moves_above_the_lookup_with_the_same_rows(make_client, owner_conn):
    admin, _, _ = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/pipelines/help", json={"definition": LATE_FILTER, "stage": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    [hint] = body["hints"]
    assert hint["from"] == 2 and hint["to"] == 1 and "status" in hint["text"]
    assert [next(iter(s)) for s in hint["stages"]] == ["source", "match", "lookup", "group"]

    before = (await admin.post(f"{DEV}/pipelines/preview", json={"definition": LATE_FILTER})).json()
    after = (await admin.post(f"{DEV}/pipelines/preview", json={"definition": {**LATE_FILTER, "stages": hint["stages"]}})).json()
    key = lambda rows: sorted((r["ejendom_id"], r["antal"]) for r in rows)  # noqa: E731
    assert key(before["rows"]) == key(after["rows"])

    # a filter on a field the lookup brings stays where it is
    on_lookup = {**LATE_FILTER, "stages": LATE_FILTER["stages"][:2] + [{"match": {"adresse": "Eksempelvej 1"}}] + LATE_FILTER["stages"][3:]}
    assert (await admin.post(f"{DEV}/pipelines/help", json={"definition": on_lookup})).json()["hints"] == []

    # the descriptions, and the grey text: sum the number that is not aggregated yet
    assert body["describe"][2] == "Beholder de rækker, hvor status = aktiv."
    assert body["describe"][1].startswith("Slår adresse op i ejendomme, hvor ejendom_id = id.")
    assert body["completion"]["key"] == "aarlig_leje_i_alt" and body["completion"]["value"] == "sum(aarlig_leje)"

    broken = {**LATE_FILTER, "stages": [{"source": "lejemaal"}, {"match": {"findes_ikke": 1}}]}
    out = (await admin.post(f"{DEV}/pipelines/help", json={"definition": broken})).json()
    assert out["error"] and out["hints"] == [] and len(out["describe"]) == 2


def test_match_fields_understands_or_and_refuses_the_unknown():
    assert pipeline_hints.match_fields({"$or": [{"a": 1}, {"b": {"$gt": 2}}], "c": None}) == {"a", "b", "c"}
    assert pipeline_hints.match_fields({"$where": "x"}) is None
    assert pipeline_hints.match_fields([]) is None


async def test_complete_uses_the_ai_only_when_it_compiles(make_client, owner_conn, ai_on):
    admin, _, _ = await _setup(make_client, owner_conn)
    reply["text"] = '{"key": "snit_leje", "value": "avg(aarlig_leje)", "why": "Gennemsnittet er nyttigt"}'
    r = await admin.post(f"{DEV}/pipelines/complete", json={"definition": LATE_FILTER, "stage": 3})
    assert r.status_code == 200, r.text
    assert r.json()["completion"] == {"key": "snit_leje", "value": "avg(aarlig_leje)", "why": "Gennemsnittet er nyttigt", "ai": True}
    reply["text"] = '{"key": "x", "value": "sum(findes_ikke)", "why": "?"}'
    c = (await admin.post(f"{DEV}/pipelines/complete", json={"definition": LATE_FILTER, "stage": 3})).json()["completion"]
    assert c["key"] == "aarlig_leje_i_alt" and "ai" not in c        # the AI's did not compile: the rule's
    assert (await admin.post(f"{DEV}/pipelines/complete", json={"definition": LATE_FILTER, "stage": 9})).status_code == 422


async def test_change_note_from_the_contract_diff(make_client, owner_conn):
    admin, _, _ = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/pipelines", json={"slug": "lejemaal_pr_ejendom", "name": "Lejemål pr. ejendom", "definition": LATE_FILTER})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    wider = {**LATE_FILTER, "stages": LATE_FILTER["stages"][:3] + [
        {"group": {"by": "ejendom_id", "antal": "count", "aarlig_leje_i_alt": "sum(aarlig_leje)"}}]}
    note = (await admin.post(f"{DEV}/pipelines/{pid}/note", json={"definition": wider})).json()
    assert note["ai"] is False and "aarlig_leje_i_alt" in note["note"]
    assert note["changeType"] in ("minor", "initial")


async def test_change_note_written_by_ai(make_client, owner_conn, ai_on):
    admin, _, _ = await _setup(make_client, owner_conn)
    pid = (await admin.post(f"{DEV}/pipelines", json={"slug": "lejemaal_pr_ejendom", "name": "L", "definition": LATE_FILTER})).json()["id"]
    reply["text"] = "En ny kolonne med den samlede årlige leje. Ingen læsere brydes."
    wider = {**LATE_FILTER, "stages": LATE_FILTER["stages"][:3] + [
        {"group": {"by": "ejendom_id", "antal": "count", "aarlig_leje_i_alt": "sum(aarlig_leje)"}}]}
    note = (await admin.post(f"{DEV}/pipelines/{pid}/note", json={"definition": wider})).json()
    assert note == {**note, "ai": True, "note": "En ny kolonne med den samlede årlige leje. Ingen læsere brydes."}
    assert "aarlig_leje_i_alt" in str(sent[-1]["messages"])

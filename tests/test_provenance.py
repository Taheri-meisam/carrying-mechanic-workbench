"""Draft history (task 4) and staleness / re-extraction (task 8)."""
import json, sqlite3
import pytest
from conftest import record, ALL_REVIEWED


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def test_extractions_is_empty_before_any_call(client):
    j = client.get("/api/extractions").json()
    assert j["count"] == 0 and j["instrument"]


def test_each_extract_appends_a_row(client, corpus):
    for _ in range(3):
        client.post("/api/extract", json={"name": "G", "entry": corpus[0]["entry"]})
    j = client.get("/api/extractions?game=G").json()
    assert j["count"] == 3
    assert [e["provider"] for e in j["extractions"]] == ["mock"] * 3


def test_history_is_newest_first(client, corpus):
    for _ in range(2):
        client.post("/api/extract", json={"name": "G", "entry": corpus[0]["entry"]})
    ts = [e["ts"] for e in client.get("/api/extractions?game=G").json()["extractions"]]
    assert ts == sorted(ts, reverse=True)


def test_history_filters_by_game(client, corpus):
    client.post("/api/extract", json={"name": "A", "entry": corpus[0]["entry"]})
    client.post("/api/extract", json={"name": "B", "entry": corpus[1]["entry"]})
    assert client.get("/api/extractions?game=A").json()["count"] == 1
    assert client.get("/api/extractions").json()["count"] == 2


def test_rows_carry_the_instrument_that_made_them(client, corpus):
    client.post("/api/extract", json={"name": "G", "entry": corpus[0]["entry"]})
    e = client.get("/api/extractions?game=G").json()["extractions"][0]
    assert e["instrument"] and e["promptHash"] and e["stale"] is False


def test_a_row_from_an_older_instrument_is_marked_stale(client, corpus, db):
    client.post("/api/extract", json={"name": "G", "entry": corpus[0]["entry"]})
    c = sqlite3.connect(db)
    c.execute("UPDATE extractions SET instrument='v0.0-ancient'"); c.commit(); c.close()
    assert client.get("/api/extractions?game=G").json()["extractions"][0]["stale"] is True


# ---- re-extraction ----------------------------------------------------------

def _seed(client, db, corpus, adjudicated=True):
    r = record(name=corpus[0]["name"])
    r["review"] = {"raw": record(name=corpus[0]["name"]), "entry": corpus[0]["entry"],
                   "coder": "C1", "meta": {"instrument": "v0.0-old", "tier": "default"},
                   "sections": ALL_REVIEWED if adjudicated else {}}
    r["dimensions"]["MC"]["level"] = 0                     # the coder's adjudicated value
    client.put("/api/records/g1", json=r)
    return r


def test_reextract_keeps_the_previous_draft(client, db, corpus):
    _seed(client, db, corpus)
    j = client.post("/api/records/g1/reextract").json()
    assert j["priorDrafts"] == 1
    rec = client.get("/api/records").json()[0]
    assert rec["review"]["priorRaw"][0]["meta"]["instrument"] == "v0.0-old"


def test_reextract_does_not_discard_the_coders_work(client, db, corpus):
    """A coder's adjudicated values and reviewed ticks are not ours to clear."""
    _seed(client, db, corpus)
    client.post("/api/records/g1/reextract")
    rec = client.get("/api/records").json()[0]
    assert rec["dimensions"]["MC"]["level"] == 0, "adjudicated value was overwritten"
    assert rec["review"]["sections"] == ALL_REVIEWED, "reviewed ticks were cleared"
    assert rec["review"]["coder"] == "C1"
    assert rec["review"]["needsRecheck"] is True, "the coder was not told to re-check"


def test_reextract_updates_the_provenance(client, db, corpus):
    _seed(client, db, corpus)
    client.post("/api/records/g1/reextract")
    rec = client.get("/api/records").json()[0]
    assert rec["review"]["meta"]["instrument"] != "v0.0-old"


def test_reextract_appends_to_the_history(client, db, corpus):
    _seed(client, db, corpus)
    client.post("/api/records/g1/reextract")
    j = client.get("/api/extractions?game=" + corpus[0]["name"]).json()
    assert j["count"] == 1


def test_reextract_refuses_without_a_stored_entry(client, db, corpus):
    r = record(name="NoEntry"); r["review"] = {"raw": record(), "entry": ""}
    client.put("/api/records/g2", json=r)
    assert client.post("/api/records/g2/reextract").status_code == 400


def test_reextract_404s_on_an_unknown_record(client):
    assert client.post("/api/records/nope/reextract").status_code == 404


def test_released_pool_carries_no_encyclopaedia_text():
    """The source entries belong to the Ministry and must not reach a public deposit.

    pool.json holds them because the workbench displays them; pool_public.json is the
    redistributable form. ORE requires deposited data under CC-BY-4.0 or CC0, which the
    authors cannot grant for someone else's text.
    """
    import pathlib
    from backend.check_release import entry_text
    papers = pathlib.Path(__file__).resolve().parent.parent / "analysis"
    from conftest import requires_records
    requires_records(papers / "pool.json", papers / "pool_public.json")
    assert entry_text(papers / "pool.json"), "working pool should still carry entries; the guard has nothing to catch"
    assert not entry_text(papers / "pool_public.json"), "pool_public.json must not carry entry text"


def test_public_pool_reproduces_the_published_figures():
    """Stripping the entries must not move a single reported number."""
    import json, pathlib
    import backend.evaluate as E
    papers = pathlib.Path(__file__).resolve().parent.parent / "analysis"
    from conftest import requires_records
    requires_records(papers / "pool.json", papers / "pool_public.json")
    a = E.evaluate(json.loads((papers / "pool.json").read_text(encoding="utf-8")))["accuracy"]
    b = E.evaluate(json.loads((papers / "pool_public.json").read_text(encoding="utf-8")))["accuracy"]
    assert {k: (v["agree"], v["n"]) for k, v in a.items()} == {k: (v["agree"], v["n"]) for k, v in b.items()}

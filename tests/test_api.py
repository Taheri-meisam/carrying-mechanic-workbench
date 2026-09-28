"""The HTTP surface, driven through TestClient against a temporary database."""
import json
import pytest
from conftest import record, ALL_REVIEWED


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")     # never call a model in a test
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def test_meta_reports_the_provider(client):
    m = client.get("/api/meta").json()
    assert m["provider"] == "mock" and m["promptHash"]


def test_extract_returns_draft_meta_and_flags(client, corpus):
    r = client.post("/api/extract", json={"name": corpus[0]["name"],
                                          "entry": corpus[0]["entry"], "tier": "default"})
    assert r.status_code == 200
    j = r.json()
    assert j["draft"]["name"] and j["meta"]["promptHash"] and "flags" in j


def test_extract_rejects_an_empty_entry(client):
    assert client.post("/api/extract", json={"entry": "   "}).status_code == 400


def test_extract_logs_provenance(client, corpus, db):
    import sqlite3
    client.post("/api/extract", json={"name": "X", "entry": corpus[0]["entry"]})
    rows = sqlite3.connect(db).execute(
        "SELECT provider, instrument, prompt_hash, flags FROM extractions").fetchall()
    assert rows and rows[0][0] == "mock" and rows[0][1] and rows[0][2] and rows[0][3]


def test_record_round_trip(client):
    client.put("/api/records/x1", json=record(name="X1"))
    got = client.get("/api/records").json()
    assert [r["name"] for r in got] == ["X1"]
    client.delete("/api/records/x1")
    assert client.get("/api/records").json() == []


def test_import_then_eval(client, drafts):
    """The round trip INVARIANTS.md asks for: import the nine drafts, evaluate them."""
    n = client.post("/api/records/import", json=drafts).json()["imported"]
    assert n == 9
    ev = client.get("/api/eval").json()
    assert ev["summary"]["n"] == 9


def test_export_csv_has_draft_and_adjudicated_columns(client, drafts):
    client.post("/api/records/import", json=drafts)
    csv = client.get("/api/export.csv").text
    head = csv.splitlines()[0]
    for col in ("id", "name", "coder", "provider", "route_draft", "route_adjudicated", "S_coder2"):
        assert col in head, col
    assert len(csv.splitlines()) == 10          # header + nine


def test_search_is_not_gated_on_an_api_key(client, monkeypatch):
    """It used to 501 without ANTHROPIC_API_KEY. It must now attempt the local path instead."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/search", json={"name": "zzz", "closest": "", "separatingRule": ""})
    assert r.status_code != 501


def test_corpus_upload_rejects_unsupported_types(client):
    r = client.post("/api/corpus/upload", files={"file": ("x.docx", b"x", "application/octet-stream")})
    assert r.status_code == 400


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Carrying Mechanic" in r.content

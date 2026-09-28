"""Instrument v0.5: distinctiveness is a search hypothesis at extraction and a logged retrieval
step afterwards. The model never assigns D0/D1/D2; the coder does, after reviewing the search log.

Before v0.5 the prompt's output skeleton printed "level":"D1" and every draft echoed it. These
tests pin the rule that replaced it, and that the nine pilot records made under v0.1-v0.4 (a level
plus a searchNote string) keep loading and keep producing the same numbers."""
import copy, json, re
import pytest
from conftest import record, legacy_record, requires_records, ALL_REVIEWED, ROOT


@pytest.fixture
def S():
    import backend.schema as S
    return S


@pytest.fixture
def M():
    import backend.model as M
    return M


@pytest.fixture
def E():
    import backend.evaluate as E
    return E


# ---- (a) a fresh draft never carries a level ---------------------------------

@pytest.mark.parametrize("level", ["D0", "D1", "D2"])
def test_fresh_draft_with_a_level_is_normalised_to_null_and_flagged(S, level):
    draft = record(); draft["distinctiveness"]["level"] = level
    before = copy.deepcopy(draft)
    body, warnings = S.normalise_draft(draft)
    assert body["distinctiveness"]["level"] is None
    assert any("distinctiveness.level" in w for w in warnings)
    assert draft == before, "the raw draft must stay verbatim (design rule 3)"
    assert "distinctiveness.level" in S.check(draft, "")["humanOnly"]


def test_fresh_draft_without_a_level_raises_no_warning(S):
    body, warnings = S.normalise_draft(record())
    assert body["distinctiveness"]["level"] is None and warnings == []


def test_normalisation_also_clears_a_model_set_verified(S):
    draft = record(); draft["distinctiveness"]["verified"] = True
    body, warnings = S.normalise_draft(draft)
    assert body["distinctiveness"]["verified"] is False and warnings


def test_normalisation_survives_a_draft_with_no_distinctiveness_section(S):
    draft = record(); del draft["distinctiveness"]
    body, _ = S.normalise_draft(draft)
    assert body["distinctiveness"]["level"] is None


def test_a_level_is_not_a_schema_fault_so_repair_does_not_fire_on_it(S):
    """It is a human-only violation, not a mechanical one; the repair pass reads `schema` only."""
    draft = record(); draft["distinctiveness"]["level"] = "D1"
    assert S.check(draft, "")["schema"] == []


def test_mock_provider_drafts_carry_no_level(M, S):
    d = M.extract_mock("Oyun", "The players throw stones onto a board and the winner takes all the pieces.")
    assert d["distinctiveness"]["level"] is None
    assert "searchNote" not in d["distinctiveness"] and d["distinctiveness"]["searchQueries"]
    assert S.check(d, "")["humanOnly"] == []


def test_constrained_decoding_schema_cannot_emit_a_level(M):
    props = M.draft_schema()["properties"]["distinctiveness"]["properties"]
    assert props["level"] == {"type": "null"}
    assert "verified" not in props and "searchLog" not in props and "counterpart" not in props
    assert "searchQueries" in props


# ---- (b) legacy records keep loading -----------------------------------------

def test_legacy_record_validates_unchanged(S):
    r = legacy_record(); before = copy.deepcopy(r)
    d = S.Draft.model_validate(r)
    assert r == before
    assert d.distinctiveness.level == "D1"
    assert d.distinctiveness.searchNote.startswith("Ludii")
    assert d.distinctiveness.searchQueries == [] and d.distinctiveness.verified is False
    assert S.check(r, "")["schema"] == []


def test_level_accepts_null_and_the_three_levels_only(S):
    for ok in (None, "D0", "D1", "D2"):
        r = record(); r["distinctiveness"]["level"] = ok
        assert S.check(r, "")["schema"] == [], ok
    r = record(); r["distinctiveness"]["level"] = "D3"
    assert any("distinctiveness.level" in x for x in S.check(r, "")["schema"])


def test_a_null_counterpart_and_a_null_search_note_validate(S):
    r = record(); r["distinctiveness"]["counterpart"] = None; r["distinctiveness"]["searchNote"] = None
    assert S.check(r, "")["schema"] == []


def test_the_shipped_pilot_pool_still_validates(S):
    pool = ROOT / "analysis" / "pool.json"
    requires_records(pool)
    recs = json.loads(pool.read_text(encoding="utf-8"))
    assert recs
    for r in recs:
        body = {k: v for k, v in r.items() if k not in ("id", "review", "review2")}
        errs = [e for e in S.check(body, "")["schema"] if e.startswith("distinctiveness")]
        assert errs == [], (r.get("name"), errs)
        assert r["distinctiveness"]["level"] in ("D0", "D1", "D2")


def test_the_nine_reference_drafts_still_validate(S, drafts):
    for d in drafts:
        assert [e for e in S.check(d, "")["schema"] if e.startswith("distinctiveness")] == [], d["name"]


# ---- (c) resolvable identifiers ----------------------------------------------

@pytest.mark.parametrize("url,id_type,ident", [
    ("https://ludii.games/details.php?keyword=Mangala", "ludii", "Mangala"),
    ("https://www.ludii.games/details.php?keyword=Nine%20Men%27s%20Morris", "ludii", "Nine Men's Morris"),
    ("https://boardgamegeek.com/boardgame/2397/backgammon", "bgg", "2397"),
    ("https://www.boardgamegeek.com/boardgame/171", "bgg", "171"),
    ("https://en.wikipedia.org/wiki/Buzkashi", "wikipedia", "en:Buzkashi"),
    ("https://tr.wikipedia.org/wiki/K%C3%B6kb%C3%B6r%C3%BC", "wikipedia", "tr:Kökbörü"),
    ("https://en.m.wikipedia.org/wiki/Leapfrog", "wikipedia", "en:Leapfrog"),
])
def test_identifier_accepts_ludii_bgg_and_wikipedia(S, url, id_type, ident):
    got = S.resolvable_identifier(url)
    assert got == {"idType": id_type, "id": ident, "url": url}


@pytest.mark.parametrize("url", [
    "", "   ", None,
    "https://traditionalgames.blog/2019/03/kokboru-rules/",          # a blog
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",                   # a video
    "https://boardgamegeek.com/thread/12345/some-discussion",        # BGG, but not a game
    "https://boardgamegeek.com/boardgame/backgammon",                # no numeric id
    "https://ludii.games/library.php",                               # Ludii, but no game
    "https://en.wikipedia.org/wiki/Special:Search?search=buzkashi",  # a search page
    "https://en.wikipedia.org/w/index.php?title=Buzkashi",           # not the /wiki/<title> form
    "https://en.wikipedia.org.example.com/wiki/Buzkashi",            # look-alike host
    "https://notwikipedia.org/wiki/Buzkashi",
    "ftp://en.wikipedia.org/wiki/Buzkashi",
    "Buzkashi",
])
def test_identifier_rejects_everything_else(S, url):
    assert S.resolvable_identifier(url) is None


# ---- (d) the retrieval step ---------------------------------------------------

HITS = [
    {"title": "Kokboru rules, a fan blog", "url": "https://traditionalgames.blog/kokboru", "snippet": "..."},
    {"title": "Buzkashi - Wikipedia", "url": "https://en.wikipedia.org/wiki/Buzkashi", "snippet": "..."},
    {"title": "A video", "url": "https://www.youtube.com/watch?v=abc", "snippet": "..."},
]
LOG_FIELDS = {"date", "languages", "sources", "queries", "candidatesInspected", "closestMatch", "separatingRule"}


def test_candidate_without_an_identifier_yields_a_null_counterpart(M):
    raw = {"closestMatch": "Buzkashi", "separatingRule": "", "counterpart": 0,
           "sources": [{"i": 0, "whatItShows": "rules"}], "reasoning": "r"}
    out = M.build_search_result("Kökbörü", "Buzkashi", "", ["q"], HITS, raw, "test")
    assert out["counterpart"] is None
    assert out["searchLog"]["candidatesInspected"][0]["identifier"] is None
    assert "no counterpart is retained" in out["reasoning"]


def test_candidate_with_an_identifier_is_retained(M):
    raw = {"closestMatch": "Buzkashi", "separatingRule": "goal shape", "counterpart": 1,
           "sources": [{"i": 0, "whatItShows": "a"}, {"i": 1, "whatItShows": "b"}]}
    out = M.build_search_result("Kökbörü", "Buzkashi", "", ["q1", "q2"], HITS, raw, "test", date="2026-09-27")
    assert out["counterpart"] == {"idType": "wikipedia", "id": "en:Buzkashi",
                                  "url": "https://en.wikipedia.org/wiki/Buzkashi"}
    log = out["searchLog"]
    assert set(log) == LOG_FIELDS
    assert log["date"] == "2026-09-27" and log["queries"] == ["q1", "q2"]
    assert log["closestMatch"] == "Buzkashi" and log["separatingRule"] == "goal shape"
    assert log["languages"] and "en.wikipedia.org" in log["sources"]
    assert [c["url"] for c in log["candidatesInspected"]] == [HITS[0]["url"], HITS[1]["url"]]


def test_the_retrieval_step_never_returns_a_level(M):
    """Even when the model volunteers one."""
    raw = {"level": "D0", "verified": True, "counterpart": 1, "sources": [{"i": 1}]}
    out = M.build_search_result("X", "", "", ["q"], HITS, raw, "test")
    assert out["level"] is None and out["verified"] is False
    assert "level" not in out["searchLog"]


@pytest.mark.parametrize("pick", [None, 99, -1, "abc", True, {"i": 99}, "https://en.wikipedia.org/wiki/Invented"])
def test_selection_is_by_index_only(M, pick):
    """An index that was not handed out, or a URL the model wrote itself, retains nothing."""
    raw = {"counterpart": pick, "sources": [{"i": 7, "whatItShows": "x"}, {"i": "zz"},
                                            {"url": "https://en.wikipedia.org/wiki/Invented"}]}
    out = M.build_search_result("X", "", "", ["q"], HITS, raw, "test")
    assert out["counterpart"] is None
    assert out["sources"] == [] and out["searchLog"]["candidatesInspected"] == []


def test_every_url_in_the_result_came_from_the_search_engine(M):
    raw = {"counterpart": 1, "sources": [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 5}]}
    out = M.build_search_result("X", "", "", ["q"], HITS, raw, "test")
    handed_out = {h["url"] for h in HITS}
    urls = {c["url"] for c in out["searchLog"]["candidatesInspected"]} | {s["url"] for s in out["sources"]}
    assert urls <= handed_out and out["counterpart"]["url"] in handed_out


def test_no_results_means_no_counterpart_and_no_level(M):
    out = M.build_search_result("X", "Y", "z", ["q"], [], None, "test")
    assert out["level"] is None and out["counterpart"] is None
    assert set(out["searchLog"]) == LOG_FIELDS and out["searchLog"]["candidatesInspected"] == []
    assert "Nothing was inferred" in out["reasoning"]


def test_the_draft_hypothesis_supplies_query_terms(M):
    qs = M.search_queries("Kökbörü", "Buzkashi", ["kok boru", "  ", "Kok Boru", "ulak tartysh"])
    assert "kok boru" in qs and "ulak tartysh" in qs and "  " not in qs
    assert len([q for q in qs if q.casefold() == "kok boru"]) == 1


def test_the_search_prompt_asks_for_no_level(M):
    p = M.search_prompt("X", "Y", "", HITS)
    assert '"level"' not in p and "D0|D1|D2" not in p
    assert "Do NOT assign a distinctiveness level" in p and "[1] Buzkashi - Wikipedia" in p


def test_search_local_end_to_end_without_network(M, monkeypatch):
    monkeypatch.setattr(M, "ddg", lambda q, limit=6: list(HITS))
    monkeypatch.setattr(M, "_chat_local", lambda *a, **k: json.dumps(
        {"level": "D1", "closestMatch": "Buzkashi", "counterpart": 2, "sources": [{"i": 2, "whatItShows": "v"}]}))
    out = M.search_local("Kökbörü", "Buzkashi", "", ["kok boru"])
    assert out["level"] is None and out["counterpart"] is None      # a YouTube link resolves to nothing
    assert "kok boru" in out["searchLog"]["queries"]


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def test_api_search_returns_log_and_counterpart_but_never_a_level(client, monkeypatch):
    import backend.model as M
    monkeypatch.setattr(M, "ddg", lambda q, limit=6: list(HITS))
    monkeypatch.setattr(M, "_chat_local", lambda *a, **k: json.dumps(
        {"level": "D0", "closestMatch": "Buzkashi", "counterpart": 1, "sources": [{"i": 1, "whatItShows": "w"}]}))
    r = client.post("/api/search", json={"name": "Kökbörü", "closest": "Buzkashi", "separatingRule": "",
                                         "searchQueries": ["kok boru"]})
    assert r.status_code == 200
    j = r.json()
    assert j["level"] is None and j["verified"] is False
    assert j["counterpart"]["idType"] == "wikipedia"
    assert set(j["searchLog"]) == LOG_FIELDS


def test_api_search_with_no_results_returns_a_null_counterpart(client, monkeypatch):
    import backend.model as M
    monkeypatch.setattr(M, "ddg", lambda q, limit=6: [])
    j = client.post("/api/search", json={"name": "zzz"}).json()
    assert j["level"] is None and j["counterpart"] is None and j["searchLog"]["candidatesInspected"] == []


def test_api_extract_draft_has_no_level(client, corpus):
    j = client.post("/api/extract", json={"name": corpus[0]["name"], "entry": corpus[0]["entry"]}).json()
    assert j["draft"]["distinctiveness"]["level"] is None
    assert "distinctiveness.level" not in j["flags"]["humanOnly"]


def test_api_extract_flags_a_model_assigned_level_and_stores_it_verbatim(client, corpus, db, monkeypatch):
    import sqlite3, backend.model as M
    real = M.extract_mock
    def with_level(name, entry, tier="default"):
        d = real(name, entry, tier); d["distinctiveness"]["level"] = "D1"; return d
    monkeypatch.setattr(M, "extract_mock", with_level)
    j = client.post("/api/extract", json={"name": "X", "entry": corpus[0]["entry"]}).json()
    assert "distinctiveness.level" in j["flags"]["humanOnly"]
    stored = json.loads(sqlite3.connect(db).execute("SELECT draft FROM extractions").fetchone()[0])
    assert stored["distinctiveness"]["level"] == "D1", "the extractions table keeps the output verbatim"


def test_export_csv_with_an_unset_level(client):
    r = record(name="Unset"); r["review"] = {"sections": ALL_REVIEWED, "raw": record(name="Unset")}
    client.put("/api/records/unset", json=r)
    client.put("/api/records/legacy", json=legacy_record(name="Legacy"))
    res = client.get("/api/export.csv")
    assert res.status_code == 200
    import csv, io
    rows = {x["name"]: x for x in csv.DictReader(io.StringIO(res.text))}
    assert rows["Unset"]["D_adjudicated"] == "" and rows["Unset"]["D_draft"] == ""
    assert rows["Legacy"]["D_adjudicated"] == "D1"
    assert client.get("/api/eval").status_code == 200


# ---- (e) the prompt -----------------------------------------------------------

def test_prompt_prints_level_null_and_not_d1(M):
    instr = (ROOT / "backend" / "instrument.md").read_text(encoding="utf-8")
    for text in (instr, M.INSTRUMENT, M.build_prompt("G", "E")):
        assert '"level":"D1"' not in text
        assert '"distinctiveness":{"level":null,' in text
        assert "searchNote" not in text
        assert 'do NOT assign D0, D1 or D2; leave "level" null' in text
        assert '"searchQueries"' in text


def test_prompt_carries_the_v05_distinctiveness_skeleton_verbatim():
    instr = (ROOT / "backend" / "instrument.md").read_text(encoding="utf-8")
    assert ('"distinctiveness":{"level":null,"closest":"","separatingRule":"","searchQueries":[""],'
            '"counterpart":{"idType":"","id":"","url":""},"searchLog":{"date":"","languages":[],"sources":[],'
            '"queries":[],"candidatesInspected":[],"closestMatch":"","separatingRule":""},"verified":false}') in instr


def test_the_skeleton_in_the_prompt_is_a_valid_v05_draft(S):
    instr = (ROOT / "backend" / "instrument.md").read_text(encoding="utf-8")
    skeleton = json.loads(instr[instr.index('{"name":""'):])
    flags = S.check(skeleton, "")
    assert flags["schema"] == [] and flags["humanOnly"] == []


def test_frontend_inline_prompt_has_the_same_rule():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    inline = re.search(r"const INSTRUMENT = `(.*?)`;", html, re.S).group(1)
    assert '"level":"D1"' not in inline and '"distinctiveness":{"level":null,' in inline


def test_frontend_no_longer_defaults_a_missing_level_to_d1():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'level:"D1"' not in html
    assert re.search(r"r\.distinctiveness=Object\.assign\(\{level:null,", html)
    assert '<option value="" ' in html and ">not set</option>" in html
    assert "D unset" in html


def test_version_is_v05_and_the_v04_prompt_is_kept():
    assert (ROOT / "backend" / "instrument_version.txt").read_text().strip() == "v0.5-2026-09-27"
    # the prompt the pilot ran under, kept as a tracked file: PIPELINE's Listing 2 prints it
    bak = (ROOT / "backend" / "instrument_v0.4.md").read_text(encoding="utf-8")
    assert '"distinctiveness":{"level":"D1",' in bak and "searchNote" in bak


def test_the_method_note_is_documentation_not_prompt():
    notes = (ROOT / "backend" / "instrument_method_notes.md").read_text(encoding="utf-8")
    instr = (ROOT / "backend" / "instrument.md").read_text(encoding="utf-8")
    assert "Distinctiveness retrieval (separate, logged step; instrument v0.5)." in notes
    assert "The coder is never asked to run the search." in notes
    assert "Distinctiveness retrieval (separate, logged step" not in instr


# ---- (f) evaluation over a mixed pool -----------------------------------------

def _reviewed(r, raw):
    r["review"] = {"sections": dict(ALL_REVIEWED), "raw": raw}
    return r


def test_evaluate_handles_null_and_legacy_levels_together(E):
    a = _reviewed(record(name="New, unset"), record(name="New, unset"))
    b = _reviewed(record(name="New, coded"), record(name="New, coded")); b["distinctiveness"]["level"] = "D2"
    c = _reviewed(legacy_record(name="Legacy"), legacy_record(name="Legacy"))
    d = _reviewed(record(name="Second coded"), record(name="Second coded"))
    d["review2"] = {"coder": "MT", "D": "D1", "route": "R1"}
    out = E.evaluate([a, b, c, d])
    json.dumps(out)                                       # and it serialises
    assert out["summary"]["reviewed"] == 4
    assert out["summary"]["D"] == {"unset": 2, "D2": 1, "D1": 1}
    assert out["accuracy"]["D"]["n"] == 4 and out["accuracy"]["D"]["agree"] == 3
    assert out["reliability"]["D"]["n"] == 1


def test_evaluate_handles_a_draft_that_omits_the_level_key(E):
    raw = record(); del raw["distinctiveness"]["level"]
    r = _reviewed(record(), raw)
    out = E.evaluate([r])
    assert out["accuracy"]["D"]["n"] == 1 and out["accuracy"]["route"]["n"] == 1


@pytest.mark.parametrize("level", [None, "D0", "D1", "D2"])
def test_distinctiveness_never_enters_eligibility(E, level):
    r = record(); r["distinctiveness"]["level"] = level
    r["review"] = {"sections": dict(ALL_REVIEWED)}
    base = record(); base["review"] = {"sections": dict(ALL_REVIEWED)}
    assert E.eligible(r) == E.eligible(base)


def test_legacy_pool_accuracy_total_is_unchanged(E):
    """Table 4: 61/99 with D excluded, on the records as stored."""
    pool = ROOT / "analysis" / "pool.json"
    requires_records(pool)
    acc = E.evaluate(json.loads(pool.read_text(encoding="utf-8")))["accuracy"]
    agree = sum(v["agree"] for f, v in acc.items() if f != "D")
    n = sum(v["n"] for f, v in acc.items() if f != "D")
    assert (agree, n) == (61, 99)


# ---- the places a level is read ------------------------------------------------

def test_exemplars_do_not_show_the_model_a_level():
    from backend.exemplars import _compact, block
    r = legacy_record(); r["distinctiveness"]["level"] = "D0"
    r["review"] = {"sections": dict(ALL_REVIEWED), "entry": "e"}
    assert "level" not in _compact(r)["distinctiveness"]
    assert '"D0"' not in block([r])


def test_coding_sheet_says_the_coder_assigns_the_level():
    import backend.coding_sheet as cs
    hint = cs.D["distinctiveness.level"][3]
    assert "Leave the model's value" not in hint
    assert "after reviewing the search log" in hint and "Blank is allowed" in hint
    assert cs.parse_value("distinctiveness.level", "") == (None, None)


def test_coding_sheet_exports_an_unset_level_as_blank(db, tmp_path):
    import csv, time
    import backend.coding_sheet as cs
    from backend.app import conn
    r = record(name="Unset"); r["id"] = "unset"
    r["review"] = {"coder": "", "sections": {}, "raw": record(name="Unset"), "entry": "entry"}
    c = conn()
    c.execute("INSERT INTO records(id,json,updated) VALUES(?,?,?)", ("unset", json.dumps(r), time.time()))
    c.commit(); c.close()
    out = tmp_path / "pack"; cs.export(db, out)
    rows = list(csv.DictReader((out / "coding_sheet.csv").open(encoding="utf-8-sig")))
    row = next(x for x in rows if x["field"] == "distinctiveness.level")
    assert row["model_value"] == "" and row["YOUR_VALUE"] == ""
    # returned blank: the level stays unset, nothing is invented
    cs.import_sheet(out / "coding_sheet.csv", db, "C1")
    import sqlite3
    rec = json.loads(sqlite3.connect(db).execute("SELECT json FROM records").fetchone()[0])
    assert rec["distinctiveness"]["level"] is None

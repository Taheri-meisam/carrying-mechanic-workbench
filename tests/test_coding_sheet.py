"""The offline coding pack. The round trip must preserve review.raw and must not mark a section
reviewed on the strength of a pre-filled cell."""
import json, sqlite3
import pytest
from conftest import record, ALL_REVIEWED


@pytest.fixture
def cs(db):
    import backend.coding_sheet as cs
    return cs


@pytest.fixture
def pool(db, drafts):
    import sqlite3, time, json as j
    from backend.app import conn, slug
    c = conn()
    for d in drafts:
        r = dict(d); r["id"] = slug(r["name"])
        r["review"] = {"coder": "", "sections": {}, "raw": dict(d), "entry": "entry text here"}
        c.execute("INSERT OR REPLACE INTO records(id,json,updated) VALUES(?,?,?)",
                  (r["id"], j.dumps(r, ensure_ascii=False), time.time()))
    c.commit(); c.close()
    return db


# ---- value parsing tolerance ------------------------------------------------

@pytest.mark.parametrize("field,raw,want", [
    ("gates.G1.pass", "TRUE", True), ("gates.G1.pass", "evet", True),
    ("gates.G1.pass", "no", False), ("gates.G1.pass", " Hayır ", False),
    ("dimensions.MC.level", " 2 ", 2),
    ("dig.S.op", "kept", "Kept"), ("dig.S.op", "SUBSTITUTED", "Substituted"),
    ("route.primary", "r2", "R2"),
    ("distinctiveness.level", "d1", "D1"),
    ("gates.G3.classes", "s, m", ["S", "M"]),
    ("review.sections.gates", "REVIEWED", True),
])
def test_parse_accepts_what_a_human_types(cs, field, raw, want):
    got, err = cs.parse_value(field, raw)
    assert err is None and got == want


@pytest.mark.parametrize("field,raw", [
    ("dimensions.MC.level", "banana"), ("dig.S.op", "sort of kept"),
    ("route.primary", "R9"), ("distinctiveness.level", "D7"),
    ("gates.G3.classes", "S, Q"),
])
def test_parse_reports_rather_than_guesses(cs, field, raw):
    got, err = cs.parse_value(field, raw)
    assert got is None and err, f"{field}={raw!r} should have been reported"


def test_d_level_is_not_parsed_as_a_number(cs):
    """distinctiveness.level ends in 'level' but takes D0/D1/D2, not 0/1/2."""
    assert cs.parse_value("distinctiveness.level", "D1") == ("D1", None)


def test_blank_is_not_an_error(cs):
    assert cs.parse_value("dimensions.MC.level", "  ") == (None, None)


# ---- export -----------------------------------------------------------------

def test_export_prefills_coder1_and_adds_section_ticks(cs, pool, tmp_path):
    out = tmp_path / "pack"; cs.export(pool, out)
    rows = list(__import__("csv").DictReader((out / "coding_sheet.csv").open(encoding="utf-8-sig")))
    ticks = [r for r in rows if r["field"].startswith("review.sections.")]
    assert len(ticks) == 7 * 9
    assert any(r["YOUR_VALUE"] for r in rows), "coder 1's sheet should be pre-filled"
    assert (out / "instrument.md").exists() and (out / "entries").is_dir()


def test_blind_export_withholds_the_draft(cs, pool, tmp_path):
    out = tmp_path / "blind"; cs.export(pool, out, blind=True)
    rows = list(__import__("csv").DictReader((out / "coding_sheet.csv").open(encoding="utf-8-sig")))
    assert not any(k.startswith("model") or "quotation" in k for k in rows[0]), rows[0].keys()
    assert not any(r["YOUR_VALUE"] for r in rows), "the blind sheet must start empty"
    assert len(rows) == 7 * 9, "blind sheet is the seven kappa criteria only"


def test_only_restricts_the_games(cs, pool, tmp_path):
    out = tmp_path / "five"; cs.export(pool, out, only=["Akdört", "Kökbörü"])
    rows = list(__import__("csv").DictReader((out / "coding_sheet.csv").open(encoding="utf-8-sig")))
    assert len({r["game"] for r in rows}) == 2


# ---- import -----------------------------------------------------------------

def _fill(path, game, field_values, ticks=()):
    import csv
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for r in rows:
        if r["game"] != game: continue
        if r["field"] in field_values: r["YOUR_VALUE"] = field_values[r["field"]]
        if r["field"] in {f"review.sections.{t}" for t in ticks}: r["YOUR_VALUE"] = "REVIEWED"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def test_import_applies_edits_and_preserves_the_raw_draft(cs, pool, tmp_path, drafts):
    out = tmp_path / "p"; cs.export(pool, out)
    game = drafts[0]["name"]
    _fill(out / "coding_sheet.csv", game, {"dimensions.MC.level": "0"}, ticks=["dimensions"])
    cs.import_sheet(out / "coding_sheet.csv", pool, "C1")
    rec = next(json.loads(j) for (j,) in sqlite3.connect(pool).execute("SELECT json FROM records")
               if json.loads(j)["name"] == game)
    assert rec["dimensions"]["MC"]["level"] == 0
    assert rec["review"]["raw"]["dimensions"]["MC"]["level"] == drafts[0]["dimensions"]["MC"]["level"]
    assert rec["review"]["coder"] == "C1"


def test_prefilled_values_do_not_mark_a_section_reviewed(cs, pool, tmp_path, drafts):
    """The tick is the gate. Without it a pre-filled sheet would auto-review everything."""
    out = tmp_path / "p"; cs.export(pool, out)
    game = drafts[0]["name"]
    _fill(out / "coding_sheet.csv", game, {}, ticks=["gates"])
    cs.import_sheet(out / "coding_sheet.csv", pool, "C1")
    rec = next(json.loads(j) for (j,) in sqlite3.connect(pool).execute("SELECT json FROM records")
               if json.loads(j)["name"] == game)
    assert rec["review"]["sections"] == {"gates": True}


def test_second_coder_writes_only_review2(cs, pool, tmp_path, drafts):
    out = tmp_path / "b"; cs.export(pool, out, blind=True)
    game = drafts[0]["name"]
    _fill(out / "coding_sheet.csv", game, {"dig.S.op": "Removed"})
    cs.import_sheet(out / "coding_sheet.csv", pool, "MT", second=True)
    rec = next(json.loads(j) for (j,) in sqlite3.connect(pool).execute("SELECT json FROM records")
               if json.loads(j)["name"] == game)
    assert rec["review2"]["S"] == "Removed" and rec["review2"]["coder"] == "MT"
    assert rec["dig"]["S"]["op"] == drafts[0]["dig"]["S"]["op"], "coder 1's record was modified"
    assert rec["review"]["sections"] == {}


def test_semicolon_csv_from_turkish_excel_is_read(cs, pool, tmp_path):
    """Excel in tr-TR writes ';' as the list separator."""
    out = tmp_path / "p"; cs.export(pool, out)
    import csv
    src = out / "coding_sheet.csv"
    rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
    with src.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader(); w.writerows(rows)
    assert len(cs.read_sheet(src)) == len(rows)

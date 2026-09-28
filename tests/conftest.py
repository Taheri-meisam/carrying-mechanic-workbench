"""Shared fixtures. Every test runs against a temporary database: nothing here may touch
data/workbench.sqlite, which holds the real pool."""
import json, os, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh empty workbench database, wired in before backend.app is imported."""
    p = tmp_path / "test.sqlite"
    monkeypatch.setenv("WORKBENCH_DB", str(p))
    for m in [k for k in list(sys.modules) if k.startswith("backend")]:
        del sys.modules[m]
    return p


@pytest.fixture
def corpus():
    """The nine pilot entries where they are present, otherwise a synthetic stand-in.

    The real entries are the Ministry's and are not distributed, so a released checkout has only
    the synthetic file. Both have the same shape and these tests do not depend on the content.
    """
    real = ROOT / "examples" / "corpus_nine_games.json"
    path = real if real.exists() else ROOT / "examples" / "corpus_sample_synthetic.json"
    return json.loads(path.read_text(encoding="utf-8"))


def requires_records(*paths):
    """Skip a test that needs the coded records, which a released checkout does not have."""
    missing = [p for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"coded records not present ({', '.join(p.name for p in missing)}); "
                    "see Data availability")


@pytest.fixture
def drafts():
    """The nine pilot drafts. They quote the source entries, so they are kept with the coded records
    and are absent from a public checkout; tests that need them are skipped there."""
    src = ROOT / "examples" / "nine_games_model_drafts.json"
    if not src.exists():
        pytest.skip("pilot drafts not present (nine_games_model_drafts.json); see Data availability")
    return json.loads(src.read_text(encoding="utf-8"))


def record(**over):
    """A minimal schema-complete record; override any field to build a case."""
    r = {
        "name": "Test Game", "altNames": "",
        "source": {"region": "", "players": "", "session": ""},
        "carryingMechanic": {"text": "t", "evidence": "e"},
        "gates": {"G1": {"pass": True, "reason": "", "evidence": ""},
                  "G2": {"pass": True, "reason": "", "evidence": ""},
                  "G3": {"pass": True, "reason": "", "classes": ["S"], "evidence": ""}},
        "route": {"primary": "R1", "secondary": "", "test": ""},
        "dimensions": {"MC": {"level": 2, "descriptor": "", "evidence": ""},
                       "LOM": {"level": 1, "objective": "", "counterfactual": "", "evidence": ""},
                       "AGE": {"level": 1, "competence": "", "parameter": "", "evidence": ""},
                       "CTa": {"level": 2, "evidence": ""},
                       "CTb": {"level": 1, "note": ""}, "CTc": {"level": 1, "note": ""}},
        "dig": {"S": {"op": "Kept", "note": "", "substitutionTest": {"pass": None, "reason": ""}},
                "M": {"op": "Kept", "note": ""}, "L": {"op": "Kept", "note": ""}},
        # instrument v0.5: a draft is a search hypothesis and carries no D level
        "ledger": [], "distinctiveness": {
            "level": None, "closest": "", "separatingRule": "", "searchQueries": [],
            "counterpart": {"idType": "", "id": "", "url": ""},
            "searchLog": {"date": "", "languages": [], "sources": [], "queries": [],
                          "candidatesInspected": [], "closestMatch": "", "separatingRule": ""},
            "verified": False},
        "ambiguities": [], "confidence": {"overall": 0.5, "low": []},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = {**r[k], **v}
        else:
            r[k] = v
    return r


def legacy_record(**over):
    """The v0.1-v0.4 shape the nine pilot records were stored in: a model-assigned level and a
    free-text searchNote, none of the v0.5 fields. These must keep loading unchanged."""
    r = record(**over)
    if "distinctiveness" not in over:
        r["distinctiveness"] = {"level": "D1", "closest": "Buzkashi (Afghanistan)", "separatingRule": "",
                                "searchNote": "Ludii Games Database, BoardGameGeek, Parlett, Wikipedia EN/TR",
                                "verified": False}
    return r


ALL_REVIEWED = {s: True for s in
                ["mechanic", "gates", "route", "dimensions", "dig", "ledger", "distinct"]}

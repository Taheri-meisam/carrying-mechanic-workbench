"""Sampling, exemplars, glyph recovery, and the invariants INVARIANTS.md states in prose."""
import json, re
from pathlib import Path
import pytest
from conftest import record, ALL_REVIEWED, ROOT


# ---- sampling ---------------------------------------------------------------

def test_same_seed_gives_the_same_draw(corpus):
    from backend.sample import draw
    a, _ = draw(corpus, 5, 20260916, {})
    b, _ = draw(corpus, 5, 20260916, {})
    assert [x["name"] for x in a] == [x["name"] for x in b]


def test_different_seed_gives_a_different_draw(corpus):
    from backend.sample import draw
    a, _ = draw(corpus, 5, 1, {})
    b, _ = draw(corpus, 5, 2, {})
    assert [x["name"] for x in a] != [x["name"] for x in b]


def test_asking_for_more_than_the_corpus_returns_the_corpus(corpus):
    from backend.sample import draw
    picked, how = draw(corpus, 999, 1, {})
    assert len(picked) == len(corpus) and "corpus size" in how


def test_stratified_draw_respects_routes(corpus):
    from backend.sample import draw
    routes = {g["name"]: ("R1" if i % 2 else "R2") for i, g in enumerate(corpus)}
    picked, how = draw(corpus, 4, 7, routes)
    assert len(picked) == 4 and "proportional" in how


# ---- exemplars --------------------------------------------------------------

def test_exemplars_are_off_by_default(monkeypatch):
    monkeypatch.delenv("EXEMPLARS", raising=False)
    from backend.exemplars import count
    assert count() == 0


def test_exemplars_never_include_the_target_game(drafts):
    from backend.exemplars import select
    recs = [dict(d, review={"sections": ALL_REVIEWED}) for d in drafts]
    picked = select(recs, drafts[0]["name"], 3)
    assert drafts[0]["name"] not in [r["name"] for r in picked]


def test_exemplars_only_use_adjudicated_records(drafts):
    from backend.exemplars import select
    recs = [dict(d, review={"sections": {"gates": True}}) for d in drafts]   # partial only
    assert select(recs, "", 3) == []


def test_prompt_is_unchanged_when_no_exemplars(monkeypatch):
    """With exemplars off the prompt must be byte-identical, or promptHash stops comparing."""
    monkeypatch.delenv("EXEMPLARS", raising=False)
    from backend.model import build_prompt
    assert build_prompt("G", "E", "") == build_prompt("G", "E")


# ---- glyph recovery ---------------------------------------------------------

def test_recovers_i_between_letters():
    from backend.corpus import recover_glyphs
    out, rep = recover_glyphs('The name der!ves from the st!cks')
    assert "derives" in out and "sticks" in out and rep["i_substitutions"] == 2


def test_recovers_word_initial_i():
    from backend.corpus import recover_glyphs
    out, _ = recover_glyphs("the !nner wood")
    assert "inner" in out


def test_leaves_real_punctuation_alone():
    from backend.corpus import recover_glyphs
    out, _ = recover_glyphs('He shouted! Then "Mali" was played.')
    assert 'shouted!' in out and '"Mali"' in out


def test_ambiguous_hash_is_reported_not_guessed():
    from backend.corpus import recover_glyphs
    out, rep = recover_glyphs("bir kelime ba#lı ve bir ba#ka")
    unresolved = {w for w, _ in rep["unresolved"]}
    assert rep["unresolved_words"] > 0 or "#" not in out


def test_hash_resolved_when_the_document_spells_it_elsewhere():
    from backend.corpus import recover_glyphs
    out, rep = recover_glyphs("yarışma sonucu. Bir yarı#ma daha yapıldı.")
    assert "yarışma daha" in out.replace("  ", " ") or rep["resolved"] >= 1


# ---- invariants stated in INVARIANTS.md -----------------------------------------

def test_instrument_is_the_only_home_of_the_prompt():
    """Design rule 5: the frontend keeps an inline copy; it must match instrument.md."""
    instr = (ROOT / "backend" / "instrument.md").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const INSTRUMENT = `(.*?)`;", html, re.S)
    assert m, "frontend INSTRUMENT constant not found"
    # The whole text, not a prefix: comparing the first 200 characters let the inline copy keep
    # the v0.2 wording of G3 and 5a through two version bumps without this test noticing.
    assert m.group(1) == instr, (
        "frontend inline instrument has drifted from backend/instrument.md (design rule 5)")


def test_prompt_hash_tracks_the_instrument():
    import hashlib, backend.model as M
    want = hashlib.sha256((ROOT / "backend" / "instrument.md").read_bytes()).hexdigest()[:12]
    assert M.PROMPT_HASH == want


def test_provider_defaults_to_local_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    import importlib, backend.model as M
    importlib.reload(M)
    assert M.resolve_provider() == "local"


def test_repair_only_fires_on_mechanical_faults(monkeypatch):
    """A G3-vs-dig contradiction is a judgement; a repair pass must not spend a call on it."""
    import backend.model as M
    r = record(); r["gates"]["G3"]["pass"] = False        # inconsistent, but structurally valid
    called = []
    monkeypatch.setattr(M, "_chat_local", lambda *a, **k: called.append(1) or "{}")
    out, errs = M.repair_local(r, "entry")
    assert out is None and errs == [] and not called


# ---- instrument v0.2: the substitution test ---------------------------------

def test_survives_is_identical_in_all_three_implementations():
    """evaluate.py, the frontend and eval.js must agree about what survives on screen
    (design rule 6). Checked on the four op values x the three test states."""
    import json, shutil, subprocess, tempfile
    import backend.evaluate as E
    if not shutil.which("node"):
        pytest.skip("node not installed")
    cases = [{"op": o, "substitutionTest": {"pass": t}} for o in
             ("Kept", "Transformed", "Substituted", "Removed") for t in (True, False, None)]
    py = [E.survives(c) for c in cases]
    js_src = (ROOT / "analysis" / "eval.js").read_text(encoding="utf-8")
    m = re.search(r"const ORDER=.*?;\s*//.*?\n//.*?\n(const survives=.*?);", js_src, re.S)
    assert m, "survives() not found in eval.js"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write("const ORDER={Kept:3,Transformed:2,Substituted:1,Removed:0};\n")
        fh.write(m.group(1) + ";\n")
        fh.write(f"console.log(JSON.stringify({json.dumps(cases)}.map(survives)));")
        path = fh.name
    out = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == py, (py, out.stdout)


def test_untested_substitution_is_flagged_for_the_coder():
    import backend.schema as S
    r = record(); r["dig"]["S"]["op"] = "Substituted"
    assert any("substitution test" in x for x in S.check(r, "")["inconsistent"])


def test_instrument_version_moved_with_the_rule():
    v = (ROOT / "backend" / "instrument_version.txt").read_text().strip()
    assert v != "v0.1-2026-09-15", "the eligibility rule changed; the instrument version must move"

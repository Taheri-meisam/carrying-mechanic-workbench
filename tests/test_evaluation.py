"""Cohen's kappa, the accuracy pairing, and the three-way agreement design rule 6 requires
between backend/evaluate.py, the frontend renderEval(), and analysis/eval.js."""
import json, re, subprocess
from pathlib import Path
import pytest
from conftest import record, ALL_REVIEWED, ROOT


@pytest.fixture
def E():
    import backend.evaluate as E
    return E


def test_kappa_perfect_agreement(E):
    assert E.kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0


def test_kappa_single_category_is_undefined(E):
    """All raters give the same label: pe == 1, so kappa divides by zero and is undefined.

    This test previously asserted 1.0, which is what the code did. That was wrong: it prints a
    perfect reliability score for a criterion on which nobody had to make a distinction. None
    renders as an em dash in the paper, the app and eval.js.
    """
    assert E.kappa(["a"] * 5, ["a"] * 5) is None


def test_kappa_chance_level_is_zero(E):
    a = ["x", "x", "y", "y"]; b = ["x", "y", "x", "y"]
    assert abs(E.kappa(a, b)) < 1e-9


def test_kappa_known_value(E):
    """Worked by hand: n=10, po=0.8, pe=0.5 -> (0.8-0.5)/(1-0.5) = 0.6"""
    a = list("aaaaabbbbb")
    b = list("aaaaabbbab".replace("x", "a"))
    a = ["a"] * 5 + ["b"] * 5
    b = ["a"] * 4 + ["b"] * 1 + ["b"] * 4 + ["a"] * 1
    k = E.kappa(a, b)
    assert abs(k - 0.6) < 1e-9, k


def test_kappa_empty_is_none(E):
    assert E.kappa([], []) is None


def test_accuracy_pairs_draft_against_adjudicated(E):
    """Accuracy compares review.raw with the adjudicated record, and counts only fully reviewed."""
    raw = record()
    adj = record(); adj["dimensions"]["MC"]["level"] = 1      # the coder disagreed
    adj["review"] = {"sections": ALL_REVIEWED, "raw": raw}
    out = E.evaluate([adj])
    assert out["accuracy"]["MC"]["n"] == 1
    assert out["accuracy"]["MC"]["agree"] == 0
    assert out["accuracy"]["route"]["agree"] == 1


def test_accuracy_ignores_unreviewed_records(E):
    r = record(); r["review"] = {"raw": record()}             # no sections ticked
    assert E.evaluate([r])["accuracy"]["MC"]["n"] == 0


def test_evaluate_on_the_nine_reference_drafts(E, drafts):
    """Import -> eval round trip: the nine shipped drafts evaluate without error."""
    out = E.evaluate(drafts)
    assert out["summary"]["n"] == 9
    assert set(out["accuracy"]) >= {"route", "MC", "LOM", "AGE", "CTa", "S", "M", "L", "D"}


# ---- design rule 6: the three implementations must agree -----------------------

def _js_fields():
    js = (ROOT / "analysis" / "eval.js").read_text(encoding="utf-8")
    m = re.search(r"const FIELDS=\{(.*?)\};", js, re.S)
    return {k for k in re.findall(r"(\w+):\s*r=>", m.group(1))}


def _frontend_fields():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const EVF\s*=\s*\{(.*?)\};", html, re.S)
    if not m:
        pytest.skip("frontend EVF table not found under that name")
    return {k for k in re.findall(r"(\w+):\s*r=>", m.group(1))}


def test_eval_js_scores_the_same_fields_as_evaluate_py(E):
    assert _js_fields() == set(E.F), (
        "eval.js and backend/evaluate.py disagree about which fields are scored "
        "(design rule 6)")


def test_frontend_scores_the_same_fields_as_evaluate_py(E):
    assert _frontend_fields() == set(E.F), (
        "frontend renderEval() and backend/evaluate.py disagree about which fields are scored "
        "(design rule 6)")


def test_eval_js_reproduces_the_papers_accuracy_table(E):
    """eval.js and backend/evaluate.py must agree field for field on the released pool.

    This is the reproduction path a reader of the PIPELINE paper follows, so it is checked against
    the same pool.json that ships with the paper rather than against a fixture. It previously ran on
    dryrun_pool.json, whose `human` block barely differs from the draft; that made eval.js print
    1.00 accuracy where the paper reports 0.62, and the test passed anyway.
    """
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    d = ROOT / "analysis"
    from conftest import requires_records
    requires_records(d / "pool.json")
    out = subprocess.run(["node", "eval.js", "pool.json"], cwd=d,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    sec1 = out.stdout.split("Table: rubric reliability")[0]
    got = dict(re.findall(r"^(\w+)\s+(\d+/\d+)", sec1, re.M))

    pool = json.loads((d / "pool.json").read_text(encoding="utf-8"))
    py = E.evaluate(pool)["accuracy"]
    assert py, "no fully reviewed records in pool.json"
    for field, v in py.items():
        assert got.get(field) == f"{v['agree']}/{v['n']}", (field, got.get(field), v)

    # and the total the paper prints in Table 1
    assert got["TOTAL"] == "61/99", got["TOTAL"]


def test_kappa_undefined_boundary(E):
    """One dissenting judgement is enough to make kappa defined again."""
    assert E.kappa(["Kept"] * 9, ["Kept"] * 9) is None
    assert E.kappa(["Kept"] * 8 + ["Removed"], ["Kept"] * 9) == 0.0
    assert E.kappa(["A", "A", "B", "B"], ["A", "A", "B", "B"]) == 1.0

"""The eligibility rule is the instrument's decision procedure. INVARIANTS.md fixes it and says it may
not change without the researchers. These tests are what makes an accidental change loud."""
import pytest
from conftest import record, ALL_REVIEWED


@pytest.fixture
def ev():
    from backend.evaluate import eligible
    return eligible


def reviewed(r):
    r = dict(r); r["review"] = {"sections": ALL_REVIEWED}; return r


def test_clean_record_is_eligible(ev):
    e = ev(reviewed(record()))
    assert e["ok"] and e["fails"] == [] and not e["hybrid"]


def test_unreviewed_record_is_never_eligible(ev):
    """No record counts until all seven sections are reviewed (design rule 2)."""
    e = ev(record())
    assert not e["ok"] and e["fails"] == []      # nothing failed; it is simply not reviewed


@pytest.mark.parametrize("gate", ["G1", "G2", "G3"])
def test_any_failed_gate_rejects(ev, gate):
    r = record(); r["gates"][gate]["pass"] = False
    e = ev(reviewed(r))
    assert not e["ok"] and "gate" in e["fails"]


def test_dig_s_substituted_without_a_test_fails(ev):
    """Instrument v0.2: substituted is conditional, and an untested substitution does not pass."""
    r = record(); r["dig"]["S"]["op"] = "Substituted"
    assert "DIG(S)" in ev(reviewed(r))["fails"]


def test_dig_s_substituted_passing_the_test_is_eligible(ev):
    r = record(); r["dig"]["S"]["op"] = "Substituted"
    r["dig"]["S"]["substitutionTest"] = {"pass": True, "reason": "competence transfers"}
    assert "DIG(S)" not in ev(reviewed(r))["fails"]


def test_dig_s_substituted_failing_the_test_rejects(ev):
    r = record(); r["dig"]["S"]["op"] = "Substituted"
    r["dig"]["S"]["substitutionTest"] = {"pass": False, "reason": "does not transfer"}
    assert "DIG(S)" in ev(reviewed(r))["fails"]


def test_removed_is_not_rescuable_by_a_test(ev):
    """Removed fails outright; the test only ever applies to Substituted."""
    r = record(); r["dig"]["S"]["op"] = "Removed"
    r["dig"]["S"]["substitutionTest"] = {"pass": True, "reason": "irrelevant"}
    assert "DIG(S)" in ev(reviewed(r))["fails"]


def test_dig_s_still_rejects_something(ev):
    """The point of the conditional rule: DIG(S) must not become unable to reject."""
    r = record(); r["dig"]["S"]["op"] = "Substituted"
    r["dig"]["S"]["substitutionTest"] = {"pass": False, "reason": "x"}
    assert not ev(reviewed(r))["ok"]


def test_dig_s_transformed_passes(ev):
    r = record(); r["dig"]["S"]["op"] = "Transformed"
    assert "DIG(S)" not in ev(reviewed(r))["fails"]


@pytest.mark.parametrize("field,key", [("MC", "MC"), ("CTa", "CTa")])
def test_minimum_of_one(ev, field, key):
    """MC and CT-a carry a hard minimum. LOM does not: see test_core_eligibility_does_not_read_LOM."""
    r = record(); r["dimensions"][field]["level"] = 0
    assert key in ev(reviewed(r))["fails"]


def test_hybrid_lost_on_screen_but_survives_elsewhere(ev):
    r = record()
    r["dig"]["S"]["op"] = "Substituted"                    # untested -> does not survive
    r["dig"]["M"]["op"] = "Transformed"
    e = ev(reviewed(r))
    assert e["hybrid"] and not e["ok"]


def test_not_hybrid_when_the_substitution_test_passes(ev):
    """If it survives on screen it is eligible, not a hybrid candidate."""
    r = record()
    r["dig"]["S"]["op"] = "Substituted"
    r["dig"]["S"]["substitutionTest"] = {"pass": True, "reason": "transfers"}
    r["dig"]["M"]["op"] = "Transformed"
    e = ev(reviewed(r))
    assert e["ok"] and not e["hybrid"]


def test_not_hybrid_when_lost_everywhere(ev):
    r = record()
    for k in "SML": r["dig"][k]["op"] = "Removed"
    assert not ev(reviewed(r))["hybrid"]


def test_no_compensation_between_criteria(ev):
    """The rule is non-compensatory: a maximum elsewhere cannot rescue a failed minimum."""
    r = record(dimensions={"MC": {"level": 2, "descriptor": "", "evidence": ""},
                           "LOM": {"level": 2, "objective": "", "counterfactual": "", "evidence": ""},
                           "AGE": {"level": 2, "competence": "", "parameter": "", "evidence": ""},
                           "CTa": {"level": 0, "evidence": ""},
                           "CTb": {"level": 2, "note": ""}, "CTc": {"level": 2, "note": ""}})
    assert not ev(reviewed(r))["ok"]        # CT-a 0 is not rescued by maxima elsewhere


def test_no_total_score_anywhere():
    """Design rule 1: nothing in the evaluation may produce a total or a ranking."""
    import inspect, backend.evaluate as E
    src = inspect.getsource(E).lower()
    for banned in ("total =", "score =", "weight", "rank"):
        assert banned not in src, f"{banned!r} appeared in evaluate.py"


def test_hybrid_flag_ignores_L_after_v03():
    """L was removed from the gate and from the hybrid flag.

    Under a co-located setup the physical game is played unchanged, so L cannot show that a game
    survives digitisation -- it could not discriminate. A game lost on screen and lost under motion
    is therefore NOT a hybrid candidate, however well it survives co-located.
    """
    import backend.evaluate as E
    r = record()
    r["review"] = {"sections": dict(ALL_REVIEWED)}
    r["dig"]["S"] = {"op": "Removed"}
    r["dig"]["M"] = {"op": "Removed"}
    r["dig"]["L"] = {"op": "Kept"}
    assert E.eligible(r)["hybrid"] is False, "L must not carry a hybrid flag"
    r["dig"]["M"] = {"op": "Transformed"}
    assert E.eligible(r)["hybrid"] is True, "M still carries the hybrid flag"


def test_v03_changes_no_verdict_on_the_pilot_pool():
    """Checked against the released pool: every G3 pass is already carried by
    S or M, so dropping L changes no hybrid flag and no eligibility verdict on the nine games."""
    import json
    import backend.evaluate as E
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    from conftest import requires_records
    requires_records(root / "analysis" / "pool.json")
    pool = json.loads((root / "analysis" / "pool.json").read_text(encoding="utf-8"))
    assert len(pool) == 9
    for r in pool:
        e = E.eligible(r)
        with_L = (not E.survives(r["dig"]["S"])) and (E.survives(r["dig"]["M"]) or E.survives(r["dig"]["L"]))
        assert e["hybrid"] == with_L, f"{r['name']}: dropping L changed the hybrid flag"


def test_printed_algorithm_matches_the_code():
    """The Algorithm 1 block in PIPELINE must state the rule the code runs.

    It drifted twice without anyone noticing: it kept `ord(S) < 2` after v0.2 made Substituted
    conditional on the substitution test, and kept `ord(L) >= 2` in the hybrid flag after v0.3
    removed L. On the pilot pool the first made Araya Aldırmaca ineligible AND a hybrid candidate
    in print, while the code and the papers' prose call it eligible. This test reads the .tex.
    """
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parent.parent / "analysis" / "paper2.tex"
    if not src.exists():
        pytest.skip("the PIPELINE source (paper2.tex) is not in this checkout")
    tex = src.read_text(encoding="utf-8")
    i = tex.index(r"\caption{Eligibility and hybrid flag")
    block = tex[i:tex.index(r"\end{algorithmic}", i)]
    assert "survives" in block.lower(), "Algorithm 1 no longer names the survives predicate"
    assert r"\mathrm{ord}(r.\mathrm{S}) < 2" not in block, \
        "Algorithm 1 tests the raw S ordinal; v0.2 made Substituted conditional on the test"
    assert r"r.\mathrm{L}" not in block, \
        "Algorithm 1 still reads L; v0.3 removed it from the gate and the hybrid flag"
    assert "substitutionTest" in block, "Algorithm 1 does not say when the substitution test is read"


def test_g3_reads_the_same_survival_predicate_as_eligibility():
    """v0.4: G3 and the eligibility rule must agree about what 'survives' means.

    Before v0.4 the G3 descriptor admitted only Kept or Transformed while `survives()` also
    admitted Substituted-with-a-passing-test. On the pilot pool that split Araya Aldırmaca: the
    descriptor failed its gate, the code passed it, and the papers called it eligible. A game
    cannot be eligible on a class that does not carry the gate.
    """
    import backend.evaluate as E
    passing_sub = {"op": "Substituted", "substitutionTest": {"pass": True}}
    failing_sub = {"op": "Substituted", "substitutionTest": {"pass": False}}
    assert E.survives(passing_sub) is True
    assert E.survives(failing_sub) is False
    # G3 is "survives under S or M"; L carries neither the gate nor the flag from v0.3
    g3 = lambda r: E.survives(r["dig"]["S"]) or E.survives(r["dig"]["M"])
    r = record(); r["dig"] = {"S": passing_sub, "M": {"op": "Removed"}, "L": {"op": "Kept"}}
    assert g3(r) is True, "a passing substitution test on S must carry G3"
    r["dig"] = {"S": failing_sub, "M": {"op": "Substituted"}, "L": {"op": "Kept"}}
    assert g3(r) is False, "L must not carry G3"


def test_core_eligibility_does_not_read_LOM():
    """LOM is conditional on a stated educational objective, so it cannot gate the core decision.

    SELECTION states this from the 23 September revision: core eligibility is gates + MC + CT-a +
    survival; LOM >= 1 is an additional pedagogical filter where an objective exists. On the nine
    pilot games the change moves no verdict, because neither coder rated any game below 1.
    """
    import backend.evaluate as E
    r = record()
    r["review"] = {"sections": dict(ALL_REVIEWED)}
    r["dimensions"]["LOM"]["level"] = 0
    out = E.eligible(r)
    assert "LOM" not in out["fails"], "core eligibility must not read LOM"
    assert out["ok"] is True, "a game failing only LOM is still core-eligible"
    assert "LOM" in out["educationalFails"], "the educational filter must still read LOM"
    assert out["educationalOk"] is False
    r["dimensions"]["LOM"]["level"] = 1
    assert E.eligible(r)["educationalOk"] is True
